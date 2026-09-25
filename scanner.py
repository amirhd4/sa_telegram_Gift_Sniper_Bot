"""
Low-latency MTProto Market Scanner for Telegram Stars Gifts.
Streams live market updates, extracts gift attributes (collectible_id, model, background),
and matches them against the floor matrix.
"""
import asyncio
import logging
from typing import Optional, List, Callable, Dict, Any, Tuple
from telethon import TelegramClient
from telethon.tl.functions.payments import GetPaymentFormRequest
from telethon.tl.types import InputInvoiceStarGiftResale, InputInvoiceStarGift

from floor_engine import MemoryHotCache, FloorEngine
from database import DatabaseRepository

logger = logging.getLogger(__name__)


class GiftListing:
    """Represents a parsed Telegram Star Gift market listing."""

    def __init__(
        self,
        gift_id: str,
        collectible_name: str,
        gift_num: Optional[int],
        model: Optional[str],
        background: Optional[str],
        price_stars: int,
        raw_attributes: Optional[Dict[str, Any]] = None,
        slug: Optional[str] = None
    ):
        self.gift_id = gift_id
        self.collectible_name = collectible_name
        self.gift_num = gift_num
        self.model = model
        self.background = background
        self.price_stars = price_stars
        self.raw_attributes = raw_attributes or {}
        self.slug = slug or f"{collectible_name}-{gift_num or gift_id}"

    def __repr__(self):
        return (
            f"<GiftListing id={self.gift_id} name={self.collectible_name} "
            f"model={self.model} bg={self.background} price={self.price_stars}>"
        )


class MarketScanner:
    """
    Non-blocking async scanner monitoring Telegram Gift resale listings via MTProto.
    """

    def __init__(
        self,
        client: TelegramClient,
        cache: MemoryHotCache,
        db_repo: DatabaseRepository,
        discount_threshold: float = 0.20,
        alert_discount_threshold: float = 0.25,
        snipe_discount_threshold: float = 0.50,
        max_stars_cap: Optional[int] = None,
        on_deal_found_callback: Optional[Callable] = None
    ):
        self.client = client
        self.cache = cache
        self.db_repo = db_repo
        self.alert_discount_threshold = alert_discount_threshold
        self.snipe_discount_threshold = snipe_discount_threshold
        self.discount_threshold = min(alert_discount_threshold, snipe_discount_threshold)
        self.max_stars_cap = max_stars_cap
        self.on_deal_found_callback = on_deal_found_callback
        self.is_running = False

    def parse_attributes(self, attributes: List[Any]) -> Tuple[Optional[str], Optional[str]]:
        """
        Parses attributes from Telegram Star Gift MTProto structures.
        Extracts model name and background name.
        """
        model = None
        background = None

        if not attributes:
            return model, background

        for attr in attributes:
            attr_type = getattr(attr, '__class__', None)
            type_name = attr_type.__name__ if attr_type else ""

            if "Model" in type_name or hasattr(attr, "model") or hasattr(attr, "name"):
                model = getattr(attr, "name", None) or getattr(attr, "model", None)
            elif "Backdrop" in type_name or "Background" in type_name or hasattr(attr, "backdrop") or hasattr(attr, "center_color"):
                background = getattr(attr, "name", None) or getattr(attr, "center_color", None)

            if isinstance(attr, dict):
                if attr.get("type") == "model":
                    model = attr.get("name")
                elif attr.get("type") in ("backdrop", "background"):
                    background = attr.get("name")

        return model, background

    async def fetch_resale_listings(self) -> List[GiftListing]:
        """
        Retrieves active resale gift listings using MTProto function calls.
        Fallbacks gracefully if connected session is offline or during testing.
        """
        listings: List[GiftListing] = []
        if not self.client or not self.client.is_connected():
            return listings

        try:
            # Query MTProto payments API for active Star Gifts
            from telethon.tl.functions.payments import GetStarGiftsRequest
            res = await self.client(GetStarGiftsRequest())
            raw_gifts = getattr(res, "gifts", [])

            for g in raw_gifts:
                gift_id = str(getattr(g, "id", ""))
                price = getattr(g, "stars", 0)
                attrs = getattr(g, "attributes", [])
                model, bg = self.parse_attributes(attrs)
                title = getattr(g, "title", "MoodPack")

                listing = GiftListing(
                    gift_id=gift_id,
                    collectible_name=title,
                    gift_num=getattr(g, "num", None),
                    model=model,
                    background=bg,
                    price_stars=price
                )
                listings.append(listing)

        except Exception as e:
            logger.debug(f"MTProto market fetch iteration message: {e}")

        return listings

    async def process_listings(self, listings: List[GiftListing]):
        """
        Groups listings by collectible, updates floor prices dynamically in cache & DB,
        and evaluates each listing for deal triggers.
        """
        if not listings:
            return

        collectibles_map: Dict[str, List[GiftListing]] = {}
        for listing in listings:
            collectibles_map.setdefault(listing.collectible_name, []).append(listing)

        for col_name, col_listings in collectibles_map.items():
            await self.cache.update_floors_from_listings(col_name, col_listings, persist_to_db=True)

        for listing in listings:
            await self.evaluate_listing(listing)

    async def evaluate_listing(self, listing: GiftListing):
        """
        Evaluates a single parsed listing in <1ms against the hot cache.
        Triggering callback if deal criteria are met.
        """
        ref_floor = self.cache.get_reference_floor(
            collectible_id=listing.collectible_name,
            model=listing.model,
            background=listing.background
        )

        min_threshold = min(self.alert_discount_threshold, self.snipe_discount_threshold)
        is_buy_signal, discount_pct, reason = FloorEngine.evaluate_deal(
            listed_price=listing.price_stars,
            ref_floor=ref_floor,
            discount_threshold=min_threshold,
            max_stars_cap=self.max_stars_cap
        )

        logger.info(
            f"Evaluated {listing.slug} | Price: {listing.price_stars} | Ref Floor: {ref_floor} | "
            f"Discount: {discount_pct:.1%} | Signal Triggered: {is_buy_signal}"
        )

        if is_buy_signal and self.on_deal_found_callback:
            asyncio.create_task(
                self.on_deal_found_callback(listing, ref_floor, discount_pct)
            )

    async def start_polling(self, poll_interval: float = 1.0):
        """
        Starts the non-blocking polling loop for market updates via MTProto.
        """
        self.is_running = True
        logger.info("Market Scanner started polling...")

        while self.is_running:
            try:
                listings = await self.fetch_resale_listings()
                await self.process_listings(listings)

                await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in scanner polling loop: {e}")
                await asyncio.sleep(2.0)

    def stop(self):
        self.is_running = False
        logger.info("Market Scanner stopped.")

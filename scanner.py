"""
Low-latency MTProto Market Scanner for Telegram Stars Gifts.
Streams live resale market updates using GetResaleStarGiftsRequest,
extracts gift attributes (collectible_id, model, background),
and matches them against the floor matrix.
"""
import asyncio
import logging
from typing import Optional, List, Callable, Dict, Any, Tuple
from telethon import TelegramClient
from telethon.tl.functions.payments import GetStarGiftsRequest, GetResaleStarGiftsRequest
from telethon.tl.types import StarGift, StarGiftUnique
from telethon.errors import (
    UserDeactivatedError,
    UserDeactivatedBanError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    AuthKeyInvalidError,
    FloodWaitError,
    RPCError
)

from floor_engine import MemoryHotCache, FloorEngine
from database import DatabaseRepository

logger = logging.getLogger(__name__)


class GiftListing:
    """Represents a parsed Telegram Star Gift market listing."""

    def __init__(
        self,
        gift_id: str,
        collectible_name: str,
        gift_num: Optional[int] = None,
        model: Optional[str] = None,
        background: Optional[str] = None,
        price_stars: int = 0,
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
        self.slug = slug or (f"{collectible_name}-{gift_num}" if gift_num else f"{collectible_name}-{gift_id}")

    def __repr__(self):
        return (
            f"<GiftListing id={self.gift_id} slug={self.slug} name={self.collectible_name} "
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
        target_gift_ids: Optional[List[int]] = None,
        discount_threshold: float = 0.20,
        alert_discount_threshold: float = 0.25,
        snipe_discount_threshold: float = 0.50,
        max_stars_cap: Optional[int] = None,
        on_deal_found_callback: Optional[Callable] = None
    ):
        self.client = client
        self.cache = cache
        self.db_repo = db_repo
        self.target_gift_ids = target_gift_ids or []
        self.alert_discount_threshold = alert_discount_threshold
        self.snipe_discount_threshold = snipe_discount_threshold
        self.discount_threshold = min(alert_discount_threshold, snipe_discount_threshold)
        self.max_stars_cap = max_stars_cap
        self.on_deal_found_callback = on_deal_found_callback
        self.is_running = False

    def parse_attributes(self, attributes: List[Any]) -> Tuple[Optional[str], Optional[str]]:
        """
        Parses attributes from Telegram Star Gift MTProto structures.
        Extracts model name and background (backdrop) name.
        """
        model = None
        background = None

        if not attributes:
            return model, background

        for attr in attributes:
            attr_type = getattr(attr, '__class__', None)
            type_name = attr_type.__name__ if attr_type else ""

            # Extract Model Name
            if "Model" in type_name or hasattr(attr, "model"):
                model = getattr(attr, "name", None) or getattr(attr, "model", None)
            # Extract Backdrop / Background Name
            if "Backdrop" in type_name or "Background" in type_name or hasattr(attr, "backdrop") or hasattr(attr, "center_color"):
                background = getattr(attr, "name", None) or getattr(attr, "center_color", None)

            if isinstance(attr, dict):
                if attr.get("type") == "model":
                    model = attr.get("name")
                elif attr.get("type") in ("backdrop", "background"):
                    background = attr.get("name")

        return model, background

    async def fetch_resale_listings(self) -> List[GiftListing]:
        """
        Retrieves active resale gift listings using MTProto GetResaleStarGiftsRequest.
        Handles account ban/deactivation and FloodWait exceptions safely.
        """
        listings: List[GiftListing] = []
        if not self.client or not self.client.is_connected():
            logger.debug("[SCANNER] Client disconnected or not available for fetching resale listings.")
            return listings

        # If target gift IDs not set, fetch catalog first to discover base gift IDs
        gift_ids_to_scan = self.target_gift_ids
        if not gift_ids_to_scan:
            try:
                catalog_res = await self.client(GetStarGiftsRequest(hash=0))
                catalog_gifts = getattr(catalog_res, "gifts", [])
                gift_ids_to_scan = [getattr(g, "id", None) for g in catalog_gifts if getattr(g, "id", None)]
            except (UserDeactivatedError, UserDeactivatedBanError, AuthKeyUnregisteredError, SessionRevokedError, AuthKeyInvalidError) as acc_err:
                logger.critical(f"[ACCOUNT_STATUS] 🛑 CRITICAL: Scanner account error/ban detected: {acc_err}")
                self.stop()
                return listings
            except Exception as e:
                logger.warning(f"[SCANNER] Failed to fetch catalog base gifts: {e}")
                return listings

        for base_gift_id in gift_ids_to_scan:
            try:
                resale_res = await self.client(GetResaleStarGiftsRequest(
                    gift_id=base_gift_id,
                    sort_by_price=True,
                    stars_only=True,
                    attributes_hash=0,
                    offset="",
                    limit=50
                ))
                raw_gifts = getattr(resale_res, "gifts", [])
                logger.info(f"[SCANNER] Fetched {len(raw_gifts)} resale listings for base gift_id={base_gift_id}")

                for g in raw_gifts:
                    gift_id = str(getattr(g, "id", ""))
                    price = getattr(g, "stars", 0)
                    attrs = getattr(g, "attributes", [])
                    model, bg = self.parse_attributes(attrs)
                    title = getattr(g, "title", f"Gift_{base_gift_id}")
                    gift_num = getattr(g, "num", None)
                    slug = getattr(g, "slug", None) or (f"{title}-{gift_num}" if gift_num else f"{title}-{gift_id}")

                    listing = GiftListing(
                        gift_id=gift_id,
                        collectible_name=title,
                        gift_num=gift_num,
                        model=model,
                        background=bg,
                        price_stars=price,
                        slug=slug
                    )
                    listings.append(listing)

            except FloodWaitError as wait_err:
                logger.warning(f"[FLOOD_WAIT] [SCANNER] Rate limited for {wait_err.seconds}s on base gift_id={base_gift_id}")
                await asyncio.sleep(wait_err.seconds + 1)
            except (UserDeactivatedError, UserDeactivatedBanError, AuthKeyUnregisteredError, SessionRevokedError, AuthKeyInvalidError) as acc_err:
                logger.critical(f"[ACCOUNT_STATUS] 🛑 CRITICAL: Scanner account error/ban detected: {acc_err}")
                self.stop()
                break
            except RPCError as rpc_err:
                logger.error(f"[SCANNER] RPC Error on gift_id={base_gift_id}: {getattr(rpc_err, 'message', str(rpc_err))}")
            except Exception as e:
                logger.warning(f"[SCANNER] Error fetching resale gifts for {base_gift_id}: {e}")

        return listings

    async def process_listings(self, listings: List[GiftListing]):
        """
        Groups listings by collectible, updates floor prices dynamically in cache & DB,
        and evaluates each listing for deal triggers.
        """
        if not listings:
            logger.debug("[SCANNER] No resale listings retrieved in this iteration.")
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
            f"[EVALUATION] {listing.slug} | Price: {listing.price_stars} Stars | Ref Floor: {ref_floor:.1f} | "
            f"Discount: {discount_pct:.1%} | Buy Signal: {is_buy_signal} | Reason: {reason}"
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
        logger.info("[SCANNER] Market Scanner started polling...")

        while self.is_running:
            try:
                listings = await self.fetch_resale_listings()
                await self.process_listings(listings)

                await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[SCANNER] Error in scanner polling loop: {e}")
                await asyncio.sleep(2.0)

    def stop(self):
        self.is_running = False
        logger.info("[SCANNER] Market Scanner stopped.")

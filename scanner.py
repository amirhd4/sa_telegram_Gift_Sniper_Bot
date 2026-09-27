import asyncio
import time
import logging
from typing import Optional, List, Callable, Dict, Any, Tuple

from telethon import TelegramClient
from telethon.tl.functions.payments import (
    GetStarGiftsRequest,
    GetResaleStarGiftsRequest,
)
from telethon.errors import (
    UserDeactivatedError,
    UserDeactivatedBanError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    AuthKeyInvalidError,
    FloodWaitError,
    RPCError,
)

from floor_engine import MemoryHotCache, FloorEngine
from database import DatabaseRepository


logger = logging.getLogger(__name__)


class GiftListing:
    def __init__(
        self,
        gift_id: str,
        collectible_name: str,
        gift_num: Optional[int] = None,
        model: Optional[str] = None,
        background: Optional[str] = None,
        price_stars: int = 0,
        raw_attributes: Optional[Dict[str, Any]] = None,
        slug: Optional[str] = None,
    ):
        self.gift_id = gift_id
        self.collectible_name = collectible_name
        self.gift_num = gift_num
        self.model = model
        self.background = background
        self.price_stars = price_stars
        self.raw_attributes = raw_attributes or {}

        self.slug = slug or (
            f"{collectible_name}-{gift_num}"
            if gift_num is not None
            else f"{collectible_name}-{gift_id}"
        )

    def __repr__(self):
        return (
            f"<GiftListing id={self.gift_id} "
            f"slug={self.slug} "
            f"name={self.collectible_name} "
            f"model={self.model} "
            f"bg={self.background} "
            f"price={self.price_stars}>"
        )


class MarketScanner:

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

        on_deal_found_callback: Optional[Callable] = None,

        # Catalog refresh
        catalog_refresh_interval: float = 600.0,

        # Network safety
        request_timeout: float = 15.0,

        # Main polling
        heartbeat_interval: float = 30.0,

        # Resale pagination
        resale_page_limit: int = 50,
        max_pages_per_gift: int = 2,

        # Small delay between individual gift requests
        request_delay: float = 0.05,
    ):
        self.client = client
        self.cache = cache
        self.db_repo = db_repo

        self.target_gift_ids = list(dict.fromkeys(target_gift_ids or []))

        self.alert_discount_threshold = alert_discount_threshold
        self.snipe_discount_threshold = snipe_discount_threshold

        self.discount_threshold = min(
            alert_discount_threshold,
            snipe_discount_threshold,
        )

        self.max_stars_cap = max_stars_cap
        self.on_deal_found_callback = on_deal_found_callback

        self.catalog_refresh_interval = catalog_refresh_interval
        self.request_timeout = request_timeout
        self.heartbeat_interval = heartbeat_interval

        self.resale_page_limit = resale_page_limit
        self.max_pages_per_gift = max_pages_per_gift
        self.request_delay = request_delay

        self.is_running = False
        self._stop_requested = False

        # Auto-discovered base Gift IDs
        self._discovered_gift_ids: List[int] = []

        # Last catalog refresh & retry timing
        self._last_catalog_fetch_time = 0.0
        self._last_catalog_retry_time = 0.0
        self.catalog_retry_interval = 5.0  # Seconds to wait before retrying empty catalog

        # Used for resale attributes caching
        self._attributes_hash: Dict[int, int] = {}

        self._cycle_count = 0
        self._last_heartbeat_time = 0.0

    # ---------------------------------------------------------
    # ATTRIBUTE PARSER
    # ---------------------------------------------------------

    def parse_attributes(
        self,
        attributes: List[Any],
    ) -> Tuple[Optional[str], Optional[str]]:

        model = None
        background = None

        if not attributes:
            return None, None

        for attr in attributes:

            type_name = type(attr).__name__

            # Model
            if (
                "Model" in type_name
                or hasattr(attr, "model")
            ):
                model = (
                    getattr(attr, "name", None)
                    or getattr(attr, "model", None)
                    or model
                )

            # Backdrop / Background
            if (
                "Backdrop" in type_name
                or "Background" in type_name
                or hasattr(attr, "backdrop")
                or hasattr(attr, "center_color")
            ):
                background = (
                    getattr(attr, "name", None)
                    or getattr(attr, "backdrop", None)
                    or getattr(attr, "center_color", None)
                    or background
                )

            # Defensive support for dict-like data
            if isinstance(attr, dict):

                attr_type = attr.get("type")

                if attr_type == "model":
                    model = attr.get("name")

                elif attr_type in ("backdrop", "background"):
                    background = attr.get("name")

        return model, background

    # ---------------------------------------------------------
    # CATALOG
    # ---------------------------------------------------------

    async def fetch_catalog_gift_ids(self) -> List[int]:

        # Explicit IDs in .env/config
        if self.target_gift_ids:
            return self.target_gift_ids

        now = time.time()

        # If we already have discovered gift IDs and refresh interval hasn't elapsed, return cache.
        if self._discovered_gift_ids and (now - self._last_catalog_fetch_time < self.catalog_refresh_interval):
            return self._discovered_gift_ids

        # If previous fetch returned 0 gifts, enforce a short retry interval (5s) instead of waiting 10 minutes.
        if not self._discovered_gift_ids and (now - self._last_catalog_retry_time < self.catalog_retry_interval):
            return self._discovered_gift_ids

        self._last_catalog_retry_time = now

        try:
            logger.info("[SCANNER] Refreshing Telegram Gift catalog...")

            result = await asyncio.wait_for(
                self.client(GetStarGiftsRequest(hash=0)),
                timeout=self.request_timeout,
            )
            logger.info(
                "[SCANNER] Catalog response type=%s | repr=%r",
                type(result).__name__,
                result,
            )

            gifts = getattr(result, "gifts", None) or []

            discovered = []
            for g in gifts:
                # Safely extract base gift ID checking both StarGift (g.id) and StarGiftUnique (g.gift_id)
                gift_id_val = getattr(g, "gift_id", None)
                if not isinstance(gift_id_val, (int, str)):
                    gift_id_val = getattr(g, "id", None)

                if isinstance(gift_id_val, (int, str)):
                    try:
                        discovered.append(int(gift_id_val))
                    except (ValueError, TypeError):
                        pass

            # If primary store returned 0 gifts (e.g. primary sales sold out), check saved gifts in profile
            if not discovered:
                try:
                    logger.info("[SCANNER] Primary store empty. Checking account Saved Star Gifts...")
                    from telethon.tl.functions.payments import GetSavedStarGiftsRequest
                    from telethon.tl.types import InputPeerSelf

                    saved_result = await asyncio.wait_for(
                        self.client(GetSavedStarGiftsRequest(peer=InputPeerSelf(), limit=100)),
                        timeout=self.request_timeout,
                    )
                    saved_gifts = getattr(saved_result, "gifts", None) or []
                    for sg in saved_gifts:
                        g = getattr(sg, "gift", None)
                        if g:
                            gift_id_val = getattr(g, "gift_id", None)
                            if not isinstance(gift_id_val, (int, str)):
                                gift_id_val = getattr(g, "id", None)
                            if isinstance(gift_id_val, (int, str)):
                                try:
                                    discovered.append(int(gift_id_val))
                                except (ValueError, TypeError):
                                    pass
                except Exception as exc:
                    logger.debug("[SCANNER] Saved gifts query check failed: %s", exc)

            if discovered:
                self._discovered_gift_ids = list(dict.fromkeys(discovered))
                self._last_catalog_fetch_time = now
                logger.info(
                    "[SCANNER] Successfully discovered %d Gift IDs: %s",
                    len(self._discovered_gift_ids),
                    self._discovered_gift_ids,
                )
            else:
                logger.warning(
                    "[SCANNER] GetStarGiftsRequest & SavedGifts returned 0 gifts (Telegram primary store has no active sales). Will retry in %.1fs.",
                    self.catalog_retry_interval,
                )

        except asyncio.TimeoutError:

            logger.warning(
                "[SCANNER] Catalog request timeout."
            )

        except (
            UserDeactivatedError,
            UserDeactivatedBanError,
            AuthKeyUnregisteredError,
            SessionRevokedError,
            AuthKeyInvalidError,
        ) as exc:

            logger.critical(
                "[ACCOUNT_STATUS] Telegram account/session error: %s",
                exc,
            )

            self.stop()

        except FloodWaitError as exc:

            logger.warning(
                "[FLOOD_WAIT] Catalog request requires %ss wait.",
                exc.seconds,
            )

            # Do NOT immediately hammer the API again.
            await asyncio.sleep(
                min(exc.seconds + 1, 60)
            )

        except RPCError as exc:

            logger.error(
                "[SCANNER] Catalog RPC error: %s",
                exc,
            )

        except Exception:

            logger.exception(
                "[SCANNER] Unexpected catalog error."
            )

        return self._discovered_gift_ids

    # ---------------------------------------------------------
    # RESALE FETCH
    # ---------------------------------------------------------

    async def fetch_resale_for_gift(
        self,
        base_gift_id: int,
    ) -> List[GiftListing]:

        listings: List[GiftListing] = []

        offset = ""

        attributes_hash = self._attributes_hash.get(
            base_gift_id,
            0,
        )

        for page in range(
            1,
            self.max_pages_per_gift + 1,
        ):

            try:

                kwargs = dict(
                    gift_id=base_gift_id,
                    sort_by_price=True,
                    stars_only=True,
                    offset=offset,
                    limit=self.resale_page_limit,
                )

                # Use hash only when we already have one.
                if attributes_hash:
                    kwargs["attributes_hash"] = attributes_hash
                else:
                    kwargs["attributes_hash"] = 0

                result = await asyncio.wait_for(
                    self.client(
                        GetResaleStarGiftsRequest(
                            **kwargs
                        )
                    ),
                    timeout=self.request_timeout,
                )

                raw_gifts = getattr(
                    result,
                    "gifts",
                    []
                ) or []

                # Save Telegram's latest attributes hash.
                returned_hash = getattr(
                    result,
                    "attributes_hash",
                    None,
                )

                if returned_hash is not None:

                    self._attributes_hash[
                        base_gift_id
                    ] = int(returned_hash)

                logger.debug(
                    "[SCANNER] Gift %s page %s: %s listings",
                    base_gift_id,
                    page,
                    len(raw_gifts),
                )

                for gift in raw_gifts:

                    gift_id = str(
                        getattr(gift, "id", "")
                    )

                    price = int(
                        getattr(gift, "stars", 0)
                        or 0
                    )

                    attrs = (
                        getattr(
                            gift,
                            "attributes",
                            []
                        )
                        or []
                    )

                    model, background = (
                        self.parse_attributes(attrs)
                    )

                    title = (
                        getattr(
                            gift,
                            "title",
                            None
                        )
                        or f"Gift_{base_gift_id}"
                    )

                    gift_num = getattr(
                        gift,
                        "num",
                        None
                    )

                    slug = (
                        getattr(
                            gift,
                            "slug",
                            None
                        )
                        or (
                            f"{title}-{gift_num}"
                            if gift_num is not None
                            else f"{title}-{gift_id}"
                        )
                    )

                    listings.append(
                        GiftListing(
                            gift_id=gift_id,
                            collectible_name=title,
                            gift_num=gift_num,
                            model=model,
                            background=background,
                            price_stars=price,
                            raw_attributes={
                                "base_gift_id": base_gift_id,
                                "raw": attrs,
                            },
                            slug=slug,
                        )
                    )

                # Pagination
                next_offset = getattr(
                    result,
                    "next_offset",
                    None
                )

                if not next_offset:
                    break

                # Prevent broken API responses from looping forever.
                if next_offset == offset:
                    logger.warning(
                        "[SCANNER] Same next_offset received "
                        "for Gift %s; stopping pagination.",
                        base_gift_id,
                    )
                    break

                offset = next_offset

                # Small delay between pages.
                if self.request_delay > 0:
                    await asyncio.sleep(
                        self.request_delay
                    )

            except asyncio.TimeoutError:

                logger.warning(
                    "[SCANNER] Timeout fetching resale "
                    "Gift %s page %s",
                    base_gift_id,
                    page,
                )

                break

            except FloodWaitError as exc:

                logger.warning(
                    "[FLOOD_WAIT] Gift %s requires %ss wait.",
                    base_gift_id,
                    exc.seconds,
                )

                # Stop this Gift instead of blocking
                # the entire scanner for a long period.
                break

            except (
                UserDeactivatedError,
                UserDeactivatedBanError,
                AuthKeyUnregisteredError,
                SessionRevokedError,
                AuthKeyInvalidError,
            ) as exc:

                logger.critical(
                    "[ACCOUNT_STATUS] Session/account error: %s",
                    exc,
                )

                self.stop()
                break

            except RPCError as exc:

                logger.error(
                    "[SCANNER] Gift %s RPC error: %s",
                    base_gift_id,
                    exc,
                )

                break

            except Exception:

                logger.exception(
                    "[SCANNER] Unexpected error "
                    "for Gift %s",
                    base_gift_id,
                )

                break

        return listings

    # ---------------------------------------------------------
    # ALL RESALE LISTINGS
    # ---------------------------------------------------------

    async def fetch_resale_listings(
        self,
    ) -> List[GiftListing]:

        listings: List[GiftListing] = []

        if (
            not self.client
            or not self.client.is_connected()
        ):
            return listings

        gift_ids = (
            await self.fetch_catalog_gift_ids()
        )

        if not gift_ids:
            return listings

        for gift_id in gift_ids:

            if self._stop_requested:
                break

            gift_listings = (
                await self.fetch_resale_for_gift(
                    gift_id
                )
            )

            listings.extend(
                gift_listings
            )

            # Prevent hammering Telegram.
            if self.request_delay > 0:
                await asyncio.sleep(
                    self.request_delay
                )

        return listings

    # ---------------------------------------------------------
    # PROCESS
    # ---------------------------------------------------------

    async def process_listings(
        self,
        listings: List[GiftListing],
    ):

        if not listings:
            return

        collectibles_map: Dict[
            str,
            List[GiftListing]
        ] = {}

        for listing in listings:

            collectibles_map.setdefault(
                listing.collectible_name,
                []
            ).append(listing)

        for (
            collectible_name,
            collectible_listings
        ) in collectibles_map.items():

            await self.cache.update_floors_from_listings(
                collectible_name,
                collectible_listings,
                persist_to_db=True,
            )

        for listing in listings:

            await self.evaluate_listing(
                listing
            )

    # ---------------------------------------------------------
    # EVALUATION
    # ---------------------------------------------------------

    async def evaluate_listing(
        self,
        listing: GiftListing,
    ):

        ref_floor = (
            self.cache.get_reference_floor(
                collectible_id=listing.collectible_name,
                model=listing.model,
                background=listing.background,
            )
        )

        if ref_floor is None:
            return

        min_threshold = min(
            self.alert_discount_threshold,
            self.snipe_discount_threshold,
        )

        (
            is_buy_signal,
            discount_pct,
            reason,
        ) = FloorEngine.evaluate_deal(
            listed_price=listing.price_stars,
            ref_floor=ref_floor,
            discount_threshold=min_threshold,
            max_stars_cap=self.max_stars_cap,
        )

        logger.info(
            "[EVALUATION] %s | "
            "Price=%s | "
            "Floor=%.1f | "
            "Discount=%.1f%% | "
            "Signal=%s | "
            "Reason=%s",
            listing.slug,
            listing.price_stars,
            ref_floor,
            discount_pct * 100,
            is_buy_signal,
            reason,
        )

        if (
            is_buy_signal
            and self.on_deal_found_callback
        ):

            asyncio.create_task(
                self.on_deal_found_callback(
                    listing,
                    ref_floor,
                    discount_pct,
                )
            )

    # ---------------------------------------------------------
    # POLLING
    # ---------------------------------------------------------

    async def start_polling(
        self,
        poll_interval: float = 3.0,
    ):

        self.is_running = True
        self._cycle_count = 0
        self._last_heartbeat_time = time.time()

        logger.info(
            "[SCANNER] Market scanner started."
        )

        gift_ids = (
            await self.fetch_catalog_gift_ids()
        )

        logger.info(
            "[SCANNER] Monitoring %d Gift types.",
            len(gift_ids),
        )

        while self.is_running and not self._stop_requested:

            try:

                self._cycle_count += 1

                started = time.monotonic()

                gift_ids = await self.fetch_catalog_gift_ids()

                now = time.time()

                if not gift_ids:
                    if now - self._last_heartbeat_time >= self.heartbeat_interval:
                        logger.warning(
                            "[SCANNER] Heartbeat | Cycle #%d | No Gift IDs available yet.",
                            self._cycle_count,
                        )
                        self._last_heartbeat_time = now

                    await asyncio.sleep(self.catalog_retry_interval)
                    continue

                listings = (
                    await self.fetch_resale_listings()
                )

                await self.process_listings(
                    listings
                )

                elapsed = (
                    time.monotonic() - started
                )

                now = time.time()

                if listings:

                    logger.info(
                        "[SCANNER] Cycle #%d | "
                        "%d listings | %.2fs",
                        self._cycle_count,
                        len(listings),
                        elapsed,
                    )

                elif (
                    now - self._last_heartbeat_time
                    >= self.heartbeat_interval
                ):

                    logger.info(
                        "[SCANNER] Heartbeat | "
                        "Cycle #%d | "
                        "Gifts=%d | "
                        "Listings=%d | "
                        "Elapsed=%.2fs",
                        self._cycle_count,
                        len(
                            self.target_gift_ids
                            or self._discovered_gift_ids
                        ),
                        len(listings),
                        elapsed,
                    )

                    self._last_heartbeat_time = now

                # Don't blindly sleep 1 second after
                # a cycle that itself took 10 seconds.
                sleep_for = max(
                    0.0,
                    poll_interval - elapsed
                )

                await asyncio.sleep(
                    sleep_for
                )

            except asyncio.CancelledError:

                logger.info(
                    "[SCANNER] Polling cancelled."
                )

                break

            except Exception:

                logger.exception(
                    "[SCANNER] Polling loop error."
                )

                await asyncio.sleep(2.0)

        self.is_running = False

    def stop(self):

        self.is_running = False
        self._stop_requested = True

        logger.info(
            "[SCANNER] Market scanner stopped."
        )
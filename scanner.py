import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional, List, Callable, Dict, Any, Tuple, Set

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


# ============================================================
# GIFT LISTING
# ============================================================

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
        base_gift_id: Optional[int] = None,
    ):
        self.gift_id = gift_id
        self.collectible_name = collectible_name
        self.gift_num = gift_num
        self.model = model
        self.background = background
        self.price_stars = price_stars
        self.raw_attributes = raw_attributes or {}
        self.base_gift_id = base_gift_id

        self.slug = slug or (
            f"{collectible_name}-{gift_num}"
            if gift_num is not None
            else f"{collectible_name}-{gift_id}"
        )

    def __repr__(self):

        return (
            f"<GiftListing "
            f"id={self.gift_id} "
            f"base={self.base_gift_id} "
            f"slug={self.slug} "
            f"name={self.collectible_name} "
            f"model={self.model} "
            f"bg={self.background} "
            f"price={self.price_stars}>"
        )


# ============================================================
# MARKET SCANNER
# ============================================================

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

        # ----------------------------------------------------
        # Catalog
        # ----------------------------------------------------

        catalog_refresh_interval: float = 600.0,

        # ----------------------------------------------------
        # Network
        # ----------------------------------------------------

        request_timeout: float = 15.0,

        # ----------------------------------------------------
        # Main polling
        # ----------------------------------------------------

        heartbeat_interval: float = 30.0,

        # ----------------------------------------------------
        # Resale pagination
        # ----------------------------------------------------

        resale_page_limit: int = 100,
        max_pages_per_gift: int = 3,

        # ----------------------------------------------------
        # Delay
        # ----------------------------------------------------

        request_delay: float = 0.05,

        # ----------------------------------------------------
        # Persistent Gift IDs
        # ----------------------------------------------------

        gift_ids_file: str = "data/gift_ids.json",

        # ----------------------------------------------------
        # Empty catalog retry
        # ----------------------------------------------------

        catalog_retry_interval: float = 30.0,

        # ----------------------------------------------------
        # Parallel Gift scanning
        # ----------------------------------------------------

        max_parallel_gifts: int = 4,
    ):

        self.client = client
        self.cache = cache
        self.db_repo = db_repo

        # ====================================================
        # CONFIG
        # ====================================================

        self.target_gift_ids = list(
            dict.fromkeys(
                target_gift_ids or []
            )
        )

        self.alert_discount_threshold = (
            alert_discount_threshold
        )

        self.snipe_discount_threshold = (
            snipe_discount_threshold
        )

        self.discount_threshold = min(
            alert_discount_threshold,
            snipe_discount_threshold,
        )

        self.max_stars_cap = max_stars_cap

        self.on_deal_found_callback = (
            on_deal_found_callback
        )

        # ====================================================
        # TIMING
        # ====================================================

        self.catalog_refresh_interval = (
            catalog_refresh_interval
        )

        self.request_timeout = (
            request_timeout
        )

        self.heartbeat_interval = (
            heartbeat_interval
        )

        self.catalog_retry_interval = (
            catalog_retry_interval
        )

        # ====================================================
        # RESALE
        # ====================================================

        self.resale_page_limit = (
            resale_page_limit
        )

        self.max_pages_per_gift = (
            max_pages_per_gift
        )

        self.request_delay = (
            request_delay
        )

        self.max_parallel_gifts = (
            max_parallel_gifts
        )

        # ====================================================
        # STATE
        # ====================================================

        self.is_running = False
        self._stop_requested = False

        # ----------------------------------------------------
        # Known base Gift IDs
        #
        # IMPORTANT:
        # These are NOT cleared when Telegram catalog
        # temporarily returns zero gifts.
        # ----------------------------------------------------

        self._discovered_gift_ids: List[int] = []

        # ----------------------------------------------------
        # Persistent storage
        # ----------------------------------------------------

        self._gift_ids_file = Path(
            gift_ids_file
        )

        # ----------------------------------------------------
        # Catalog timing
        # ----------------------------------------------------

        self._last_catalog_fetch_time = 0.0
        self._last_catalog_retry_time = 0.0

        # ----------------------------------------------------
        # Telegram attributes hash
        # ----------------------------------------------------

        self._attributes_hash: Dict[int, int] = {}

        # ----------------------------------------------------
        # Polling
        # ----------------------------------------------------

        self._cycle_count = 0
        self._last_heartbeat_time = 0.0

        # ----------------------------------------------------
        # Listing deduplication
        # ----------------------------------------------------

        self._seen_listings: Dict[str, float] = {}

        # ====================================================
        # LOAD PERSISTED IDs
        # ====================================================

        self._load_persisted_gift_ids()

    # ========================================================
    # PERSISTENT GIFT IDS
    # ========================================================

    def _load_persisted_gift_ids(self) -> None:

        ids: Set[int] = set()

        # ----------------------------------------------------
        # IDs from config
        # ----------------------------------------------------

        for gift_id in self.target_gift_ids:

            try:

                ids.add(
                    int(gift_id)
                )

            except (
                TypeError,
                ValueError,
            ):
                pass

        # ----------------------------------------------------
        # IDs from JSON
        # ----------------------------------------------------

        try:

            if self._gift_ids_file.exists():

                raw = json.loads(
                    self._gift_ids_file.read_text(
                        encoding="utf-8"
                    )
                )

                if isinstance(
                    raw,
                    list,
                ):

                    for gift_id in raw:

                        try:

                            ids.add(
                                int(gift_id)
                            )

                        except (
                            TypeError,
                            ValueError,
                        ):
                            pass

        except Exception:

            logger.exception(
                "[SCANNER] Failed loading persisted Gift IDs"
            )

        # ----------------------------------------------------
        # Default seed for app if file does not exist
        # ----------------------------------------------------

        if (
            not self._gift_ids_file.exists()
            and str(self._gift_ids_file) == "data/gift_ids.json"
            and not ids
        ):

            default_seeds = []


        self._discovered_gift_ids = sorted(
            ids
        )

        if (
            not self._gift_ids_file.exists()
            and str(self._gift_ids_file) == "data/gift_ids.json"
            and self._discovered_gift_ids
        ):

            self._persist_gift_ids()

        if self._discovered_gift_ids:

            logger.info(
                "[SCANNER] Loaded %d persisted Gift IDs: %s",
                len(
                    self._discovered_gift_ids
                ),
                self._discovered_gift_ids,
            )

    # ========================================================

    def _persist_gift_ids(self) -> None:

        try:

            self._gift_ids_file.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            self._gift_ids_file.write_text(
                json.dumps(
                    self._discovered_gift_ids,
                    indent=2,
                ),
                encoding="utf-8",
            )

        except Exception:

            logger.exception(
                "[SCANNER] Failed persisting Gift IDs"
            )

    # ========================================================
    # ATTRIBUTE PARSER
    # ========================================================

    @staticmethod
    def parse_attributes(
        attributes: List[Any],
    ) -> Tuple[
        Optional[str],
        Optional[str],
    ]:

        model = None
        background = None

        if not attributes:

            return None, None

        for attr in attributes:

            if isinstance(attr, dict):
                attr_type = str(attr.get("type", "")).lower()
                attr_name = attr.get("name")
                if attr_type == "model" or "model" in attr_type:
                    model = attr_name or model
                elif attr_type in ("backdrop", "background") or "backdrop" in attr_type or "background" in attr_type:
                    background = attr_name or background
                continue

            type_name = type(
                attr
            ).__name__

            # ------------------------------------------------
            # Model
            # ------------------------------------------------

            if type_name == "StarGiftAttributeModel":

                model = (
                    getattr(
                        attr,
                        "name",
                        None,
                    )
                    or model
                )

            # ------------------------------------------------
            # Background / Backdrop
            # ------------------------------------------------

            elif (
                type_name
                == "StarGiftAttributeBackdrop"
            ):

                background = (
                    getattr(
                        attr,
                        "name",
                        None,
                    )
                    or background
                )

            # ------------------------------------------------
            # Pattern
            # ------------------------------------------------

            elif (
                type_name
                == "StarGiftAttributePattern"
            ):

                continue

            # ------------------------------------------------
            # Defensive fallback
            # ------------------------------------------------

            else:

                name = getattr(
                    attr,
                    "name",
                    None,
                )

                if name:

                    if "Model" in type_name:

                        model = name

                    elif "Backdrop" in type_name:

                        background = name

        return (
            model,
            background,
        )

    # ========================================================
    # CATALOG
    # ========================================================

    async def refresh_catalog(
        self,
    ) -> List[int]:

        now = time.time()

        # ----------------------------------------------------
        # Explicit IDs always win.
        # ----------------------------------------------------

        if self.target_gift_ids:

            merged = set(
                self.target_gift_ids
            )

            merged.update(
                self._discovered_gift_ids
            )

            self._discovered_gift_ids = sorted(
                merged
            )

            return self._discovered_gift_ids

        # ----------------------------------------------------
        # Existing IDs + cache still valid
        # ----------------------------------------------------

        if (
            self._discovered_gift_ids
            and
            (
                now
                - self._last_catalog_fetch_time
                < self.catalog_refresh_interval
            )
        ):

            return (
                self._discovered_gift_ids
            )

        # ----------------------------------------------------
        # Avoid hammering catalog
        # ----------------------------------------------------

        if (
            now
            - self._last_catalog_retry_time
            < self.catalog_retry_interval
        ):

            return (
                self._discovered_gift_ids
            )

        self._last_catalog_retry_time = now

        try:

            logger.info(
                "[SCANNER] Refreshing Telegram Gift catalog..."
            )

            result = await asyncio.wait_for(
                self.client(
                    GetStarGiftsRequest(
                        hash=0
                    )
                ),
                timeout=self.request_timeout,
            )

            logger.info(
                "[DEBUG] StarGifts result type=%s",
                type(result).__name__,
            )

            logger.info(
                "[DEBUG] StarGifts result=%r",
                result,
            )

            logger.info(
                "[DEBUG] StarGifts gifts=%r",
                getattr(result, "gifts", None),
            )

            gifts = (
                getattr(
                    result,
                    "gifts",
                    None,
                )
                or []
            )

            logger.info(
                "[SCANNER] Catalog response type=%s | gifts=%d",
                type(result).__name__,
                len(gifts),
            )

            discovered = []

            for gift in gifts:

                gift_id = getattr(gift, "id", None)

                if not isinstance(gift_id, int):
                    continue

                if isinstance(
                    gift_id,
                    int,
                ):

                    discovered.append(
                        gift_id
                    )

                    logger.debug(
                        "[SCANNER] Catalog Gift "
                        "id=%s title=%s "
                        "sold_out=%s "
                        "availability_resale=%s "
                        "resell_min_stars=%s",
                        gift_id,
                        getattr(
                            gift,
                            "title",
                            None,
                        ),
                        getattr(
                            gift,
                            "sold_out",
                            False,
                        ),
                        getattr(
                            gift,
                            "availability_resale",
                            None,
                        ),
                        getattr(
                            gift,
                            "resell_min_stars",
                            None,
                        ),
                    )

            # =================================================
            # NEW / IMPORTANT
            #
            # Never clear old IDs when catalog is empty.
            # =================================================

            if discovered:

                merged = set(
                    self._discovered_gift_ids
                )

                merged.update(
                    discovered
                )

                self._discovered_gift_ids = sorted(
                    merged
                )

                self._last_catalog_fetch_time = (
                    now
                )

                self._persist_gift_ids()

                logger.info(
                    "[SCANNER] Discovered %d Gift IDs. "
                    "Total monitored=%d",
                    len(discovered),
                    len(
                        self._discovered_gift_ids
                    ),
                )

            else:

                logger.warning(
                    "[SCANNER] Telegram primary "
                    "catalog returned 0 gifts."
                )

                if self._discovered_gift_ids:

                    logger.info(
                        "[SCANNER] Keeping %d previously "
                        "known Gift IDs. "
                        "Continuing resale scan.",
                        len(
                            self._discovered_gift_ids
                        ),
                    )

                else:

                    logger.warning(
                        "[SCANNER] No known Gift IDs yet. "
                        "Initial discovery is required."
                    )

        # ====================================================
        # ERRORS
        # ====================================================

        except asyncio.TimeoutError:

            logger.warning(
                "[SCANNER] Catalog request timeout."
            )

        except FloodWaitError as exc:

            logger.warning(
                "[FLOOD_WAIT] Catalog requires %ss wait.",
                exc.seconds,
            )

        except (
            UserDeactivatedError,
            UserDeactivatedBanError,
            AuthKeyUnregisteredError,
            SessionRevokedError,
            AuthKeyInvalidError,
        ) as exc:

            logger.critical(
                "[ACCOUNT_STATUS] "
                "Telegram session/account error: %s",
                exc,
            )

            self.stop()

        except RPCError as exc:

            logger.error(
                "[SCANNER] Catalog RPC error: %s",
                exc,
            )

        except Exception:

            logger.exception(
                "[SCANNER] Unexpected catalog error."
            )

        return (
            self._discovered_gift_ids
        )

    # ========================================================
    # COMPATIBILITY
    # ========================================================

    async def fetch_catalog_gift_ids(
        self,
    ) -> List[int]:

        return await self.refresh_catalog()

    # ========================================================
    # PRICE EXTRACTION
    # ========================================================

    @staticmethod
    def _extract_resale_price(
        gift: Any,
    ) -> int:

        # ----------------------------------------------------
        # Modern Telegram:
        #
        # resell_amount = Vector<StarsAmount>
        # ----------------------------------------------------

        resell_amount = getattr(
            gift,
            "resell_amount",
            None,
        )

        if resell_amount:

            # Vector
            if isinstance(
                resell_amount,
                (
                    list,
                    tuple,
                ),
            ):

                for amount in resell_amount:

                    value = getattr(
                        amount,
                        "amount",
                        None,
                    )

                    if isinstance(
                        value,
                        int,
                    ):

                        return value

            # Single StarsAmount
            value = getattr(
                resell_amount,
                "amount",
                None,
            )

            if isinstance(
                value,
                int,
            ):

                return value

        # ----------------------------------------------------
        # Compatibility
        # ----------------------------------------------------

        value = getattr(
            gift,
            "stars",
            None,
        )

        if isinstance(
            value,
            int,
        ):

            return value

        value = getattr(
            gift,
            "resell_stars",
            None,
        )

        if isinstance(
            value,
            int,
        ):

            return value

        return 0

    # ========================================================
    # RESALE FETCH
    # ========================================================

    async def fetch_resale_for_gift(
        self,
        base_gift_id: int,
    ) -> List[GiftListing]:

        listings: List[
            GiftListing
        ] = []

        offset = ""

        attributes_hash = (
            self._attributes_hash.get(
                base_gift_id,
                0,
            )
        )

        for page in range(
            1,
            self.max_pages_per_gift + 1,
        ):

            if self._stop_requested:

                break

            try:

                kwargs = dict(
                    gift_id=int(
                        base_gift_id
                    ),
                    sort_by_price=True,
                    stars_only=True,
                    offset=offset,
                    limit=self.resale_page_limit,
                    attributes_hash=(
                        attributes_hash
                        if attributes_hash
                        else 0
                    ),
                )

                result = await asyncio.wait_for(
                    self.client(
                        GetResaleStarGiftsRequest(
                            **kwargs
                        )
                    ),
                    timeout=self.request_timeout,
                )

                raw_gifts = (
                    getattr(
                        result,
                        "gifts",
                        None,
                    )
                    or []
                )

                # ------------------------------------------------
                # Save attributes hash
                # ------------------------------------------------

                returned_hash = getattr(
                    result,
                    "attributes_hash",
                    None,
                )

                if returned_hash is not None:

                    self._attributes_hash[
                        base_gift_id
                    ] = int(
                        returned_hash
                    )

                logger.debug(
                    "[SCANNER] Resale "
                    "Gift=%s Page=%s "
                    "Listings=%d",
                    base_gift_id,
                    page,
                    len(
                        raw_gifts
                    ),
                )

                # =================================================
                # PARSE LISTINGS
                # =================================================

                for gift in raw_gifts:

                    unique_id = getattr(
                        gift,
                        "id",
                        None,
                    )

                    if unique_id is None:

                        continue

                    gift_id = str(
                        unique_id
                    )

                    title = (
                        getattr(
                            gift,
                            "title",
                            None,
                        )
                        or
                        f"Gift_{base_gift_id}"
                    )

                    gift_num = getattr(
                        gift,
                        "num",
                        None,
                    )

                    slug = getattr(
                        gift,
                        "slug",
                        None,
                    )

                    # ------------------------------------------------
                    # Price
                    # ------------------------------------------------

                    price = (
                        self._extract_resale_price(
                            gift
                        )
                    )

                    if price <= 0:

                        logger.debug(
                            "[SCANNER] "
                            "Skipping Gift %s: "
                            "invalid price",
                            gift_id,
                        )

                        continue

                    # ------------------------------------------------
                    # Attributes
                    # ------------------------------------------------

                    attrs = (
                        getattr(
                            gift,
                            "attributes",
                            None,
                        )
                        or []
                    )

                    model, background = (
                        self.parse_attributes(
                            attrs
                        )
                    )

                    listing = GiftListing(
                        gift_id=gift_id,
                        collectible_name=title,
                        gift_num=gift_num,
                        model=model,
                        background=background,
                        price_stars=price,
                        raw_attributes={
                            "base_gift_id": base_gift_id,
                            "raw": attrs,
                            "telegram_type": type(
                                gift
                            ).__name__,
                        },
                        slug=slug,
                        base_gift_id=base_gift_id,
                    )

                    listings.append(
                        listing
                    )

                # =================================================
                # PAGINATION
                # =================================================

                next_offset = getattr(
                    result,
                    "next_offset",
                    None,
                )

                if not next_offset:

                    break

                if next_offset == offset:

                    logger.warning(
                        "[SCANNER] Same next_offset "
                        "received for Gift %s. "
                        "Stopping pagination.",
                        base_gift_id,
                    )

                    break

                offset = next_offset

                if self.request_delay > 0:

                    await asyncio.sleep(
                        self.request_delay
                    )

            # ====================================================
            # ERRORS
            # ====================================================

            except asyncio.TimeoutError:

                logger.warning(
                    "[SCANNER] Timeout fetching "
                    "resale Gift %s page %s",
                    base_gift_id,
                    page,
                )

                break

            except FloodWaitError as exc:

                logger.warning(
                    "[FLOOD_WAIT] Resale Gift %s "
                    "requires %ss.",
                    base_gift_id,
                    exc.seconds,
                )

                # Do not block the entire scanner.
                break

            except (
                UserDeactivatedError,
                UserDeactivatedBanError,
                AuthKeyUnregisteredError,
                SessionRevokedError,
                AuthKeyInvalidError,
            ) as exc:

                logger.critical(
                    "[ACCOUNT_STATUS] "
                    "Telegram session error: %s",
                    exc,
                )

                self.stop()

                break

            except RPCError as exc:

                logger.error(
                    "[SCANNER] Gift %s "
                    "resale RPC error: %s",
                    base_gift_id,
                    exc,
                )

                break

            except Exception:

                logger.exception(
                    "[SCANNER] Unexpected resale "
                    "error for Gift %s",
                    base_gift_id,
                )

                break

        return listings

    # ========================================================
    # ALL RESALE LISTINGS
    # ========================================================

    async def fetch_resale_listings(
        self,
    ) -> List[GiftListing]:

        if (
            not self.client
            or not self.client.is_connected()
        ):

            return []

        gift_ids = (
            await self.fetch_catalog_gift_ids()
        )

        if not gift_ids:

            return []

        # ----------------------------------------------------
        # Limited concurrency
        # ----------------------------------------------------

        semaphore = asyncio.Semaphore(
            self.max_parallel_gifts
        )

        async def worker(
            gift_id: int,
        ):

            async with semaphore:

                result = (
                    await self.fetch_resale_for_gift(
                        gift_id
                    )
                )

                if self.request_delay > 0:

                    await asyncio.sleep(
                        self.request_delay
                    )

                return result

        tasks = [
            asyncio.create_task(
                worker(gift_id)
            )
            for gift_id in gift_ids
        ]

        results = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

        listings: List[
            GiftListing
        ] = []

        for result in results:

            if isinstance(
                result,
                Exception,
            ):

                logger.error(
                    "[SCANNER] "
                    "Resale worker failed: %s",
                    result,
                )

                continue

            listings.extend(
                result
            )

        return listings

    # ========================================================
    # PROCESS LISTINGS
    # ========================================================

    async def process_listings(
        self,
        listings: List[GiftListing],
    ):

        if not listings:

            return

        collectibles_map: Dict[
            str,
            List[GiftListing],
        ] = {}

        for listing in listings:

            collectibles_map.setdefault(
                listing.collectible_name,
                [],
            ).append(
                listing
            )

        # ----------------------------------------------------
        # UPDATE FLOORS FIRST
        # ----------------------------------------------------

        for (
            collectible_name,
            collectible_listings,
        ) in collectibles_map.items():

            await self.cache.update_floors_from_listings(
                collectible_name,
                collectible_listings,
                persist_to_db=True,
            )

        # ----------------------------------------------------
        # EVALUATE
        # ----------------------------------------------------

        for listing in listings:

            await self.evaluate_listing(
                listing
            )

    # ========================================================
    # EVALUATE LISTING
    # ========================================================

    async def evaluate_listing(
        self,
        listing: GiftListing,
    ):

        now = time.time()

        # ----------------------------------------------------
        # Prevent duplicate callback
        # ----------------------------------------------------

        previous = self._seen_listings.get(
            listing.gift_id
        )

        if (
            previous is not None
            and
            now - previous < 1.0
        ):

            return

        self._seen_listings[
            listing.gift_id
        ] = now

        # ----------------------------------------------------
        # Cleanup
        # ----------------------------------------------------

        if len(
            self._seen_listings
        ) > 100_000:

            cutoff = now - 60

            self._seen_listings = {
                key: timestamp
                for key, timestamp
                in self._seen_listings.items()
                if timestamp >= cutoff
            }

        # ====================================================
        # REFERENCE FLOOR
        # ====================================================

        ref_floor = (
            self.cache.get_reference_floor(
                collectible_id=(
                    listing.collectible_name
                ),
                model=listing.model,
                background=listing.background,
            )
        )

        if ref_floor is None:

            return

        if ref_floor <= 0:

            return

        # ====================================================
        # DISCOUNT
        # ====================================================

        (
            is_buy_signal,
            discount_pct,
            reason,
        ) = FloorEngine.evaluate_deal(
            listed_price=listing.price_stars,
            ref_floor=ref_floor,
            discount_threshold=(
                self.discount_threshold
            ),
            max_stars_cap=self.max_stars_cap,
        )

        logger.info(
            "[EVALUATION] %s | "
            "BaseGift=%s | "
            "Price=%s | "
            "Floor=%.1f | "
            "Discount=%.2f%% | "
            "Signal=%s | "
            "Reason=%s",
            listing.slug,
            listing.base_gift_id,
            listing.price_stars,
            ref_floor,
            discount_pct * 100,
            is_buy_signal,
            reason,
        )

        # ====================================================
        # CALLBACK
        # ====================================================

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

    # ========================================================
    # POLLING
    # ========================================================

    async def start_polling(
        self,
        poll_interval: float = 3.0,
    ):

        self.is_running = True
        self._stop_requested = False

        self._cycle_count = 0

        self._last_heartbeat_time = (
            time.time()
        )

        logger.info(
            "[SCANNER] Market scanner started."
        )

        # ----------------------------------------------------
        # Initial discovery
        # ----------------------------------------------------

        gift_ids = (
            await self.fetch_catalog_gift_ids()
        )

        logger.info(
            "[SCANNER] Monitoring %d Gift types.",
            len(gift_ids),
        )

        # ====================================================
        # MAIN LOOP
        # ====================================================

        while (
            self.is_running
            and not self._stop_requested
        ):

            try:

                self._cycle_count += 1

                started = (
                    time.monotonic()
                )

                # ------------------------------------------------
                # Refresh catalog
                #
                # IMPORTANT:
                # Empty catalog does NOT delete known IDs.
                # ------------------------------------------------

                gift_ids = (
                    await self.fetch_catalog_gift_ids()
                )

                # ------------------------------------------------
                # Still no IDs
                # ------------------------------------------------

                if not gift_ids:

                    now = time.time()

                    if (
                        now
                        - self._last_heartbeat_time
                        >= self.heartbeat_interval
                    ):

                        logger.warning(
                            "[SCANNER] Heartbeat | "
                            "Cycle #%d | "
                            "No known Gift IDs yet.",
                            self._cycle_count,
                        )

                        self._last_heartbeat_time = now

                    await asyncio.sleep(
                        min(
                            self.catalog_retry_interval,
                            poll_interval,
                        )
                    )

                    continue

                # =================================================
                # RESALE MARKET SCAN
                # =================================================

                listings = (
                    await self.fetch_resale_listings()
                )

                # =================================================
                # FLOOR + EVALUATION
                # =================================================

                await self.process_listings(
                    listings
                )

                elapsed = (
                    time.monotonic()
                    - started
                )

                now = time.time()

                # =================================================
                # LOG
                # =================================================

                if listings:

                    logger.info(
                        "[SCANNER] Cycle #%d | "
                        "GiftTypes=%d | "
                        "Listings=%d | "
                        "Elapsed=%.2fs",
                        self._cycle_count,
                        len(gift_ids),
                        len(listings),
                        elapsed,
                    )

                elif (
                    now
                    - self._last_heartbeat_time
                    >= self.heartbeat_interval
                ):

                    logger.info(
                        "[SCANNER] Heartbeat | "
                        "Cycle #%d | "
                        "Gifts=%d | "
                        "Listings=0 | "
                        "Elapsed=%.2fs",
                        self._cycle_count,
                        len(gift_ids),
                        elapsed,
                    )

                    self._last_heartbeat_time = now

                # =================================================
                # POLL DELAY
                # =================================================

                sleep_for = max(
                    0.0,
                    poll_interval
                    - elapsed,
                )

                await asyncio.sleep(
                    sleep_for
                )

            # ====================================================
            # CANCEL
            # ====================================================

            except asyncio.CancelledError:

                logger.info(
                    "[SCANNER] Polling cancelled."
                )

                break

            # ====================================================
            # UNEXPECTED ERROR
            # ====================================================

            except Exception:

                logger.exception(
                    "[SCANNER] Polling loop error."
                )

                await asyncio.sleep(
                    2.0
                )

        self.is_running = False

        logger.info(
            "[SCANNER] Market scanner stopped."
        )

    # ========================================================
    # STOP
    # ========================================================

    def stop(self):

        self.is_running = False
        self._stop_requested = True

        logger.info(
            "[SCANNER] Market scanner stopped."
        )
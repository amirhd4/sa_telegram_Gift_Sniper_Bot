"""
Main Entrypoint and Orchestrator for Telegram Stars Gift Sniper & Floor Analyzer.
Scanner-only authentication/debug version.
"""

import sys
import asyncio
import logging
import argparse

# Enable uvloop
try:
    import uvloop

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    uvloop_enabled = True
except ImportError:
    uvloop_enabled = False

# Enable tgcrypto
try:
    import tgcrypto

    tgcrypto_enabled = True
except ImportError:
    tgcrypto_enabled = False

from telethon import TelegramClient
from telethon import errors

from config import settings
from database import init_db, DatabaseRepository
from floor_engine import MemoryHotCache
from scanner import MarketScanner, GiftListing
from auto_buyer import AutoBuyer
from alert_bot import AlertChannelBot


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger("GiftSniperMain")


async def seed_example_data(
    db_repo: DatabaseRepository,
    cache: MemoryHotCache,
):
    """Seed initial reference floor prices."""

    logger.info(
        "[MAIN] Seeding initial reference floor prices "
        "into Database & Hot Cache..."
    )

    col_id = "MoodPack"

    await db_repo.upsert_general_floor(
        col_id,
        630.0,
    )

    await db_repo.upsert_model_floor(
        col_id,
        "Bank Vault",
        1200.0,
    )

    await db_repo.upsert_model_floor(
        col_id,
        "Star Pupil",
        1300.0,
    )

    await db_repo.upsert_background_floor(
        col_id,
        "Black",
        3100.0,
    )

    await db_repo.upsert_background_floor(
        col_id,
        "Onyx Black",
        1000.0,
    )

    await cache.sync_from_db([col_id])

    logger.info("[MAIN] Seeding completed successfully.")


async def authenticate_scanner(client: TelegramClient):
    """
    Connect and authenticate the Scanner Telegram account.

    If the session is already authorized, no login code is requested.
    If the session is not authorized, Telethon will ask for phone/code/2FA.
    """

    logger.info("[AUTH] Connecting Scanner Telegram client...")

    await client.connect()

    logger.info("[AUTH] Scanner client connected.")

    try:
        authorized = await client.is_user_authorized()
    except Exception as exc:
        logger.exception(
            "[AUTH] Failed while checking Scanner authorization: %s",
            exc,
        )
        raise

    if not authorized:
        logger.warning(
            "[AUTH] Scanner session is NOT authorized."
        )

        logger.info(
            "[AUTH] Starting interactive Telegram login..."
        )

        # This will ask for:
        # 1. phone number
        # 2. Telegram login code
        # 3. 2FA password if enabled
        await client.start()

        logger.info(
            "[AUTH] Scanner login process completed."
        )

    else:
        logger.info(
            "[AUTH] Scanner session is already authorized."
        )

    # Verify the authenticated Telegram account.
    me = await client.get_me()

    if me is None:
        raise RuntimeError(
            "Scanner Telegram session is not authenticated."
        )

    logger.info(
        "[AUTH] Scanner account authenticated successfully."
    )

    logger.info(
        "[AUTH] Telegram User ID: %s",
        me.id,
    )

    logger.info(
        "[AUTH] Telegram Username: @%s",
        me.username if me.username else "(no username)",
    )

    logger.info(
        "[AUTH] Telegram Name: %s",
        me.first_name if me.first_name else "(no first name)",
    )

    if getattr(me, "phone", None):
        logger.info(
            "[AUTH] Telegram Phone: %s",
            me.phone,
        )

    logger.info(
        "[AUTH] Scanner authentication check PASSED."
    )

    return me


async def main():

    parser = argparse.ArgumentParser(
        description="Telegram Stars Gift Sniper & Floor Analyzer"
    )

    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialize database tables",
    )

    parser.add_argument(
        "--seed",
        action="store_true",
        help="Seed initial floor price data",
    )

    parser.add_argument(
        "--proxy",
        action="store_true",
        help="Use proxy",
    )

    parser.add_argument(
        "--mode",
        choices=["scanner", "buyer", "all"],
        default="scanner",
        help="Mode to run",
    )

    args = parser.parse_args()

    logger.info(
        "[MAIN] Starting Gift Sniper Engine "
        "(uvloop: %s, tgcrypto: %s)",
        uvloop_enabled,
        tgcrypto_enabled,
    )

    # ---------------------------------------------------------
    # 1. Database
    # ---------------------------------------------------------

    db_repo = DatabaseRepository()

    if args.init_db:
        await init_db()

    # ---------------------------------------------------------
    # 2. Hot Cache
    # ---------------------------------------------------------

    cache = MemoryHotCache(db_repo)

    if args.seed:
        await seed_example_data(
            db_repo,
            cache,
        )
    else:
        await cache.sync_from_db(
            [
                "MoodPack",
                "BowTie",
            ]
        )

    # ---------------------------------------------------------
    # 3. Proxy
    # ---------------------------------------------------------

    proxy = {
        "proxy_type": "http",
        "addr": "127.0.0.1",
        "port": 10808,
    } if args.proxy else None

    # ---------------------------------------------------------
    # 4. Scanner Telegram Client
    # ---------------------------------------------------------

    scanner_client = TelegramClient(
        settings.SCANNER_SESSION,
        settings.API_ID,
        settings.API_HASH,
        proxy=proxy,
    )

    # ---------------------------------------------------------
    # 5. Scanner Authentication
    # ---------------------------------------------------------

    try:

        await authenticate_scanner(
            scanner_client
        )

    except errors.AuthKeyError as exc:

        logger.critical(
            "[AUTH] Scanner Session/AuthKey is invalid: %s",
            exc,
        )

        logger.critical(
            "[AUTH] The Telegram session is no longer "
            "recognized by Telegram."
        )

        logger.critical(
            "[AUTH] Create a fresh Scanner session and login again."
        )

        return

    except Exception as exc:

        logger.exception(
            "[AUTH] Scanner authentication failed: %s",
            exc,
        )

        return

    # ---------------------------------------------------------
    # 6. Alert Bot
    # ---------------------------------------------------------

    alert_bot = AlertChannelBot(
        scanner_client,
        settings.ALERT_CHANNEL_ID,
    )

    # ---------------------------------------------------------
    # 7. Auto Buyer
    #
    # IMPORTANT:
    # In scanner mode we DO NOT create/connect buyer client.
    # ---------------------------------------------------------

    buyer_client = None
    auto_buyer = None

    if args.mode in ("buyer", "all"):

        logger.info(
            "[MAIN] Buyer mode requested. "
            "Creating Buyer Telegram client..."
        )

        buyer_client = TelegramClient(
            settings.BUYER_SESSION,
            settings.API_ID,
            settings.API_HASH,
            proxy=proxy,
        )

        await buyer_client.start()

        auto_buyer = AutoBuyer(
            buyer_client=buyer_client,
            db_repo=db_repo,
            max_stars_per_gift=settings.MAX_STARS_PER_GIFT,
            daily_budget=settings.DAILY_STARS_BUDGET,
            hide_name=settings.HIDE_NAME,
        )

    # ---------------------------------------------------------
    # 8. Deal Handler
    # ---------------------------------------------------------

    async def handle_deal_found(
        listing: GiftListing,
        ref_floor: float,
        discount_pct: float,
    ):

        logger.info(
            "🔥 [DEAL_SIGNAL] DETECTED: %s | "
            "Ref Floor: %.1f | "
            "Discount: %.1f%%",
            listing,
            ref_floor,
            discount_pct * 100,
        )

        # ---------------------------------------------
        # Channel Alert
        # ---------------------------------------------

        if discount_pct >= settings.ALERT_DISCOUNT_THRESHOLD:

            logger.info(
                "📢 [MAIN] Sending Channel Alert "
                "(%.1f%% >= %.1f%%)",
                discount_pct * 100,
                settings.ALERT_DISCOUNT_THRESHOLD * 100,
            )

            await alert_bot.send_deal_alert(
                gift_id=listing.gift_id,
                collectible_name=listing.collectible_name,
                model=listing.model,
                background=listing.background,
                listed_price=listing.price_stars,
                ref_floor=ref_floor,
                discount_pct=discount_pct,
                gift_slug=listing.slug,
            )

        else:

            logger.debug(
                "ℹ️ [MAIN] Skipped Channel Alert "
                "(%.1f%% < %.1f%%)",
                discount_pct * 100,
                settings.ALERT_DISCOUNT_THRESHOLD * 100,
            )

        # ---------------------------------------------
        # Auto Snipe
        # ---------------------------------------------

        if discount_pct >= settings.SNIPE_DISCOUNT_THRESHOLD:

            if (
                args.mode in ("buyer", "all")
                and auto_buyer is not None
            ):

                logger.info(
                    "⚡ [MAIN] Executing Auto-Snipe "
                    "(%.1f%% >= %.1f%%)",
                    discount_pct * 100,
                    settings.SNIPE_DISCOUNT_THRESHOLD * 100,
                )

                await auto_buyer.buy_gift(
                    listing,
                    ref_floor,
                    discount_pct,
                )

        else:

            logger.debug(
                "ℹ️ [MAIN] Skipped Auto-Snipe "
                "(%.1f%% < %.1f%%)",
                discount_pct * 100,
                settings.SNIPE_DISCOUNT_THRESHOLD * 100,
            )

    # ---------------------------------------------------------
    # 9. Market Scanner
    # ---------------------------------------------------------

    target_gift_ids = None
    if settings.TARGET_GIFT_IDS:
        try:
            target_gift_ids = [
                int(x.strip())
                for x in settings.TARGET_GIFT_IDS.split(",")
                if x.strip().isdigit()
            ]
            logger.info(
                f"[MAIN] Configured static target gift IDs: {target_gift_ids}"
            )
        except Exception as e:
            logger.warning(
                f"[MAIN] Failed to parse TARGET_GIFT_IDS from config: {e}"
            )

    scanner = MarketScanner(
        client=scanner_client,
        cache=cache,
        db_repo=db_repo,
        target_gift_ids=target_gift_ids,
        alert_discount_threshold=settings.ALERT_DISCOUNT_THRESHOLD,
        snipe_discount_threshold=settings.SNIPE_DISCOUNT_THRESHOLD,
        max_stars_cap=settings.MAX_STARS_PER_GIFT,
        on_deal_found_callback=handle_deal_found,
        catalog_refresh_interval=settings.CATALOG_REFRESH_INTERVAL,
    )

    # ---------------------------------------------------------
    # 10. Start Scanner
    # ---------------------------------------------------------

    try:

        logger.info(
            "[MAIN] ========================================"
        )

        logger.info(
            "[MAIN] Scanner account is authenticated."
        )

        logger.info(
            "[MAIN] Running mode: %s",
            args.mode,
        )

        logger.info(
            "[MAIN] Starting Market Scanner..."
        )

        logger.info(
            "[MAIN] ========================================"
        )

        await scanner.start_polling(
            poll_interval=settings.POLL_INTERVAL_SECONDS
        )

    except KeyboardInterrupt:

        logger.info(
            "[MAIN] Shutdown signal received."
        )

    except Exception as exc:

        logger.exception(
            "[MAIN] Scanner crashed: %s",
            exc,
        )

    finally:

        scanner.stop()

        if scanner_client.is_connected():

            await scanner_client.disconnect()

        if (
            buyer_client is not None
            and buyer_client.is_connected()
        ):

            await buyer_client.disconnect()

        logger.info(
            "[MAIN] Shutdown completed."
        )


if __name__ == "__main__":
    asyncio.run(main())


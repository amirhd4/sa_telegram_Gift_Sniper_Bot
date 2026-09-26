"""
Main Entrypoint and Orchestrator for Telegram Stars Gift Sniper & Floor Analyzer.
"""
import sys
import asyncio
import logging
import argparse
from typing import Optional

# Enable uvloop for 2x-4x async event loop performance boost
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    uvloop_enabled = True
except ImportError:
    uvloop_enabled = False

# Enable tgcrypto for accelerated AES-IGE calculations
try:
    import tgcrypto
    tgcrypto_enabled = True
except ImportError:
    tgcrypto_enabled = False

from telethon import TelegramClient

from config import settings
from database import init_db, DatabaseRepository
from floor_engine import MemoryHotCache, FloorEngine
from scanner import MarketScanner, GiftListing
from auto_buyer import AutoBuyer
from alert_bot import AlertChannelBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("GiftSniperMain")


async def seed_example_data(db_repo: DatabaseRepository, cache: MemoryHotCache):
    """Seeds initial reference floors as described in conversation and PDF doc."""
    logger.info("[MAIN] Seeding initial reference floor prices into Database & Hot Cache...")

    # MoodPack collectible example from chat & PDF:
    col_id = "MoodPack"
    await db_repo.upsert_general_floor(col_id, 630.0)
    await db_repo.upsert_model_floor(col_id, "Bank Vault", 1200.0)
    await db_repo.upsert_model_floor(col_id, "Star Pupil", 1300.0)
    await db_repo.upsert_background_floor(col_id, "Black", 3100.0)
    await db_repo.upsert_background_floor(col_id, "Onyx Black", 1000.0)

    # Sync cache
    await cache.sync_from_db([col_id])
    logger.info("[MAIN] Seeding completed successfully.")


async def main():
    parser = argparse.ArgumentParser(description="Telegram Stars Gift Sniper & Floor Analyzer")
    parser.add_argument("--init-db", action="store_true", help="Initialize database tables")
    parser.add_argument("--seed", action="store_true", help="Seed initial floor price data")
    parser.add_argument("--proxy", action="store_true", help="Use proxy")
    parser.add_argument("--mode", choices=["scanner", "buyer", "all"], default="all", help="Mode to run")
    args = parser.parse_args()

    logger.info(f"[MAIN] Starting Gift Sniper Engine (uvloop: {uvloop_enabled}, tgcrypto: {tgcrypto_enabled})")

    # 1. Initialize Database & Repository
    db_repo = DatabaseRepository()
    if args.init_db:
        await init_db()

    # 2. Initialize Hot Cache
    cache = MemoryHotCache(db_repo)
    if args.seed:
        await seed_example_data(db_repo, cache)
    else:
        # Initial sync for standard collectibles
        await cache.sync_from_db(["MoodPack", "BowTie"])

    # 3. Create MTProto Telegram Clients (Isolated Scanner vs Buyer sessions)
    proxy = {
        "proxy_type": "http",
        "addr": "127.0.0.1",
        "port": 10808,
    } if args.proxy else None

    scanner_client = TelegramClient(
        settings.SCANNER_SESSION,
        settings.API_ID,
        settings.API_HASH,
        proxy=proxy
    )

    buyer_client = TelegramClient(
        settings.BUYER_SESSION,
        settings.API_ID,
        settings.API_HASH,
        proxy=proxy
    )

    # 4. Initialize Alert Bot
    alert_bot = AlertChannelBot(scanner_client, settings.ALERT_CHANNEL_ID)

    # 5. Initialize Auto Buyer
    auto_buyer = AutoBuyer(
        buyer_client=buyer_client,
        db_repo=db_repo,
        max_stars_per_gift=settings.MAX_STARS_PER_GIFT,
        daily_budget=settings.DAILY_STARS_BUDGET,
        hide_name=settings.HIDE_NAME
    )

    # 6. Define Deal Trigger Handler
    async def handle_deal_found(listing: GiftListing, ref_floor: float, discount_pct: float):
        logger.info(f"🔥 [DEAL_SIGNAL] DETECTED: {listing} | Ref Floor: {ref_floor:.1f} | Discount: {discount_pct:.1%}")

        # Action A: Send Channel Alert if discount meets/exceeds ALERT_DISCOUNT_THRESHOLD (>= 25%)
        if discount_pct >= settings.ALERT_DISCOUNT_THRESHOLD:
            logger.info(f"📢 [MAIN] Sending Channel Alert ({discount_pct:.1%} >= {settings.ALERT_DISCOUNT_THRESHOLD:.1%})")
            await alert_bot.send_deal_alert(
                gift_id=listing.gift_id,
                collectible_name=listing.collectible_name,
                model=listing.model,
                background=listing.background,
                listed_price=listing.price_stars,
                ref_floor=ref_floor,
                discount_pct=discount_pct,
                gift_slug=listing.slug
            )
        else:
            logger.info(f"ℹ️ [MAIN] Skipped Channel Alert ({discount_pct:.1%} < {settings.ALERT_DISCOUNT_THRESHOLD:.1%})")

        # Action B: Execute Auto Snipe Buy if in buyer/all mode and discount meets/exceeds SNIPE_DISCOUNT_THRESHOLD (>= 50%)
        if discount_pct >= settings.SNIPE_DISCOUNT_THRESHOLD:
            if args.mode in ("buyer", "all"):
                logger.info(f"⚡ [MAIN] Executing Auto-Snipe ({discount_pct:.1%} >= {settings.SNIPE_DISCOUNT_THRESHOLD:.1%})")
                await auto_buyer.buy_gift(listing, ref_floor, discount_pct)
        else:
            logger.info(f"ℹ️ [MAIN] Skipped Auto-Snipe ({discount_pct:.1%} < {settings.SNIPE_DISCOUNT_THRESHOLD:.1%})")

    # 7. Initialize Market Scanner
    scanner = MarketScanner(
        client=scanner_client,
        cache=cache,
        db_repo=db_repo,
        alert_discount_threshold=settings.ALERT_DISCOUNT_THRESHOLD,
        snipe_discount_threshold=settings.SNIPE_DISCOUNT_THRESHOLD,
        max_stars_cap=settings.MAX_STARS_PER_GIFT,
        on_deal_found_callback=handle_deal_found
    )

    # 8. Start Services
    try:
        await scanner_client.connect()
        await buyer_client.connect()

        logger.info(f"[MAIN] Bot running in '{args.mode}' mode. Press Ctrl+C to stop.")
        await scanner.start_polling(poll_interval=settings.POLL_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info("[MAIN] Shutdown signal received.")
    finally:
        scanner.stop()
        if scanner_client.is_connected():
            await scanner_client.disconnect()
        if buyer_client.is_connected():
            await buyer_client.disconnect()
        logger.info("[MAIN] Shutdown completed.")


if __name__ == "__main__":
    asyncio.run(main())

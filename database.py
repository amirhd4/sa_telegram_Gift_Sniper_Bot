"""
Database engine and Schema definition for Telegram Stars Gift Sniper & Floor Analyzer.
"""
import aiosqlite
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = "gift_sniper.db"


async def init_db(db_path: str = DB_PATH):
    """Initialize database tables asynchronously."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS general_floors (
                collectible_id TEXT PRIMARY KEY,
                floor_price REAL NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS model_floors (
                collectible_id TEXT NOT NULL,
                model_name TEXT NOT NULL,
                floor_price REAL NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (collectible_id, model_name)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS background_floors (
                collectible_id TEXT NOT NULL,
                bg_name TEXT NOT NULL,
                floor_price REAL NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (collectible_id, bg_name)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS gift_listings (
                gift_id TEXT PRIMARY KEY,
                collectible_id TEXT NOT NULL,
                gift_num INTEGER,
                model TEXT,
                background TEXT,
                price_stars INTEGER NOT NULL,
                is_available BOOLEAN DEFAULT 1,
                listed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS sniper_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gift_id TEXT NOT NULL,
                collectible_id TEXT NOT NULL,
                model TEXT,
                background TEXT,
                listed_price INTEGER NOT NULL,
                reference_floor REAL NOT NULL,
                discount_pct REAL NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.commit()
        logger.info("Database schema initialized successfully.")


class DatabaseRepository:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    async def upsert_general_floor(self, collectible_id: str, floor_price: float):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO general_floors (collectible_id, floor_price, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(collectible_id) DO UPDATE SET
                    floor_price = excluded.floor_price,
                    updated_at = CURRENT_TIMESTAMP
            """, (collectible_id, floor_price))
            await db.commit()

    async def upsert_model_floor(self, collectible_id: str, model_name: str, floor_price: float):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO model_floors (collectible_id, model_name, floor_price, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(collectible_id, model_name) DO UPDATE SET
                    floor_price = excluded.floor_price,
                    updated_at = CURRENT_TIMESTAMP
            """, (collectible_id, model_name, floor_price))
            await db.commit()

    async def upsert_background_floor(self, collectible_id: str, bg_name: str, floor_price: float):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO background_floors (collectible_id, bg_name, floor_price, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(collectible_id, bg_name) DO UPDATE SET
                    floor_price = excluded.floor_price,
                    updated_at = CURRENT_TIMESTAMP
            """, (collectible_id, bg_name, floor_price))
            await db.commit()

    async def get_floors_for_collectible(self, collectible_id: str) -> Dict[str, Any]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # General floor
            async with db.execute("SELECT floor_price FROM general_floors WHERE collectible_id = ?", (collectible_id,)) as cursor:
                row = await cursor.fetchone()
                gen_floor = row["floor_price"] if row else 0.0

            # Model floors
            model_floors = {}
            async with db.execute("SELECT model_name, floor_price FROM model_floors WHERE collectible_id = ?", (collectible_id,)) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    model_floors[r["model_name"]] = r["floor_price"]

            # Background floors
            bg_floors = {}
            async with db.execute("SELECT bg_name, floor_price FROM background_floors WHERE collectible_id = ?", (collectible_id,)) as cursor:
                rows = await cursor.fetchall()
                for r in rows:
                    bg_floors[r["bg_name"]] = r["floor_price"]

            return {
                "general_floor": gen_floor,
                "model_floors": model_floors,
                "bg_floors": bg_floors
            }

    async def log_transaction(
        self,
        gift_id: str,
        collectible_id: str,
        model: Optional[str],
        background: Optional[str],
        listed_price: int,
        reference_floor: float,
        discount_pct: float,
        status: str,
        error_message: Optional[str] = None
    ):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO sniper_transactions
                (gift_id, collectible_id, model, background, listed_price, reference_floor, discount_pct, status, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (gift_id, collectible_id, model, background, listed_price, reference_floor, discount_pct, status, error_message))
            await db.commit()

    async def get_daily_spent_stars(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT SUM(listed_price) FROM sniper_transactions
                WHERE status = 'SUCCESS' AND date(created_at) = date('now')
            """) as cursor:
                row = await cursor.fetchone()
                return row[0] if row and row[0] is not None else 0

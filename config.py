"""
Configuration management for Telegram Stars Gift Sniper & Floor Analyzer.
"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Telegram API Credentials
    API_ID: int
    API_HASH: str

    # MTProto Sessions (Separate scanner & buyer to prevent rate limits/floodwait)
    SCANNER_SESSION: str = "scanner_session"
    BUYER_SESSION: str = "buyer_session"

    # Alert Bot & Channel Configuration
    BOT_TOKEN: str = ""
    ALERT_CHANNEL_ID: Optional[int] = None  # e.g., -1001234567890

    # Valuation & Sniper Thresholds
    ALERT_DISCOUNT_THRESHOLD: float = 0.25  # Minimum 25% discount below floor to post to channel
    SNIPE_DISCOUNT_THRESHOLD: float = 0.50  # Minimum 50% discount below floor for auto-buyer snipe
    DISCOUNT_THRESHOLD: float = 0.20       # Legacy fallback threshold
    MAX_STARS_PER_GIFT: int = 100000   # Safety budget guard per single gift transaction
    DAILY_STARS_BUDGET: int = 500000   # Max total stars allowed to spend per day
    HIDE_NAME: bool = True             # Hide buyer's Telegram name on gift purchase

    # Database & Storage
    DATABASE_URL: str = "sqlite+aiosqlite:///gift_sniper.db"
    POLL_INTERVAL_SECONDS: float = 1.0 # Scanner interval in seconds
    TARGET_GIFT_IDS: Optional[str] = None # Optional comma-separated gift IDs, e.g., "101,102"
    CATALOG_REFRESH_INTERVAL: float = 600.0 # Base catalog refresh interval in seconds (default 10 mins)


settings = Settings()

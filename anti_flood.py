"""
Anti-FloodWait and rate limiter module for MTProto Telegram operations.
Prevents FLOOD_WAIT bans and manages request delays dynamically.
"""
import asyncio
import logging
from typing import Callable, Any
from telethon.errors import FloodWaitError

logger = logging.getLogger(__name__)


class AntiFloodModule:
    """
    Manages rate limits, dynamic backoff, and FloodWait delays.
    """

    def __init__(self, max_retries: int = 3, base_delay: float = 0.5):
        self.max_retries = max_retries
        self.base_delay = base_delay

    async def execute_with_anti_flood(self, func: Callable, *args, **kwargs) -> Any:
        """
        Executes an MTProto request with automatic retry handling for FloodWait exceptions.
        """
        retries = 0
        while retries <= self.max_retries:
            try:
                return await func(*args, **kwargs)
            except FloodWaitError as e:
                wait_seconds = e.seconds + 1
                logger.warning(
                    f"FLOOD_WAIT encountered: Must wait {wait_seconds} seconds before retrying. "
                    f"Attempt {retries + 1}/{self.max_retries}"
                )
                await asyncio.sleep(wait_seconds)
                retries += 1
            except Exception as e:
                logger.error(f"MTProto execution error: {e}")
                raise e

        raise Exception(f"Failed to execute MTProto action after {self.max_retries} FloodWait retries.")

"""
Automated Telegram Stars Gift Auto-Buyer Engine.
Executes purchase transactions via MTProto MTProto API:
payments.getPaymentForm + inputInvoiceStarGiftResale / inputInvoiceStarGift + payments.sendStarsForm
Ref: PDF Section 3 & Telegram API documentation.
"""
import logging
import asyncio
from typing import Optional, Dict, Any
from telethon import TelegramClient
from telethon.tl.functions.payments import GetPaymentFormRequest, SendStarsFormRequest
from telethon.tl.types import (
    InputInvoiceStarGiftResale,
    InputInvoiceStarGift,
    InputPeerSelf
)

from database import DatabaseRepository
from anti_flood import AntiFloodModule
from scanner import GiftListing

logger = logging.getLogger(__name__)


class AutoBuyer:
    """
    Executes automated Telegram Stars gift sniper buys using MTProto.
    Runs on an isolated Telegram session string (Buyer Session) to avoid rate limits.
    """

    def __init__(
        self,
        buyer_client: TelegramClient,
        db_repo: DatabaseRepository,
        max_stars_per_gift: int = 100000,
        daily_budget: int = 500000,
        hide_name: bool = True
    ):
        self.client = buyer_client
        self.db_repo = db_repo
        self.max_stars_per_gift = max_stars_per_gift
        self.daily_budget = daily_budget
        self.hide_name = hide_name
        self.anti_flood = AntiFloodModule()

    async def verify_budget_guards(self, price_stars: int) -> bool:
        """
        Validates single item limit and cumulative daily budget limits.
        """
        if price_stars > self.max_stars_per_gift:
            logger.warning(
                f"Budget Guard Alert: Gift price {price_stars} exceeds single item cap {self.max_stars_per_gift} Stars."
            )
            return False

        daily_spent = await self.db_repo.get_daily_spent_stars()
        if daily_spent + price_stars > self.daily_budget:
            logger.warning(
                f"Budget Guard Alert: Total daily spend ({daily_spent} + {price_stars}) "
                f"exceeds daily budget cap {self.daily_budget} Stars."
            )
            return False

        return True

    async def buy_gift(
        self,
        listing: GiftListing,
        ref_floor: float,
        discount_pct: float
    ) -> bool:
        """
        Executes instant gift purchase via MTProto payments protocol.
        """
        can_buy = await self.verify_budget_guards(listing.price_stars)
        if not can_buy:
            await self.db_repo.log_transaction(
                gift_id=listing.gift_id,
                collectible_id=listing.collectible_name,
                model=listing.model,
                background=listing.background,
                listed_price=listing.price_stars,
                reference_floor=ref_floor,
                discount_pct=discount_pct,
                status="SKIPPED_BUDGET_CAP",
                error_message="Exceeded max item price or daily budget guard"
            )
            return False

        logger.info(
            f"⚡ INITIATING SNIPE BUY for {listing.slug} "
            f"at {listing.price_stars} Stars (Ref Floor: {ref_floor}, Discount: {discount_pct:.1%})"
        )

        try:
            # Construct Invoice Request (Section 3 of PDF doc)
            peer = InputPeerSelf()
            if listing.slug:
                invoice = InputInvoiceStarGiftResale(
                    slug=listing.slug,
                    to_id=peer,
                    show_name=not self.hide_name
                )
            else:
                invoice = InputInvoiceStarGift(
                    peer=peer,
                    gift_id=int(listing.gift_id),
                    hide_name=self.hide_name
                )

            # Retrieve payment form
            async def _get_form():
                return await self.client(GetPaymentFormRequest(
                    invoice=invoice
                ))

            form = await self.anti_flood.execute_with_anti_flood(_get_form)
            form_id = getattr(form, "form_id", 123456)

            # Submit Stars Form
            async def _send_stars():
                return await self.client(SendStarsFormRequest(
                    form_id=form_id,
                    invoice=invoice
                ))

            res = await self.anti_flood.execute_with_anti_flood(_send_stars)

            logger.info(f"✅ SNIPE SUCCESSFUL for gift {listing.gift_id}! Result: {res}")

            await self.db_repo.log_transaction(
                gift_id=listing.gift_id,
                collectible_id=listing.collectible_name,
                model=listing.model,
                background=listing.background,
                listed_price=listing.price_stars,
                reference_floor=ref_floor,
                discount_pct=discount_pct,
                status="SUCCESS"
            )
            return True

        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ SNIPE FAILED for gift {listing.gift_id}: {error_msg}")

            await self.db_repo.log_transaction(
                gift_id=listing.gift_id,
                collectible_id=listing.collectible_name,
                model=listing.model,
                background=listing.background,
                listed_price=listing.price_stars,
                reference_floor=ref_floor,
                discount_pct=discount_pct,
                status="FAILED",
                error_message=error_msg
            )
            return False

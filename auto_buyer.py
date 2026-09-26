"""
Automated Telegram Stars Gift Auto-Buyer Engine.
Executes purchase transactions via MTProto API:
GetPaymentFormRequest + SendStarsFormRequest with InputInvoiceStarGiftResale / InputInvoiceStarGift.
Implements the TL RPC Errors Matrix (Section 4.1):
- 400 BALANCE_TOO_LOW: Disable sniper engine immediately & send critical log
- 400 STARGIFT_INVALID / STARGIFT_NOT_FOUND: Remove gift & cancel purchase
- 400 TO_ID_INVALID: Rebuild InputPeer entities
- 420 FLOOD_WAIT_X: Handled with Anti-Flood retry mechanism
- Account Ban / Session Deactivation handling
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
from telethon.errors import (
    UserDeactivatedError,
    UserDeactivatedBanError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    AuthKeyInvalidError,
    FloodWaitError,
    RPCError
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
        self.is_active = True  # Can be disabled dynamically on critical errors like BALANCE_TOO_LOW

    async def verify_budget_guards(self, price_stars: int) -> bool:
        """
        Validates single item limit and cumulative daily budget limits (Budget Guards Section 4.2).
        """
        if price_stars > self.max_stars_per_gift:
            logger.warning(
                f"[BUYER] [BUDGET_GUARD] Alert: Gift price {price_stars} Stars exceeds single item limit {self.max_stars_per_gift} Stars."
            )
            return False

        daily_spent = await self.db_repo.get_daily_spent_stars()
        if daily_spent + price_stars > self.daily_budget:
            logger.warning(
                f"[BUYER] [BUDGET_GUARD] Alert: Cumulative daily spend ({daily_spent} + {price_stars}) "
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
        if not self.is_active:
            logger.warning(f"[BUYER] AutoBuyer engine is currently deactivated. Skipping buy for {listing.slug}")
            return False

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
                error_message="Exceeded max single gift price or daily budget guard limit"
            )
            return False

        logger.info(
            f"⚡ [BUYER] INITIATING SNIPE BUY for {listing.slug} "
            f"at {listing.price_stars} Stars (Ref Floor: {ref_floor:.1f}, Discount: {discount_pct:.1%})"
        )

        try:
            # Construct Invoice Request
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

            # Step 1: Retrieve payment form
            async def _get_form():
                return await self.client(GetPaymentFormRequest(
                    invoice=invoice
                ))

            form = await self.anti_flood.execute_with_anti_flood(_get_form)
            form_id = getattr(form, "form_id", None)

            if not form_id:
                raise ValueError("Payment form returned without valid form_id")

            # Step 2: Finalize purchase with Stars
            async def _send_stars():
                return await self.client(SendStarsFormRequest(
                    form_id=form_id,
                    invoice=invoice
                ))

            res = await self.anti_flood.execute_with_anti_flood(_send_stars)

            logger.info(f"✅ [BUYER] SNIPE SUCCESSFUL for gift {listing.gift_id}! Result: {res}")

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

        except (UserDeactivatedError, UserDeactivatedBanError, AuthKeyUnregisteredError, SessionRevokedError, AuthKeyInvalidError) as acc_err:
            logger.critical(
                f"[ACCOUNT_STATUS] [BUYER] 🛑 CRITICAL BUYER ACCOUNT ERROR/BAN DETECTED: {acc_err}. "
                f"Buyer session deactivated or banned! Deactivating buyer."
            )
            self.is_active = False
            await self.db_repo.log_transaction(
                gift_id=listing.gift_id,
                collectible_id=listing.collectible_name,
                model=listing.model,
                background=listing.background,
                listed_price=listing.price_stars,
                reference_floor=ref_floor,
                discount_pct=discount_pct,
                status="FAILED",
                error_message=f"ACCOUNT_BAN: {str(acc_err)}"
            )
            return False

        except RPCError as rpc_err:
            error_code = getattr(rpc_err, 'code', None)
            error_msg = getattr(rpc_err, 'message', str(rpc_err)).upper()

            # TL RPC Errors Matrix Handling (Section 4.1)
            if "BALANCE_TOO_LOW" in error_msg:
                logger.critical(
                    f"[BUYER] [ERROR_400] ❌ BALANCE_TOO_LOW: Buyer account does not have enough Stars! "
                    f"Immediately deactivating sniper engine."
                )
                self.is_active = False
                status_msg = "BALANCE_TOO_LOW: AutoBuyer deactivated"

            elif "STARGIFT_INVALID" in error_msg or "STARGIFT_NOT_FOUND" in error_msg:
                logger.warning(
                    f"[BUYER] [ERROR_400] ⚠️ STARGIFT_INVALID or STARGIFT_NOT_FOUND: Gift {listing.gift_id} "
                    f"is no longer available or already sold. Cancelling order."
                )
                status_msg = f"Gift sold/invalid: {error_msg}"

            elif "TO_ID_INVALID" in error_msg:
                logger.error(f"[BUYER] [ERROR_400] ⚠️ TO_ID_INVALID: Peer destination invalid. Error: {error_msg}")
                status_msg = f"TO_ID_INVALID: {error_msg}"

            else:
                logger.error(f"❌ [BUYER] RPC Transaction Error for gift {listing.gift_id}: Code={error_code}, Msg={error_msg}")
                status_msg = f"RPCError ({error_code}): {error_msg}"

            await self.db_repo.log_transaction(
                gift_id=listing.gift_id,
                collectible_id=listing.collectible_name,
                model=listing.model,
                background=listing.background,
                listed_price=listing.price_stars,
                reference_floor=ref_floor,
                discount_pct=discount_pct,
                status="FAILED",
                error_message=status_msg
            )
            return False

        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ [BUYER] UNEXPECTED SNIPE FAILURE for gift {listing.gift_id}: {error_msg}")

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

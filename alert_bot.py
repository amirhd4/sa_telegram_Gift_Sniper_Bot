"""
Telegram Alert Channel Bot Logger for Telegram Stars Gift Sniper.
Sends instant deal alerts to Telegram channels with gift metadata,
discount calculation, and direct purchase link.
"""
import logging
from typing import Optional
from telethon import TelegramClient

logger = logging.getLogger(__name__)


class AlertChannelBot:
    """
    Sends real-time deal alerts to Telegram channels/groups.
    """

    def __init__(self, client: TelegramClient, channel_id: Optional[int] = None):
        self.client = client
        self.channel_id = channel_id

    async def send_deal_alert(
        self,
        gift_id: str,
        collectible_name: str,
        model: Optional[str],
        background: Optional[str],
        listed_price: int,
        ref_floor: float,
        discount_pct: float,
        gift_slug: Optional[str] = None
    ):
        if not self.channel_id:
            logger.warning("[ALERT_BOT] No ALERT_CHANNEL_ID configured in settings. Channel alert skipped.")
            return

        # Direct Telegram NFT Gift link format e.g., https://t.me/nft/MoodPack-182609
        buy_link = f"https://t.me/nft/{gift_slug}" if gift_slug else f"https://t.me/nft/{collectible_name}-{gift_id}"

        message = (
            f"🚨 **NEW GIFT DEAL DETECTED!** 🚨\n\n"
            f"🎁 **Collectible**: `{collectible_name}`\n"
            f"🎨 **Model**: `{model or 'N/A'}`\n"
            f"🖼 **Background**: `{background or 'N/A'}`\n\n"
            f"💰 **Listed Price**: **{listed_price:,} Stars**\n"
            f"📊 **Reference Floor**: `{ref_floor:,.1f} Stars`\n"
            f"🔥 **Discount**: **{discount_pct:.1%} BELOW FLOOR**\n\n"
            f"🔗 [View / Buy Gift in Telegram]({buy_link})"
        )

        try:
            await self.client.send_message(
                entity=self.channel_id,
                message=message,
                link_preview=False
            )
            logger.info(f"[ALERT_BOT] 📢 Deal alert successfully sent to channel {self.channel_id} for gift {gift_id} (Slug: {gift_slug})")
        except Exception as e:
            logger.error(f"[ALERT_BOT] ❌ Failed to send deal alert to channel {self.channel_id}: {e}")

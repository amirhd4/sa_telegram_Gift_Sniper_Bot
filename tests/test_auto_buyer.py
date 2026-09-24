"""
Unit tests for Auto Buyer & Budget Guards.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from auto_buyer import AutoBuyer
from scanner import GiftListing
from database import DatabaseRepository


@pytest.mark.asyncio
async def test_auto_buyer_budget_guards():
    db_mock = AsyncMock(spec=DatabaseRepository)
    db_mock.get_daily_spent_stars.return_value = 10000

    client_mock = AsyncMock()
    client_mock.return_value = MagicMock(form_id=123)

    buyer = AutoBuyer(
        buyer_client=client_mock,
        db_repo=db_mock,
        max_stars_per_gift=50000,
        daily_budget=100000,
        hide_name=True
    )

    # Test item exceeding single item limit
    expensive_item = GiftListing(
        gift_id="999",
        collectible_name="MoodPack",
        gift_num=999,
        model="Rare",
        background="Black",
        price_stars=60000
    )

    can_buy = await buyer.verify_budget_guards(expensive_item.price_stars)
    assert can_buy is False

    # Test item within budget limits
    normal_item = GiftListing(
        gift_id="1000",
        collectible_name="MoodPack",
        gift_num=1000,
        model="Standard",
        background="Default",
        price_stars=5000
    )

    can_buy_normal = await buyer.verify_budget_guards(normal_item.price_stars)
    assert can_buy_normal is True

"""
Unit tests for MarketScanner attributes parsing, resale fetching, and deal triggers.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from scanner import MarketScanner, GiftListing
from floor_engine import MemoryHotCache


class DummyModelAttr:
    def __init__(self, name):
        self.name = name


class DummyBackdropAttr:
    def __init__(self, name):
        self.name = name


def test_parse_attributes():
    scanner = MarketScanner(
        client=MagicMock(),
        cache=MemoryHotCache(),
        db_repo=MagicMock()
    )

    # TL Class attributes
    attrs = [DummyModelAttr("Star Pulip"), DummyBackdropAttr("Onyx Black")]
    model, bg = scanner.parse_attributes(attrs)
    assert model == "Star Pulip"
    assert bg == "Onyx Black"

    # Dict attributes fallback
    dict_attrs = [{"type": "model", "name": "Bank Vault"}, {"type": "backdrop", "name": "Black"}]
    model_d, bg_d = scanner.parse_attributes(dict_attrs)
    assert model_d == "Bank Vault"
    assert bg_d == "Black"


@pytest.mark.asyncio
async def test_scanner_fetch_resale_listings():
    client_mock = AsyncMock()
    client_mock.is_connected = MagicMock(return_value=True)
    cache = MemoryHotCache()
    db_mock = AsyncMock()

    # Mock raw gift response
    mock_gift = MagicMock()
    mock_gift.id = 12345
    mock_gift.stars = 400
    mock_gift.title = "MoodPack"
    mock_gift.num = 182609
    mock_gift.slug = "MoodPack-182609"
    mock_gift.attributes = [DummyModelAttr("Star Pulip"), DummyBackdropAttr("Black")]

    mock_resale_res = MagicMock()
    mock_resale_res.gifts = [mock_gift]

    client_mock.side_effect = [mock_resale_res]

    scanner = MarketScanner(
        client=client_mock,
        cache=cache,
        db_repo=db_mock,
        target_gift_ids=[123]
    )

    listings = await scanner.fetch_resale_listings()
    assert len(listings) == 1
    item = listings[0]
    assert item.gift_id == "12345"
    assert item.price_stars == 400
    assert item.model == "Star Pulip"
    assert item.background == "Black"
    assert item.slug == "MoodPack-182609"

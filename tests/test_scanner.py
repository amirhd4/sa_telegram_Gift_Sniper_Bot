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


@pytest.mark.asyncio
async def test_scanner_catalog_caching_and_refresh():
    client_mock = AsyncMock()
    client_mock.is_connected = MagicMock(return_value=True)

    # Mock catalog gift response
    mock_base_gift = MagicMock()
    mock_base_gift.id = 999

    mock_catalog_res = MagicMock()
    mock_catalog_res.gifts = [mock_base_gift]

    client_mock.side_effect = [mock_catalog_res]

    scanner = MarketScanner(
        client=client_mock,
        cache=MemoryHotCache(),
        db_repo=AsyncMock(),
        catalog_refresh_interval=600.0
    )

    # First call should invoke MTProto call
    ids_1 = await scanner.fetch_catalog_gift_ids()
    assert ids_1 == [999]
    assert client_mock.call_count == 1

    # Second immediate call should return cached result without MTProto call
    ids_2 = await scanner.fetch_catalog_gift_ids()
    assert ids_2 == [999]
    assert client_mock.call_count == 1


@pytest.mark.asyncio
async def test_scanner_timeout_handling():
    client_mock = AsyncMock()
    client_mock.is_connected = MagicMock(return_value=True)

    import asyncio
    async def slow_call(*args, **kwargs):
        await asyncio.sleep(2.0)

    client_mock.side_effect = slow_call

    scanner = MarketScanner(
        client=client_mock,
        cache=MemoryHotCache(),
        db_repo=AsyncMock(),
        target_gift_ids=[123],
        request_timeout=0.1
    )

    listings = await scanner.fetch_resale_listings()
    assert listings == []

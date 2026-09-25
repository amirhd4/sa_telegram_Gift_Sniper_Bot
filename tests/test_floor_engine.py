"""
Unit tests for Floor Engine and Memory Hot Cache.
"""
import pytest
from floor_engine import FloorEngine, MemoryHotCache


def test_cheapest_three_average():
    # Test case 1: Empty list
    assert FloorEngine.calculate_cheapest_three_average([]) == 0.0

    # Test case 2: Fewer than 3 items
    assert FloorEngine.calculate_cheapest_three_average([100.0, 200.0]) == 150.0

    # Test case 3: Customer example (637 + 646 + 649 = 1932 / 3 = 644)
    assert FloorEngine.calculate_cheapest_three_average([637.0, 646.0, 649.0]) == 644.0

    # Test case 4: More than 3 items (takes 3 cheapest: 400, 500, 600 -> average 500)
    assert FloorEngine.calculate_cheapest_three_average([600.0, 400.0, 500.0, 1000.0, 5000.0]) == 500.0


@pytest.mark.asyncio
async def test_update_floors_from_listings():
    cache = MemoryHotCache()
    mock_listings = [
        {"price_stars": 637, "model": "Star Pulip", "background": "Black"},
        {"price_stars": 646, "model": "Star Pulip", "background": "Black"},
        {"price_stars": 649, "model": "Star Pulip", "background": "Black"},
        {"price_stars": 1000, "model": "Star Pulip", "background": "Onyx"},
    ]

    await cache.update_floors_from_listings("MoodPack", mock_listings, persist_to_db=False)

    # General floor = 644.0
    assert cache.get_reference_floor("MoodPack") == 644.0

    # Model Star Pulip floor = 644.0
    assert cache.get_reference_floor("MoodPack", model="Star Pulip") == 644.0


def test_reference_floor_calculation():
    # F_ref = max(F_general, F_model, F_background)
    ref = FloorEngine.calculate_reference_floor(
        general_floor=630.0,
        model_floor=1200.0,
        bg_floor=3100.0
    )
    assert ref == 3100.0


def test_deal_evaluation():
    # Buy signal triggered if discount >= 20%
    is_buy, disc, reason = FloorEngine.evaluate_deal(
        listed_price=2000.0,
        ref_floor=3100.0,
        discount_threshold=0.20
    )
    assert is_buy is True
    assert round(disc, 4) == round((3100 - 2000) / 3100, 4)

    # Buy signal rejected if price is too high
    is_buy_reject, _, _ = FloorEngine.evaluate_deal(
        listed_price=2800.0,
        ref_floor=3100.0,
        discount_threshold=0.20
    )
    assert is_buy_reject is False


def test_hot_cache_lookups():
    cache = MemoryHotCache()
    cache.set_floors(
        collectible_id="MoodPack",
        general_floor=630.0,
        model_floors={"Bank Vault": 1200.0},
        bg_floors={"Black": 3100.0, "Onyx Black": 1000.0}
    )

    # Check general floor lookup
    assert cache.get_reference_floor("MoodPack") == 630.0

    # Check model floor lookup
    assert cache.get_reference_floor("MoodPack", model="Bank Vault") == 1200.0

    # Check background floor lookup (Black)
    assert cache.get_reference_floor("MoodPack", background="Black") == 3100.0

    # Check combination (max of general, model, bg)
    assert cache.get_reference_floor("MoodPack", model="Bank Vault", background="Black") == 3100.0

"""
Unit tests for Floor Engine and Memory Hot Cache.
"""
import pytest
from floor_engine import FloorEngine, MemoryHotCache


def test_trimmed_moving_average():
    # Test case 1: Empty list
    assert FloorEngine.calculate_trimmed_moving_average([]) == 0.0

    # Test case 2: Few items (< 3)
    assert FloorEngine.calculate_trimmed_moving_average([100.0, 200.0]) == 150.0

    # Test case 3: 3 items (Average of 3 lowest)
    assert FloorEngine.calculate_trimmed_moving_average([600.0, 630.0, 660.0]) == 630.0

    # Test case 4: Extreme outliers removed with trim ratio
    prices = [500.0, 600.0, 620.0, 640.0, 10000.0]  # Outlier 10000 wash trade
    tma = FloorEngine.calculate_trimmed_moving_average(prices, trim_ratio=0.1)
    assert tma < 1000.0


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

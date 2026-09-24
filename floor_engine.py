"""
In-Memory Hot Cache & Floor Price Engine for Telegram Stars Gifts.
Provides sub-5ms reference floor evaluation, trimmed moving average computation,
and memory caching with O(1) lookups.
"""
import logging
from typing import List, Dict, Optional, Tuple, Any
from database import DatabaseRepository

logger = logging.getLogger(__name__)


class FloorEngine:
    """
    Computes floor prices, reference floor, and evaluation logic.
    Ref: PDF Section 1 & 2 formula.
    """

    @staticmethod
    def calculate_trimmed_moving_average(prices: List[float], trim_ratio: float = 0.1) -> float:
        """
        Calculates Trimmed Moving Average to remove extreme outliers and wash trading prices.
        If fewer than 3 prices are available, returns average of available prices.
        """
        if not prices:
            return 0.0

        sorted_prices = sorted(prices)
        n = len(sorted_prices)

        if n <= 3:
            # Average of up to 3 lowest prices as specified in PDF section 2:
            # "General floor = average of 3 cheapest items"
            return sum(sorted_prices[:n]) / float(n)

        k = int(n * trim_ratio)
        if k > 0 and (n - 2 * k) > 0:
            trimmed = sorted_prices[k: n - k]
        else:
            trimmed = sorted_prices

        # Take average of the lowest 3 prices from trimmed set
        cheapest_3 = trimmed[:3]
        return sum(cheapest_3) / float(len(cheapest_3))

    @staticmethod
    def calculate_reference_floor(
        general_floor: float,
        model_floor: float,
        bg_floor: float
    ) -> float:
        """
        Calculates reference floor price (F_ref) according to formula:
        F_ref = max(F_general, F_model, F_background)
        """
        return max(general_floor, model_floor, bg_floor)

    @staticmethod
    def evaluate_deal(
        listed_price: float,
        ref_floor: float,
        discount_threshold: float = 0.20,
        max_stars_cap: Optional[int] = None
    ) -> Tuple[bool, float, str]:
        """
        Evaluates whether a listing is a profitable deal.
        Formula: Listed_Price <= Ref_Floor * (1 - Discount_Threshold)

        Returns:
            (is_buy_signal, discount_pct, reason)
        """
        if ref_floor <= 0:
            return False, 0.0, "Invalid reference floor (0)"

        if max_stars_cap and listed_price > max_stars_cap:
            return False, 0.0, f"Price {listed_price} exceeds max cap {max_stars_cap}"

        discount_pct = (ref_floor - listed_price) / ref_floor

        if discount_pct >= discount_threshold:
            return True, discount_pct, f"Discount {discount_pct:.2%} exceeds threshold {discount_threshold:.2%}"

        return False, discount_pct, f"Discount {discount_pct:.2%} below threshold {discount_threshold:.2%}"


class MemoryHotCache:
    """
    In-Memory Hot Cache storing reference floors for O(1) instantaneous lookup.
    """

    def __init__(self, db_repo: Optional[DatabaseRepository] = None):
        self.db_repo = db_repo
        # Store floor matrices in memory:
        # { collectible_id: { "general": float, "models": { model_name: float }, "backgrounds": { bg_name: float } } }
        self._cache: Dict[str, Dict[str, Any]] = {}

    async def sync_from_db(self, collectible_ids: List[str]):
        """Synchronizes cache from DB for given collectibles."""
        if not self.db_repo:
            return

        for col_id in collectible_ids:
            data = await self.db_repo.get_floors_for_collectible(col_id)
            self._cache[col_id] = {
                "general": data.get("general_floor", 0.0),
                "models": data.get("model_floors", {}),
                "backgrounds": data.get("bg_floors", {})
            }
        logger.info(f"Hot Cache updated for {len(collectible_ids)} collectibles.")

    def set_floors(
        self,
        collectible_id: str,
        general_floor: float,
        model_floors: Optional[Dict[str, float]] = None,
        bg_floors: Optional[Dict[str, float]] = None
    ):
        """Sets or updates floor matrix in memory."""
        if collectible_id not in self._cache:
            self._cache[collectible_id] = {
                "general": 0.0,
                "models": {},
                "backgrounds": {}
            }

        self._cache[collectible_id]["general"] = general_floor
        if model_floors:
            self._cache[collectible_id]["models"].update(model_floors)
        if bg_floors:
            self._cache[collectible_id]["backgrounds"].update(bg_floors)

    def get_reference_floor(
        self,
        collectible_id: str,
        model: Optional[str] = None,
        background: Optional[str] = None
    ) -> float:
        """
        Retrieves reference floor F_ref = max(F_general, F_model, F_background)
        in O(1) time complexity (<1ms).
        """
        col_data = self._cache.get(collectible_id)
        if not col_data:
            return 0.0

        gen_floor = col_data.get("general", 0.0)

        model_floor = 0.0
        if model and model in col_data.get("models", {}):
            model_floor = col_data["models"][model]

        bg_floor = 0.0
        # Check specific background (especially Black and Onyx Black)
        if background and background in col_data.get("backgrounds", {}):
            bg_floor = col_data["backgrounds"][background]

        return FloorEngine.calculate_reference_floor(gen_floor, model_floor, bg_floor)

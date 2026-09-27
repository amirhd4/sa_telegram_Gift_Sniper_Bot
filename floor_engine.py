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
    def calculate_cheapest_three_average(prices: List[float]) -> float:
        """
        Calculates the floor price as the average of the 3 cheapest items in the market.
        Example: [637, 646, 649] -> (637 + 646 + 649) / 3 = 644.0
        """
        valid_prices = [p for p in prices if p > 0]
        if not valid_prices:
            return 0.0

        sorted_prices = sorted(valid_prices)
        cheapest_3 = sorted_prices[:3]
        return float(sum(cheapest_3)) / float(len(cheapest_3))

    @staticmethod
    def calculate_trimmed_moving_average(prices: List[float], trim_ratio: float = 0.1) -> float:
        """
        Maintained for backward compatibility. Uses the 3 cheapest items average rule requested by user.
        """
        return FloorEngine.calculate_cheapest_three_average(prices)

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
        logger.debug(f"Hot Cache updated for {len(collectible_ids)} collectibles.")

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

    async def update_floors_from_listings(
        self,
        collectible_id: str,
        listings: List[Any],
        persist_to_db: bool = True
    ):
        """
        Dynamically calculates and updates general, model, and background floors
        from market listings using average of 3 cheapest items algorithm.
        """
        general_prices: List[float] = []
        model_prices: Dict[str, List[float]] = {}
        bg_prices: Dict[str, List[float]] = {}

        for item in listings:
            price = getattr(item, "price_stars", None) if not isinstance(item, dict) else item.get("price_stars")
            model = getattr(item, "model", None) if not isinstance(item, dict) else item.get("model")
            bg = getattr(item, "background", None) if not isinstance(item, dict) else item.get("background")

            if price and price > 0:
                general_prices.append(float(price))
                if model:
                    model_prices.setdefault(model, []).append(float(price))
                if bg:
                    bg_prices.setdefault(bg, []).append(float(price))

        gen_floor = FloorEngine.calculate_cheapest_three_average(general_prices)
        computed_models = {m: FloorEngine.calculate_cheapest_three_average(p) for m, p in model_prices.items()}
        computed_bgs = {b: FloorEngine.calculate_cheapest_three_average(p) for b, p in bg_prices.items()}

        self.set_floors(
            collectible_id=collectible_id,
            general_floor=gen_floor,
            model_floors=computed_models,
            bg_floors=computed_bgs
        )

        if persist_to_db and self.db_repo:
            if gen_floor > 0:
                await self.db_repo.upsert_general_floor(collectible_id, gen_floor)
            for m_name, m_floor in computed_models.items():
                if m_floor > 0:
                    await self.db_repo.upsert_model_floor(collectible_id, m_name, m_floor)
            for b_name, b_floor in computed_bgs.items():
                if b_floor > 0:
                    await self.db_repo.upsert_background_floor(collectible_id, b_name, b_floor)

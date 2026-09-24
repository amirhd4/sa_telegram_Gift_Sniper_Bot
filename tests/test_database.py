"""
Unit tests for Async Database Repository.
"""
import pytest
import os
from database import init_db, DatabaseRepository


@pytest.mark.asyncio
async def test_database_crud(tmp_path):
    db_file = os.path.join(tmp_path, "test_sniper.db")
    await init_db(db_file)

    repo = DatabaseRepository(db_file)

    # 1. Upsert floors
    await repo.upsert_general_floor("BowTie", 500.0)
    await repo.upsert_model_floor("BowTie", "Dribble", 1000.0)
    await repo.upsert_background_floor("BowTie", "Black", 2500.0)

    # 2. Retrieve floors
    floors = await repo.get_floors_for_collectible("BowTie")
    assert floors["general_floor"] == 500.0
    assert floors["model_floors"]["Dribble"] == 1000.0
    assert floors["bg_floors"]["Black"] == 2500.0

    # 3. Log transaction
    await repo.log_transaction(
        gift_id="48660",
        collectible_id="BowTie",
        model="Dribble",
        background="Black",
        listed_price=1800,
        reference_floor=2500.0,
        discount_pct=0.28,
        status="SUCCESS"
    )

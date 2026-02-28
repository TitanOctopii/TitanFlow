from pathlib import Path

import pytest
from sqlalchemy import text

from titanflow.config import DatabaseConfig
from titanflow.core.database import Database


@pytest.mark.asyncio
async def test_database_init_sets_pragmas(tmp_path):
    db_path = tmp_path / "titanflow.db"
    config = DatabaseConfig(path=str(db_path))
    db = Database(config)
    await db.init()

    async with db._engine.connect() as conn:
        journal_mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
        synchronous = (await conn.execute(text("PRAGMA synchronous"))).scalar_one()

    assert journal_mode.lower() == "wal"
    assert str(synchronous) in {"1", "NORMAL"}

    await db.close()

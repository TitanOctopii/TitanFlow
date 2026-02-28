"""TitanFlow Database — SQLModel + async SQLite."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from titanflow.config import DatabaseConfig

logger = logging.getLogger("titanflow.database")


class Database:
    """Async SQLite database manager."""

    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config
        self.db_path = Path(config.path)
        self._engine = None
        self._session_factory = None

    async def init(self) -> None:
        """Initialize database connection and create tables."""
        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        db_url = f"sqlite+aiosqlite:///{self.db_path}"
        self._engine = create_async_engine(db_url, echo=False)
        self._session_factory = sessionmaker(
            self._engine, class_=AsyncSession, expire_on_commit=False
        )

        # Create all tables
        async with self._engine.begin() as conn:
            await conn.execute(text("PRAGMA journal_mode=WAL"))
            await conn.execute(text("PRAGMA synchronous=NORMAL"))
            journal_mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
            synchronous = (await conn.execute(text("PRAGMA synchronous"))).scalar_one()
            await conn.run_sync(SQLModel.metadata.create_all)

        logger.info(f"Database initialized at {self.db_path} (journal_mode={journal_mode}, synchronous={synchronous})")

    def session(self) -> AsyncSession:
        """Get an async session. Use as async context manager."""
        if self._session_factory is None:
            raise RuntimeError("Database not initialized. Call init() first.")
        return self._session_factory()

    async def close(self) -> None:
        """Close database connection."""
        if self._engine:
            await self._engine.dispose()
            logger.info("Database connection closed")

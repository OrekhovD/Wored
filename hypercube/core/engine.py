"""Async SQLAlchemy engine and session factories."""

from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ..core.config import config
from ..core.logger import module_logger

log = module_logger("engine")

_settings = config()
_db_url = _settings.SQLITE_DB_URL

# SQLite needs check_same_thread disabled when aiosqlite handles connections
# on separate threads/coroutines.
connect_args: dict = {}
if "sqlite" in _db_url:
    connect_args["check_same_thread"] = False

_engine = create_async_engine(
    _db_url,
    echo=settings.LOG_LEVEL == "DEBUG",
    connect_args=connect_args,
    pool_pre_ping=True,
)

_session_factory = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async DB session."""
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_engine():
    """Expose the underlying engine (e.g. for Alembic or raw SQL)."""
    return _engine

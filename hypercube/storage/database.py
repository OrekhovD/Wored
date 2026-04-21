"""Database initialization and session factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.config import AppConfiguration

engine = create_async_engine(
    "sqlite+aiosqlite:///./data/hytergram.db",
    echo=False,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency-injection DB session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db(config: AppConfiguration | None = None) -> None:
    """Create all ORM tables."""
    from storage.models import Base  # noqa: loop import

    if config:
        global engine
        engine = create_async_engine(
            config.SQLITE_DB_URL,
            echo=False,
        )
        global async_session_factory
        async_session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

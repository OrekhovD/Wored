"""SQLAlchemy base metadata and table factory helpers."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine

from .models.accounting import RequestAccountingModel
from .models.base import Base
from .models.context import SessionContextModel
from .models.message import MessageModel
from .models.session import SessionContextTable

__all__ = [
    "Base",
    "ALL_MODELS",
    "create_tables_async",
    "drop_tables_async",
]

# Ordered list of ORM-bound models (declarative_base subclasses).
ALL_MODELS = (
    MessageModel,
    RequestAccountingModel,
    SessionContextModel,
    SessionContextTable,
)

_postgresql_naming = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=_postgresql_naming)
metadata.reflect = False  # do not auto-reflect


async def create_tables_async(engine: AsyncEngine) -> None:
    """Create every table represented in *ALL_MODELS*."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_tables_async(engine: AsyncEngine) -> None:
    """Drop every table represented in *ALL_MODELS* (dev only!)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

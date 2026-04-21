import asyncpg
import os
import logging
import json

log = logging.getLogger(__name__)

_pool = None

async def get_pool():
    global _pool
    if not _pool:
        db_url = os.getenv("DATABASE_URL")
        # Ensure correct driver format for asyncpg
        if db_url and "postgresql+asyncpg://" in db_url:
            db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        try:
            _pool = await asyncpg.create_pool(dsn=db_url)
        except Exception as e:
            log.error(f"Postgres connect error: {e}")
    return _pool

async def get_recent_alert_history(limit: int = 5):
    pool = await get_pool()
    if not pool: return []
    query = """
    SELECT symbol, threshold, timestamp 
    FROM alerts 
    ORDER BY timestamp DESC 
    LIMIT $1
    """
    async with pool.acquire() as conn:
        records = await conn.fetch(query, limit)
        return [dict(r) for r in records]

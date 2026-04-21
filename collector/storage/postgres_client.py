import asyncpg
import os
import logging

log = logging.getLogger(__name__)

# Reusing pool
_pool = None

async def get_pool():
    global _pool
    if not _pool:
        db_url = os.getenv("DATABASE_URL")
        # Ensure we connect properly. asyncpg needs postgresql:// instead of postgresql+asyncpg://
        if db_url and "postgresql+asyncpg://" in db_url:
            db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
            
        try:
            _pool = await asyncpg.create_pool(dsn=db_url)
        except Exception as e:
            log.error(f"Postgres connect error: {e}")
            raise
    return _pool

async def save_tickers(tickers: list[dict]):
    """Save ticker data: list of dicts with symbol, price, volume, change_pct"""
    pool = await get_pool()
    query = """
    INSERT INTO market_tickers (symbol, price, volume, change_pct)
    VALUES ($1, $2, $3, $4)
    """
    records = [(t['symbol'], t['price'], t['volume'], t['change_pct']) for t in tickers]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany(query, records)

async def save_alert(symbol: str, threshold: float):
    pool = await get_pool()
    query = """
    INSERT INTO alerts (symbol, threshold, triggered)
    VALUES ($1, $2, TRUE)
    """
    async with pool.acquire() as conn:
        await conn.execute(query, symbol, threshold)

async def cleanup_old_tickers(hours: int = 24):
    """Удалить тикеры старше N часов."""
    pool = await get_pool()
    query = "DELETE FROM market_tickers WHERE timestamp < NOW() - INTERVAL '$1 hours'"
    async with pool.acquire() as conn:
        deleted = await conn.execute(query, hours)
        log.info(f"Cleaned up tickers older than {hours}h")

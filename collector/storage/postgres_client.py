import asyncpg
import os
import logging

log = logging.getLogger(__name__)

_pool = None
PREDICTION_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS forecast_requests (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(15) NOT NULL,
    horizon_hours INT NOT NULL,
    base_price DECIMAL(20, 8) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    source VARCHAR(20) NOT NULL DEFAULT 'webui',
    requested_by VARCHAR(64),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_forecast_requests_created_at ON forecast_requests (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_forecast_requests_symbol_status ON forecast_requests (symbol, status);

CREATE TABLE IF NOT EXISTS forecast_model_runs (
    id SERIAL PRIMARY KEY,
    request_id INT NOT NULL REFERENCES forecast_requests(id) ON DELETE CASCADE,
    model_key VARCHAR(32) NOT NULL,
    model_name VARCHAR(128) NOT NULL,
    model_id VARCHAR(128) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'completed',
    summary TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_forecast_model_runs_request_id ON forecast_model_runs (request_id);

CREATE TABLE IF NOT EXISTS forecast_points (
    id SERIAL PRIMARY KEY,
    request_id INT NOT NULL REFERENCES forecast_requests(id) ON DELETE CASCADE,
    model_run_id INT NOT NULL REFERENCES forecast_model_runs(id) ON DELETE CASCADE,
    forecast_hour INT NOT NULL,
    target_time TIMESTAMP NOT NULL,
    predicted_price DECIMAL(20, 8) NOT NULL,
    predicted_change_pct DECIMAL(10, 4) NOT NULL,
    confidence DECIMAL(5, 2),
    rationale TEXT,
    actual_price DECIMAL(20, 8),
    actual_change_pct DECIMAL(10, 4),
    price_error_pct DECIMAL(10, 4),
    change_error_pct DECIMAL(10, 4),
    accuracy_score DECIMAL(6, 2),
    failure_score DECIMAL(6, 2),
    direction_match BOOLEAN,
    verdict TEXT,
    evaluated_at TIMESTAMP,
    UNIQUE (model_run_id, forecast_hour)
);

CREATE INDEX IF NOT EXISTS idx_forecast_points_target_time ON forecast_points (target_time, evaluated_at);
CREATE INDEX IF NOT EXISTS idx_forecast_points_request_id ON forecast_points (request_id, forecast_hour);
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS failure_score DECIMAL(6, 2);
"""


async def get_pool():
    global _pool
    if not _pool:
        db_url = os.getenv("DATABASE_URL")
        if db_url and "postgresql+asyncpg://" in db_url:
            db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

        try:
            _pool = await asyncpg.create_pool(dsn=db_url)
            async with _pool.acquire() as conn:
                for statement in [item.strip() for item in PREDICTION_TABLES_SQL.split(";") if item.strip()]:
                    await conn.execute(statement)
        except Exception as e:
            log.error(f"Postgres connect error: {e}")
            raise
    return _pool


async def save_tickers(tickers: list[dict]):
    """Save ticker data: list of dicts with symbol, price, volume, change_pct."""
    pool = await get_pool()
    query = """
    INSERT INTO market_tickers (symbol, price, volume, change_pct)
    VALUES ($1, $2, $3, $4)
    """
    records = [(t["symbol"], t["price"], t["volume"], t["change_pct"]) for t in tickers]
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
    """Delete ticker snapshots older than N hours."""
    pool = await get_pool()
    query = f"DELETE FROM market_tickers WHERE timestamp < NOW() - INTERVAL '{int(hours)} hours'"
    async with pool.acquire() as conn:
        await conn.execute(query)
        log.info("Cleaned up tickers older than %sh", hours)


async def get_due_forecast_points(limit: int = 200) -> list[dict]:
    pool = await get_pool()
    query = """
    SELECT
        fp.id AS point_id,
        fr.id AS request_id,
        fr.symbol,
        fr.base_price,
        fp.predicted_price,
        fp.predicted_change_pct,
        fp.target_time
    FROM forecast_points fp
    JOIN forecast_requests fr ON fr.id = fp.request_id
    WHERE fp.evaluated_at IS NULL
      AND fp.target_time <= NOW()
      AND fr.status IN ('active', 'tracking')
    ORDER BY fp.target_time ASC
    LIMIT $1
    """
    async with pool.acquire() as conn:
        records = await conn.fetch(query, limit)
    return [dict(record) for record in records]


async def save_forecast_point_evaluation(
    point_id: int,
    actual_price: float,
    actual_change_pct: float,
    price_error_pct: float,
    change_error_pct: float,
    accuracy_score: float,
    failure_score: float,
    direction_match: bool,
    verdict: str,
):
    pool = await get_pool()
    query = """
    UPDATE forecast_points
    SET
        actual_price = $2,
        actual_change_pct = $3,
        price_error_pct = $4,
        change_error_pct = $5,
        accuracy_score = $6,
        failure_score = $7,
        direction_match = $8,
        verdict = $9,
        evaluated_at = COALESCE(evaluated_at, NOW())
    WHERE id = $1
    """
    async with pool.acquire() as conn:
        await conn.execute(
            query,
            point_id,
            actual_price,
            actual_change_pct,
            price_error_pct,
            change_error_pct,
            accuracy_score,
            failure_score,
            direction_match,
            verdict,
        )


async def get_scored_forecast_points(limit: int = 2000) -> list[dict]:
    pool = await get_pool()
    query = """
    SELECT
        fp.id AS point_id,
        fr.base_price,
        fp.predicted_price,
        fp.predicted_change_pct,
        fp.actual_price
    FROM forecast_points fp
    JOIN forecast_requests fr ON fr.id = fp.request_id
    WHERE fp.actual_price IS NOT NULL
    ORDER BY fp.evaluated_at DESC NULLS LAST, fp.id DESC
    LIMIT $1
    """
    async with pool.acquire() as conn:
        records = await conn.fetch(query, limit)
    return [dict(record) for record in records]


async def finalize_completed_forecast_requests():
    pool = await get_pool()
    query = """
    UPDATE forecast_requests fr
    SET
        status = CASE
            WHEN EXISTS (
                SELECT 1
                FROM forecast_points fp
                WHERE fp.request_id = fr.id
                  AND fp.evaluated_at IS NULL
            ) THEN 'tracking'
            ELSE 'completed'
        END,
        updated_at = NOW()
    WHERE fr.status IN ('active', 'tracking')
    """
    async with pool.acquire() as conn:
        await conn.execute(query)

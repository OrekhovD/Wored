"""Persistent HTX linear-swap (perpetual) candle and funding store.

This module is the single source of truth for price history used by the
self-learning loop.  It consumes the validated :class:`PerpetualCandle` records
produced by :mod:`collector.htx.history_loader` (already venue-correct:
``api.hbdm.com`` linear swap, never spot) and persists them to
``trader_v1_perp_candles``.

Design invariants
-----------------
*  Idempotent: a candle is keyed by a deterministic UUIDv5 of
   ``(venue, contract_code, timeframe, open_time)`` so a collector retry can
   never create a duplicate row (``ON CONFLICT DO NOTHING``).
*  Only ``1min`` is fetched from HTX.  Higher timeframes (15m/1h/4h) are
   derived deterministically from the persisted 1m rows via SQL bucketing, so
   every timeframe agrees with 1m by construction (no independent REST pull).
*  No financial side effects: this is market data only.  Order execution stays
   in ``paper_v2_*``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence
from uuid import UUID, uuid5

import asyncpg

try:  # package-relative when imported as part of the collector package
    from collector.htx.history_loader import PerpetualCandle
except ImportError:  # runtime sys.path layout inside the collector image
    from htx.history_loader import PerpetualCandle  # type: ignore

# Namespace for deterministic candle primary keys.  Stable across processes;
# never change without a data migration, or ids will drift and duplicates appear.
_CANDLE_NAMESPACE = UUID("6f1c1f2e-7b6d-5b9a-9c11-a3d0c4e5f607")
_VENUE = "htx"

# Default cap when republishing a candle window onto Redis (matches HTX page size).
MAX_CANDLE_WINDOW = 2000

# Derived timeframes (built from persisted 1m rows), bucket size in minutes.
_DERIVED_PERIODS: dict[str, int] = {
    "15min": 15,
    "60min": 60,
    "4hour": 240,
}

# Public list of timeframes rebuilt from stored 1m rows (never fetched from HTX).
DERIVED_TIMEFRAMES: tuple[str, ...] = tuple(_DERIVED_PERIODS)

TRADER_V1_PERP_CANDLES_DDL = """
CREATE TABLE IF NOT EXISTS trader_v1_perp_candles (
    candle_id UUID PRIMARY KEY,
    venue VARCHAR(24) NOT NULL DEFAULT 'htx',
    contract_code VARCHAR(32) NOT NULL,
    timeframe VARCHAR(12) NOT NULL,
    open_time TIMESTAMPTZ NOT NULL,
    close_time TIMESTAMPTZ NOT NULL,
    open NUMERIC(20,8) NOT NULL,
    high NUMERIC(20,8) NOT NULL,
    low NUMERIC(20,8) NOT NULL,
    close NUMERIC(20,8) NOT NULL,
    volume NUMERIC(28,8) NOT NULL,
    source VARCHAR(48) NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (venue, contract_code, timeframe, open_time),
    CHECK (high >= low),
    CHECK (open_time < close_time)
);
CREATE INDEX IF NOT EXISTS idx_trader_v1_candles_lookup
    ON trader_v1_perp_candles (contract_code, timeframe, open_time DESC);
"""

TRADER_V1_FUNDING_RATE_DDL = """
CREATE TABLE IF NOT EXISTS trader_v1_funding_rate (
    funding_id UUID PRIMARY KEY,
    venue VARCHAR(24) NOT NULL DEFAULT 'htx',
    contract_code VARCHAR(32) NOT NULL,
    funding_time TIMESTAMPTZ NOT NULL,
    funding_rate NUMERIC(20,12) NOT NULL,
    predicted_rate NUMERIC(20,12),
    source VARCHAR(48) NOT NULL DEFAULT 'htx-linear-swap-rest',
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (venue, contract_code, funding_time)
);
CREATE INDEX IF NOT EXISTS idx_trader_v1_funding_lookup
    ON trader_v1_funding_rate (contract_code, funding_time DESC);
"""


def candle_uuid(contract_code: str, timeframe: str, open_time: datetime) -> UUID:
    """Deterministic primary key so re-ingest is a no-op, never a duplicate."""
    key = f"{_VENUE}|{contract_code}|{timeframe}|{open_time.astimezone(timezone.utc).isoformat()}"
    return uuid5(_CANDLE_NAMESPACE, key)


def funding_uuid(contract_code: str, funding_time: datetime) -> UUID:
    key = f"{_VENUE}|{contract_code}|{funding_time.astimezone(timezone.utc).isoformat()}"
    return uuid5(_CANDLE_NAMESPACE, key)


async def ensure_perp_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(TRADER_V1_PERP_CANDLES_DDL)
        await conn.execute(TRADER_V1_FUNDING_RATE_DDL)


async def bulk_upsert_candles(
    pool: asyncpg.Pool, candles: Sequence[PerpetualCandle]
) -> int:
    """Insert validated closed candles; existing rows are skipped.

    Returns the number of *newly inserted* rows.
    """
    if not candles:
        return 0
    records = [
        (
            candle_uuid(c.contract_code, c.period, c.open_time),
            _VENUE,
            c.contract_code,
            c.period,
            c.open_time,
            c.close_time,
            Decimal(str(c.open)),
            Decimal(str(c.high)),
            Decimal(str(c.low)),
            Decimal(str(c.close)),
            Decimal(str(c.volume)),
            c.source,
        )
        for c in candles
    ]
    inserted = 0
    async with pool.acquire() as conn:
        for rec in records:
            tag = await conn.execute(
                """
                INSERT INTO trader_v1_perp_candles
                    (candle_id, venue, contract_code, timeframe, open_time, close_time,
                     open, high, low, close, volume, source)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (venue, contract_code, timeframe, open_time) DO NOTHING
                """,
                *rec,
            )
            # tag == "INSERT 0 1" for a new row, "INSERT 0 0" when skipped.
            if tag.endswith(" 1"):
                inserted += 1
    return inserted


async def aggregate_timeframe(
    pool: asyncpg.Pool, contract_code: str, timeframe: str
) -> int:
    """Derive ``timeframe`` from persisted 1m rows via SQL bucketing.

    Higher TFs are never fetched from HTX; they are recomputed from 1m so they
    agree with the base series by construction.  Rows are written with the same
    deterministic :func:`candle_uuid` used for 1m, so re-running is idempotent.
    Returns the number of buckets stored.
    """
    minutes = _DERIVED_PERIODS.get(timeframe)
    if minutes is None:
        raise ValueError(f"unsupported derived timeframe: {timeframe}")
    bucket_seconds = minutes * 60
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT
                to_timestamp(floor(extract(epoch FROM open_time) / {bucket_seconds}) * {bucket_seconds})
                    AT TIME ZONE 'UTC' AS bucket,
                (array_agg(open ORDER BY open_time))[1]                       AS open,
                max(high)                                                     AS high,
                min(low)                                                      AS low,
                (array_agg(close ORDER BY close_time DESC))[1]                AS close,
                sum(volume)                                                   AS volume
            FROM trader_v1_perp_candles
            WHERE venue = $1 AND contract_code = $2 AND timeframe = '1min'
            GROUP BY 1
            ORDER BY 1
            """,
            _VENUE,
            contract_code,
        )
    stored = 0
    for r in rows:
        bucket = r["bucket"]
        if bucket.tzinfo is None:
            bucket = bucket.replace(tzinfo=timezone.utc)
        open_time = bucket
        close_time = bucket + timedelta(minutes=minutes)
        async with pool.acquire() as conn:
            tag = await conn.execute(
                """
                INSERT INTO trader_v1_perp_candles
                    (candle_id, venue, contract_code, timeframe, open_time, close_time,
                     open, high, low, close, volume, source)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'derived-from-1min')
                ON CONFLICT (venue, contract_code, timeframe, open_time)
                DO UPDATE SET open = EXCLUDED.open, high = EXCLUDED.high,
                              low = EXCLUDED.low, close = EXCLUDED.close,
                              volume = EXCLUDED.volume
                """,
                candle_uuid(contract_code, timeframe, open_time),
                _VENUE, contract_code, timeframe, open_time, close_time,
                r["open"], r["high"], r["low"], r["close"], r["volume"],
            )
        if tag.endswith(" 1"):
            stored += 1
    return stored


async def upsert_funding_rate(
    pool: asyncpg.Pool,
    contract_code: str,
    funding_time: datetime,
    funding_rate: Decimal,
    predicted_rate: Decimal | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO trader_v1_funding_rate
                (funding_id, venue, contract_code, funding_time, funding_rate, predicted_rate)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (venue, contract_code, funding_time)
            DO UPDATE SET funding_rate = EXCLUDED.funding_rate,
                          predicted_rate = COALESCE(EXCLUDED.predicted_rate,
                                                    trader_v1_funding_rate.predicted_rate)
            """,
            funding_uuid(contract_code, funding_time), _VENUE, contract_code,
            funding_time, Decimal(str(funding_rate)),
            None if predicted_rate is None else Decimal(str(predicted_rate)),
        )


async def count_rows(pool: asyncpg.Pool, contract_code: str, timeframe: str) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM trader_v1_perp_candles "
            "WHERE venue=$1 AND contract_code=$2 AND timeframe=$3",
            _VENUE, contract_code, timeframe,
        )


async def count_gaps(
    pool: asyncpg.Pool,
    contract_code: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> int:
    """Number of missing buckets in ``[start, end)`` for the given timeframe."""
    minutes = 1 if timeframe == "1min" else _DERIVED_PERIODS.get(timeframe)
    if minutes is None:
        raise ValueError(f"unsupported timeframe: {timeframe}")
    async with pool.acquire() as conn:
        return await conn.fetchval(
            f"""
            WITH expected AS (
                SELECT generate_series(
                    $4::timestamptz,
                    ($5::timestamptz - interval '{minutes} minutes'),
                    interval '{minutes} minutes'
                ) AS bucket
            )
            SELECT count(*) FROM expected e
            LEFT JOIN trader_v1_perp_candles c
              ON c.venue = $1 AND c.contract_code = $2 AND c.timeframe = $3
             AND c.open_time = e.bucket
            WHERE c.candle_id IS NULL
            """,
            _VENUE, contract_code, timeframe, start, end,
        )


async def load_candles(
    pool: asyncpg.Pool,
    contract_code: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT open_time, close_time, open, high, low, close, volume
              FROM trader_v1_perp_candles
             WHERE venue = $1 AND contract_code = $2 AND timeframe = $3
               AND open_time >= $4 AND open_time < $5
             ORDER BY open_time ASC
            """,
            _VENUE, contract_code, timeframe, start, end,
        )
    return [dict(r) for r in rows]


async def load_recent_candles(
    pool: asyncpg.Pool,
    contract_code: str,
    timeframe: str,
    limit: int = MAX_CANDLE_WINDOW,
) -> list[PerpetualCandle]:
    """Return the newest ``limit`` stored candles (chronological order).

    Used to republish derived timeframes back onto the Redis candle channel
    the runner already reads, without a second HTX request.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT contract_code, timeframe, open_time, close_time,
                   open, high, low, close, volume, source
              FROM trader_v1_perp_candles
             WHERE venue = $1 AND contract_code = $2 AND timeframe = $3
             ORDER BY open_time DESC
             LIMIT $4
            """,
            _VENUE, contract_code, timeframe, limit,
        )
    candles = [
        PerpetualCandle(
            contract_code=r["contract_code"],
            period=r["timeframe"],
            open_time=r["open_time"],
            close_time=r["close_time"],
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
            volume=r["volume"],
            source=r["source"],
        )
        for r in rows
    ]
    return sorted(candles, key=lambda item: item.open_time)


async def dataset_sha256(
    pool: asyncpg.Pool,
    contract_code: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> str:
    """Stable content hash over a candle window (acceptance criterion A)."""
    import hashlib

    rows = await load_candles(pool, contract_code, timeframe, start, end)
    h = hashlib.sha256()
    for r in rows:
        h.update(r["open_time"].astimezone(timezone.utc).isoformat().encode())
        for field in ("open", "high", "low", "close", "volume"):
            h.update(format(Decimal(str(r[field])), "f").encode())
    return h.hexdigest()

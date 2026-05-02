#!/usr/bin/env python3

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncpg
import httpx
import redis.asyncio as redis
from pydantic import BaseModel

# --- CONFIG ---
HTX_REST_URL = "https://api.huobi.pro"
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_LIMIT = 2000
DEFAULT_PERIOD = "60min"
DEFAULT_SYMBOL = "BTCUSDT"

# --- MODELS ---
class Candle(BaseModel):
    symbol: str
    period: str
    open_time: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str

# --- UTILS ---
def parse_period(period: str) -> str:
    mapping = {
        "1min": "1min",
        "5min": "5min",
        "15min": "15min",
        "30min": "30min",
        "60min": "60min",
        "4hour": "4hour",
        "1day": "1day",
    }
    if period not in mapping:
        raise ValueError(f"Unsupported period '{period}'")
    return mapping[period]

def parse_iso_date(date_str: str) -> datetime:
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"Invalid ISO date: {date_str}")

def get_time_range(
    lookback_days: int | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    limit: int | None = None,
) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    if from_date and to_date:
        return from_date, to_date
    if from_date:
        return from_date, now
    if to_date:
        return to_date - timedelta(days=lookback_days or DEFAULT_LOOKBACK_DAYS), to_date
    if lookback_days is None:
        lookback_days = DEFAULT_LOOKBACK_DAYS
    from_dt = now - timedelta(days=lookback_days)
    return from_dt, now

# --- HTX CLIENT ---
async def fetch_htx_candles(
    client: httpx.AsyncClient,
    symbol: str,
    period: str,
    from_ts: int,
    to_ts: int,
    limit: int = DEFAULT_LIMIT,
) -> list[dict]:
    params = {
        "symbol": symbol.lower(),
        "period": period,
        "from": str(from_ts),
        "to": str(to_ts),
        "size": str(limit),
    }
    try:
        response = await client.get(f"{HTX_REST_URL}/market/history/kline", params=params)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "ok":
            raise RuntimeError(f"HTX error: {payload.get('status')} {payload.get('msg', '')}")
        raw_klines = payload.get("data", [])
        candles = []
        for item in raw_klines:
            # HTX format: [id, open, high, low, close, vol, amount]
            # id = timestamp in ms
            ts_ms = item[0]
            open_time = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
            candles.append(
                {
                    "symbol": symbol.upper(),
                    "period": period,
                    "open_time": open_time,
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                    "source": "htx_rest",
                }
            )
        return candles
    except Exception as exc:
        raise RuntimeError(f"HTX fetch failed: {exc}")

# --- REDIS CACHE ---
async def cache_to_redis(
    redis_client: redis.Redis,
    symbol: str,
    period: str,
    candles: list[dict],
    ttl_seconds: int = 3600,
):
    key = f"candles:{symbol}:{period}"
    try:
        await redis_client.delete(key)
        await redis_client.setex(key, ttl_seconds, json.dumps(candles))
        logging.info(f"Cached {len(candles)} candles to Redis key {key} (TTL={ttl_seconds}s)")
    except Exception as exc:
        raise RuntimeError(f"Redis cache failed: {exc}")

# --- POSTGRES STORE ---
async def store_to_postgres(
    pool: asyncpg.Pool,
    symbol: str,
    period: str,
    candles: list[dict],
):
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS candles (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(15) NOT NULL,
                    period VARCHAR(10) NOT NULL,
                    open_time TIMESTAMP WITH TIME ZONE NOT NULL,
                    open DECIMAL(20, 8) NOT NULL,
                    high DECIMAL(20, 8) NOT NULL,
                    low DECIMAL(20, 8) NOT NULL,
                    close DECIMAL(20, 8) NOT NULL,
                    volume DECIMAL(20, 8) NOT NULL,
                    source VARCHAR(32) NOT NULL DEFAULT 'htx_rest',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            await conn.executemany(
                """
                INSERT INTO candles (symbol, period, open_time, open, high, low, close, volume, source)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9);
                """,
                [
                    (
                        c["symbol"],
                        c["period"],
                        c["open_time"],
                        c["open"],
                        c["high"],
                        c["low"],
                        c["close"],
                        c["volume"],
                        c["source"],
                    )
                    for c in candles
                ],
            )
        logging.info(f"Stored {len(candles)} candles to Postgres")
    except Exception as exc:
        raise RuntimeError(f"Postgres store failed: {exc}")

# --- MAIN ---
async def main():
    parser = argparse.ArgumentParser(description="Fetch market history on demand")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Symbol (e.g., BTCUSDT)")
    parser.add_argument("--period", default=DEFAULT_PERIOD, help="Period (1min, 5min, ..., 1day)")
    parser.add_argument("--from", dest="from_date", type=parse_iso_date, help="ISO start date")
    parser.add_argument("--to", dest="to_date", type=parse_iso_date, help="ISO end date")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="Days to look back")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Max candles")
    parser.add_argument(
        "--mode",
        choices=["preview", "json", "cache", "store"],
        default="preview",
        help="Execution mode",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    # Get time range
    from_dt, to_dt = get_time_range(
        lookback_days=args.lookback_days,
        from_date=args.from_date,
        to_date=args.to_date,
        limit=args.limit,
    )

    # Convert to timestamps (ms)
    from_ts = int(from_dt.timestamp() * 1000)
    to_ts = int(to_dt.timestamp() * 1000)

    # Fetch candles
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        try:
            candles = await fetch_htx_candles(
                http_client,
                args.symbol,
                parse_period(args.period),
                from_ts,
                to_ts,
                args.limit,
            )
        except Exception as exc:
            logging.error(f"❌ Failed to fetch candles: {exc}")
            sys.exit(1)

    # Output
    if args.mode == "preview":
        print(f"✅ Preview: {len(candles)} candles for {args.symbol} {args.period} from {from_dt.date()} to {to_dt.date()}")
        print(f"   First: {candles[0]['open_time'] if candles else 'none'}")
        print(f"   Last:  {candles[-1]['open_time'] if candles else 'none'}")
        return

    if args.mode == "json":
        print(json.dumps(candles, indent=2, ensure_ascii=False))
        return

    # Load Redis & Postgres clients
    redis_url = "redis://localhost:6379/0"
    postgres_url = "postgresql://bot:password@localhost:5432/trading"

    if args.mode == "cache":
        redis_client = redis.from_url(redis_url)
        try:
            await cache_to_redis(redis_client, args.symbol, args.period, candles)
            print(f"✅ Cached {len(candles)} candles to Redis")
        finally:
            await redis_client.close()
        return

    if args.mode == "store":
        # WARNING: only run if explicitly requested
        if not input("⚠️  Confirm write to Postgres? (y/N): ").lower().startswith("y"):
            try:
                pool = await asyncpg.create_pool(postgres_url)
                await store_to_postgres(pool, args.symbol, args.period, candles)
                await pool.close()
                print(f"✅ Stored {len(candles)} candles to Postgres")
            except Exception as exc:
                logging.error(f"❌ Store failed: {exc}")
                sys.exit(1)
        else:
            print("❌ Cancelled by user.")
        return


if __name__ == "__main__":
    asyncio.run(main())

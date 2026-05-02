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
DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_PERIOD = "60min"
DEFAULT_SYMBOL = "BTCUSDT"
DEFAULT_PATTERN = "volume_spike"
DEFAULT_FORWARD_HOURS = 8
DEFAULT_GROUP_BY = "none"

# --- MODELS ---
class PatternLabResult(BaseModel):
    symbol: str
    period: str
    pattern: str
    forward_hours: int
    group_by: str
    samples: int
    avg_forward_change_pct: float | None
    median_forward_change_pct: float | None
    win_rate_up: float | None
    win_rate_down: float | None
    max_forward_gain_pct: float | None
    max_forward_drawdown_pct: float | None
    stddev_forward_change: float | None
    best_group: str | None
    worst_group: str | None

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
    limit: int = 2000,
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

# --- MAIN ---
async def main():
    parser = argparse.ArgumentParser(description="Pattern Lab: research market patterns")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Symbol (e.g., BTCUSDT)")
    parser.add_argument("--period", default=DEFAULT_PERIOD, help="Period (1min, 5min, ..., 1day)")
    parser.add_argument("--pattern", default=DEFAULT_PATTERN, help="Pattern type")
    parser.add_argument("--forward-hours", type=int, default=DEFAULT_FORWARD_HOURS, help="Hours to observe after pattern")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="Days to search back")
    parser.add_argument("--group-by", choices=["none", "hour_of_day", "day_of_week", "session", "month", "volatility_regime"], default=DEFAULT_GROUP_BY, help="Group analysis by")
    parser.add_argument("--from", dest="from_date", type=parse_iso_date, help="ISO start date")
    parser.add_argument("--to", dest="to_date", type=parse_iso_date, help="ISO end date")
    parser.add_argument("--format", choices=["json", "markdown"], default="json", help="Output format")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    # Get time range
    from_dt, to_dt = get_time_range(
        lookback_days=args.lookback_days,
        from_date=args.from_date,
        to_date=args.to_date,
    )

    # Convert to timestamps (ms)
    from_ts = int(from_dt.timestamp() * 1000)
    to_ts = int(to_dt.timestamp() * 1000)

    # Load candles
    candles = []
    try:
        pool = await asyncpg.create_pool("postgresql://bot:password@localhost:5432/trading")
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM candles WHERE symbol = $1 AND period = $2 AND open_time BETWEEN $3 AND $4 ORDER BY open_time ASC",
                args.symbol,
                args.period,
                from_dt,
                to_dt,
            )
            candles = [
                {
                    "symbol": row["symbol"],
                    "period": row["period"],
                    "open_time": row["open_time"].isoformat(),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "source": row["source"],
                }
                for row in rows
            ]
        await pool.close()
        logging.info(f"Loaded {len(candles)} candles from Postgres")
    except Exception as exc:
        logging.warning(f"Postgres load failed: {exc}. Falling back to HTX.")

    # If empty, fall back to HTX
    if not candles:
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            try:
                candles = await fetch_htx_candles(
                    http_client,
                    args.symbol,
                    parse_period(args.period),
                    from_ts,
                    to_ts,
                )
                logging.info(f"Fetched {len(candles)} candles from HTX")
            except Exception as exc:
                logging.error(f"❌ Failed to fetch from HTX: {exc}")
                sys.exit(1)

    # Detect pattern occurrences
    pattern_times = []
    if args.pattern == "volume_spike":
        # Volume spike: volume > 2× average over last 24h
        if len(candles) > 24:
            avg_vol = sum(c["volume"] for c in candles[-24:]) / 24
            for i, candle in enumerate(candles[:-1]):
                if candle["volume"] > avg_vol * 2.0:
                    pattern_times.append(candle["open_time"])
    elif args.pattern == "rsi_overbought":
        # RSI > 70
        # TODO: compute RSI first
        pass
    elif args.pattern == "rsi_oversold":
        # RSI < 30
        pass
    else:
        logging.warning(f"⚠️  Pattern '{args.pattern}' not implemented yet.")

    if not pattern_times:
        result = {
            "symbol": args.symbol,
            "period": args.period,
            "pattern": args.pattern,
            "forward_hours": args.forward_hours,
            "group_by": args.group_by,
            "samples": 0,
            "avg_forward_change_pct": None,
            "median_forward_change_pct": None,
            "win_rate_up": None,
            "win_rate_down": None,
            "max_forward_gain_pct": None,
            "max_forward_drawdown_pct": None,
            "stddev_forward_change": None,
            "best_group": None,
            "worst_group": None,
            "summary": "insufficient_data"
        }
    else:
        # Compute forward changes
        forward_changes = []
        for t in pattern_times:
            t_dt = datetime.fromisoformat(t)
            forward_dt = t_dt + timedelta(hours=args.forward_hours)
            # Find next candle at forward_dt ± 30m
            target_candle = None
            for c in candles:
                c_dt = datetime.fromisoformat(c["open_time"])
                if abs((c_dt - forward_dt).total_seconds()) <= 1800:
                    target_candle = c
                    break
            if target_candle:
                base_close = candles[candles.index(target_candle) - 1]["close"]
                change_pct = ((target_candle["close"] - base_close) / base_close) * 100
                forward_changes.append(change_pct)

        if not forward_changes:
            result = {
                "symbol": args.symbol,
                "period": args.period,
                "pattern": args.pattern,
                "forward_hours": args.forward_hours,
                "group_by": args.group_by,
                "samples": len(pattern_times),
                "avg_forward_change_pct": None,
                "median_forward_change_pct": None,
                "win_rate_up": None,
                "win_rate_down": None,
                "max_forward_gain_pct": None,
                "max_forward_drawdown_pct": None,
                "stddev_forward_change": None,
                "best_group": None,
                "worst_group": None,
                "summary": "no_forward_candles_found"
            }
        else:
            import statistics
            avg = round(statistics.mean(forward_changes), 4)
            median = round(statistics.median(forward_changes), 4)
            up_count = sum(1 for x in forward_changes if x > 0)
            down_count = sum(1 for x in forward_changes if x < 0)
            win_rate_up = round(up_count / len(forward_changes) * 100, 2) if forward_changes else None
            win_rate_down = round(down_count / len(forward_changes) * 100, 2) if forward_changes else None
            max_gain = round(max(forward_changes), 4) if forward_changes else None
            max_dd = round(min(forward_changes), 4) if forward_changes else None
            std = round(statistics.stdev(forward_changes), 4) if len(forward_changes) > 1 else 0.0

            result = {
                "symbol": args.symbol,
                "period": args.period,
                "pattern": args.pattern,
                "forward_hours": args.forward_hours,
                "group_by": args.group_by,
                "samples": len(pattern_times),
                "avg_forward_change_pct": avg,
                "median_forward_change_pct": median,
                "win_rate_up": win_rate_up,
                "win_rate_down": win_rate_down,
                "max_forward_gain_pct": max_gain,
                "max_forward_drawdown_pct": max_dd,
                "stddev_forward_change": std,
                "best_group": None,
                "worst_group": None,
                "summary": f"{len(pattern_times)} occurrences found. Avg forward change: {avg}%"
            }

    # Output
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.format == "markdown":
        print(f"# Pattern Lab: {args.symbol} {args.pattern} → {args.forward_hours}h\n")
        print(f"Lookback: {from_dt.date()} → {to_dt.date()}\n")
        print(f"## Summary\n{result['summary']}\n")
        if result["samples"] > 0:
            print("## Metrics\n")
            print(f"- Samples: {result['samples']}")
            print(f"- Avg forward change: {result['avg_forward_change_pct']}%")
            print(f"- Median forward change: {result['median_forward_change_pct']}%")
            print(f"- Win rate up: {result['win_rate_up']}%")
            print(f"- Win rate down: {result['win_rate_down']}%")
            print(f"- Max gain: {result['max_forward_gain_pct']}%")
            print(f"- Max drawdown: {result['max_forward_drawdown_pct']}%")
            print(f"- Stddev: {result['stddev_forward_change']}%")


if __name__ == "__main__":
    asyncio.run(main())

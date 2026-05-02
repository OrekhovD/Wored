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
DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_PERIOD = "60min"
DEFAULT_SYMBOL = "BTCUSDT"

# --- MODELS ---
class MarketContext(BaseModel):
    symbol: str
    period: str
    range: dict[str, str]
    data_source: dict[str, int | str]
    latest: dict[str, float | str]
    patterns: list[dict[str, str | float]]
    summary: str

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

# --- INDICATORS ---
def compute_sma(candles: list[dict], window: int) -> list[float]:
    sma: list[float] = []
    rolling = []
    total = 0.0
    for candle in candles:
        close = candle.get("close", 0.0)
        rolling.append(close)
        total += close
        if len(rolling) > window:
            total -= rolling.pop(0)
        if len(rolling) == window:
            sma.append(round(total / window, 6))
    return sma

def compute_rsi(candles: list[dict], period: int = 14) -> list[float]:
    if len(candles) <= period:
        return []
    closes = [c.get("close", 0.0) for c in candles]
    gains = []
    losses = []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi_values = []
    def rsi(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0
        rs = gain / loss
        return 100.0 - (100.0 / (1.0 + rs))
    rsi_values.append(round(rsi(avg_gain, avg_loss), 4))
    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period
        rsi_values.append(round(rsi(avg_gain, avg_loss), 4))
    return rsi_values

def compute_macd(candles: list[dict]) -> dict[str, list[float]]:
    closes = [c.get("close", 0.0) for c in candles]
    ema_fast = compute_ema(closes, 12)
    ema_slow = compute_ema(closes, 26)
    macd_values = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_values = compute_ema(macd_values, 9)
    histogram_values = [m - s for m, s in zip(macd_values, signal_values)]
    return {
        "macd": macd_values,
        "signal": signal_values,
        "histogram": histogram_values,
    }

def compute_ema(values: list[float], span: int) -> list[float]:
    multiplier = 2.0 / (span + 1)
    output = []
    ema_value = None
    for value in values:
        ema_value = value if ema_value is None else (value - ema_value) * multiplier + ema_value
        output.append(ema_value)
    return output

def compute_volume_avg(candles: list[dict]) -> float:
    volumes = [c.get("volume", 0.0) for c in candles]
    return round(sum(volumes) / len(volumes), 3) if volumes else 0.0

def compute_volatility(candles: list[dict]) -> float:
    closes = [c.get("close", 0.0) for c in candles]
    if len(closes) < 2:
        return 0.0
    mean = sum(closes) / len(closes)
    variance = sum((x - mean) ** 2 for x in closes) / len(closes)
    return round(variance ** 0.5, 6)

def compute_max_drawdown(candles: list[dict]) -> float:
    if not candles:
        return 0.0
    highs = [c.get("high", 0.0) for c in candles]
    peak = highs[0]
    max_dd = 0.0
    for high in highs:
        if high > peak:
            peak = high
        dd = (peak - high) / peak * 100.0 if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return round(max_dd, 4)

def compute_trend_direction(candles: list[dict]) -> str:
    if not candles:
        return "unknown"
    first = candles[0].get("open", 0.0)
    last = candles[-1].get("close", 0.0)
    if last > first * 1.02:
        return "trend_up"
    if last < first * 0.98:
        return "trend_down"
    return "sideways"

def detect_patterns(candles: list[dict], sma20: list[float], sma50: list[float], rsi: list[float], macd: dict) -> list[dict]:
    patterns = []
    now = datetime.now(timezone.utc)
    # Volume spike
    if len(candles) > 1:
        avg_vol = compute_volume_avg(candles[:-1])
        latest_vol = candles[-1].get("volume", 0.0)
        if latest_vol > avg_vol * 2.0:
            patterns.append({
                "type": "volume_spike",
                "time": candles[-1].get("open_time", now.isoformat()),
                "strength": round(latest_vol / avg_vol, 2)
            })
    # RSI overbought/oversold
    if rsi and rsi[-1] > 70:
        patterns.append({
            "type": "rsi_overbought",
            "time": candles[-1].get("open_time", now.isoformat()),
            "strength": rsi[-1]
        })
    if rsi and rsi[-1] < 30:
        patterns.append({
            "type": "rsi_oversold",
            "time": candles[-1].get("open_time", now.isoformat()),
            "strength": rsi[-1]
        })
    # SMA cross
    if len(sma20) >= 2 and len(sma50) >= 2:
        if sma20[-2] <= sma50[-2] and sma20[-1] > sma50[-1]:
            patterns.append({
                "type": "sma_bull_cross",
                "time": candles[-1].get("open_time", now.isoformat())
            })
        if sma20[-2] >= sma50[-2] and sma20[-1] < sma50[-1]:
            patterns.append({
                "type": "sma_bear_cross",
                "time": candles[-1].get("open_time", now.isoformat())
            })
    # MACD cross
    if macd.get("macd") and macd.get("signal"):
        macd_vals = macd["macd"]
        signal_vals = macd["signal"]
        if len(macd_vals) >= 2 and len(signal_vals) >= 2:
            if macd_vals[-2] <= signal_vals[-2] and macd_vals[-1] > signal_vals[-1]:
                patterns.append({
                    "type": "macd_bull_cross",
                    "time": candles[-1].get("open_time", now.isoformat())
                })
            if macd_vals[-2] >= signal_vals[-2] and macd_vals[-1] < signal_vals[-1]:
                patterns.append({
                    "type": "macd_bear_cross",
                    "time": candles[-1].get("open_time", now.isoformat())
                })
    return patterns

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
    parser = argparse.ArgumentParser(description="Fetch market context")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Symbol (e.g., BTCUSDT)")
    parser.add_argument("--period", default=DEFAULT_PERIOD, help="Period (1min, 5min, ..., 1day)")
    parser.add_argument("--from", dest="from_date", type=parse_iso_date, help="ISO start date")
    parser.add_argument("--to", dest="to_date", type=parse_iso_date, help="ISO end date")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="Days to look back")
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

    # Try local Postgres first
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

    # Compute indicators
    sma20 = compute_sma(candles, 20)
    sma50 = compute_sma(candles, 50)
    rsi14 = compute_rsi(candles, 14)
    macd_payload = compute_macd(candles)
    volume_avg = compute_volume_avg(candles)
    volatility = compute_volatility(candles)
    drawdown = compute_max_drawdown(candles)
    trend = compute_trend_direction(candles)

    # Detect patterns
    patterns = detect_patterns(candles, sma20, sma50, rsi14, macd_payload)

    # Build context
    if not candles:
        context = {
            "symbol": args.symbol,
            "period": args.period,
            "range": {"from": from_dt.isoformat(), "to": to_dt.isoformat()},
            "data_source": {"local_candles": 0, "fetched_from_htx": 0, "mode": "on_demand"},
            "latest": {},
            "patterns": [],
            "summary": "insufficient_data"
        }
    else:
        latest = candles[-1]
        context = {
            "symbol": args.symbol,
            "period": args.period,
            "range": {"from": from_dt.isoformat(), "to": to_dt.isoformat()},
            "data_source": {
                "local_candles": len(candles),
                "fetched_from_htx": 0,
                "mode": "on_demand"
            },
            "latest": {
                "close": latest.get("close", 0.0),
                "rsi14": rsi14[-1] if rsi14 else 0.0,
                "macd": macd_payload["macd"][-1] if macd_payload["macd"] else 0.0,
                "macd_signal": macd_payload["signal"][-1] if macd_payload["signal"] else 0.0,
                "sma20": sma20[-1] if sma20 else 0.0,
                "sma50": sma50[-1] if sma50 else 0.0,
            },
            "patterns": patterns,
            "summary": f"{args.symbol} had {trend} trend with {len(patterns)} detected patterns."
        }

    # Output
    if args.format == "json":
        print(json.dumps(context, indent=2, ensure_ascii=False))
    elif args.format == "markdown":
        print(f"# Market Context: {args.symbol} {args.period}\n")
        print(f"Range: {from_dt.date()} → {to_dt.date()}\n")
        print(f"Source: local {len(candles)} candles, HTX {len(candles)} candles\n")
        print("## Latest\n")
        for k, v in context["latest"].items():
            if isinstance(v, float):
                print(f"- {k}: {v:.4f}")
            else:
                print(f"- {k}: {v}")
        if context["patterns"]:
            print("\n## Patterns\n")
            for p in context["patterns"]:
                t = p.get("time", "unknown")
                strength = p.get("strength", "")
                if strength:
                    print(f"- {t}: {p['type']} x{strength}")
                else:
                    print(f"- {t}: {p['type']}")
        print(f"\n## Summary\n{context['summary']}")


if __name__ == "__main__":
    asyncio.run(main())

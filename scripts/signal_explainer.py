#!/usr/bin/env python3

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- CONFIG ---
HTX_REST_URL = "https://api.huobi.pro"
DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_PERIOD = "60min"
DEFAULT_SYMBOL = "BTCUSDT"

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

# --- INDICATORS (copied & simplified from pattern_lab.py) ---
def compute_rsi(candles, period=14):
    if len(candles) < period + 1:
        return [None] * len(candles)
    rsi = [None] * len(candles)
    gains = []
    losses = []
    for i in range(1, period + 1):
        change = candles[i]["close"] - candles[i-1]["close"]
        gains.append(max(0, change))
        losses.append(max(0, -change))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        rsi[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100.0 - (100.0 / (1 + rs))
    for i in range(period + 1, len(candles)):
        change = candles[i]["close"] - candles[i-1]["close"]
        gain = max(0, change)
        loss = max(0, -change)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100.0 - (100.0 / (1 + rs))
    return rsi

def compute_sma(candles, period):
    if len(candles) < period:
        return [None] * len(candles)
    sma = [None] * len(candles)
    for i in range(period - 1, len(candles)):
        window = [c["close"] for c in candles[i - period + 1:i + 1]]
        sma[i] = sum(window) / len(window)
    return sma

def compute_macd(candles, fast=12, slow=26, signal=9):
    if len(candles) < slow:
        return {"macd": [None]*len(candles), "signal": [None]*len(candles), "hist": [None]*len(candles)}
    fast_sma = compute_sma(candles, fast)
    slow_sma = compute_sma(candles, slow)
    macd_line = [None] * len(candles)
    for i in range(len(candles)):
        if fast_sma[i] is not None and slow_sma[i] is not None:
            macd_line[i] = fast_sma[i] - slow_sma[i]
    signal_line = compute_sma([{"close": v} for v in macd_line if v is not None], signal)
    padded_signal = [None] * len(candles)
    if len(signal_line) > 0:
        padded_signal[-len(signal_line):] = signal_line
    hist = [None] * len(candles)
    for i in range(len(candles)):
        if macd_line[i] is not None and padded_signal[i] is not None:
            hist[i] = macd_line[i] - padded_signal[i]
    return {"macd": macd_line, "signal": padded_signal, "hist": hist}

# --- HTX CLIENT ---
async def fetch_htx_candles(
    client,
    symbol: str,
    period: str,
    from_ts: int,
    to_ts: int,
    limit: int = 2000,
) -> list[dict]:
    params = {
        "symbol": symbol.lower(),
        "period": period,
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
            open_time = datetime.fromtimestamp(item["id"], tz=timezone.utc).isoformat()
            candles.append(
                {
                    "symbol": symbol.upper(),
                    "period": period,
                    "open_time": open_time,
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                    "volume": float(item["vol"]),
                    "source": "htx_rest",
                }
            )
        candles.reverse()
        return candles
    except Exception as exc:
        raise RuntimeError(f"HTX fetch failed: {exc}")

# --- MAIN ---
async def main():
    parser = argparse.ArgumentParser(description="Signal Explainer: explain market state (bullish/bearish/neutral)")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help="Symbol (e.g., BTCUSDT)")
    parser.add_argument("--period", default=DEFAULT_PERIOD, help="Period (1min, 5min, ..., 1day)")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="Days to search back")
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
        import asyncpg
        pool = await asyncpg.create_pool("postgresql://bot:sOH9yRjRBfFeD9W0ALOFxSm24tpiQAhK@localhost:5432/trading")
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
    except ImportError as exc:
        logging.warning(f"Postgres load skipped: asyncpg not installed ({exc})")
    except Exception as exc:
        logging.warning(f"Postgres load failed: {exc}. Falling back to HTX.")

    # If empty, fall back to HTX
    if not candles:
        try:
            import httpx
        except ImportError as exc:
            logging.warning(f"HTX fallback skipped: httpx not installed ({exc})")
            result = {
                "symbol": args.symbol,
                "period": args.period,
                "summary": "insufficient_data",
                "reasons": [],
                "risks": [],
                "secrets_printed": False,
            }
            if args.format == "json":
                print(json.dumps(result, indent=2, ensure_ascii=False))
            elif args.format == "markdown":
                print(f"# Signal Explainer: {args.symbol} {args.period}\n")
                print("## Summary\ninsufficient_data\n")
            sys.exit(0)
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

    # Not enough data
    if len(candles) < 30:
        result = {
            "symbol": args.symbol,
            "period": args.period,
            "summary": "insufficient_data",
            "reasons": [],
            "risks": [],
            "secrets_printed": False,
        }
        if args.format == "json":
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.format == "markdown":
            print(f"# Signal Explainer: {args.symbol} {args.period}\n")
            print("## Summary\ninsufficient_data\n")
        sys.exit(0)

    # Compute indicators
    rsi_vals = compute_rsi(candles, period=14)
    sma20 = compute_sma(candles, 20)
    sma50 = compute_sma(candles, 50)
    macd_data = compute_macd(candles, fast=12, slow=26, signal=9)
    macd_vals = macd_data["macd"]
    signal_vals = macd_data["signal"]

    # Detect volume spike (last 24h)
    volume_spike = False
    if len(candles) > 24:
        avg_vol = sum(c["volume"] for c in candles[-24:]) / 24
        if candles[-1]["volume"] > avg_vol * 2.0:
            volume_spike = True

    # Detect volatility expansion (std dev of returns > 2× base)
    volatility_expanded = False
    if len(candles) > 30:
        changes = [candles[i]["close"] / candles[i-1]["close"] - 1 for i in range(1, 31)]
        base_std = (sum((x - sum(changes)/len(changes))**2 for x in changes) / len(changes))**0.5 if len(changes) > 1 else 0.0
        recent_changes = [candles[j]["close"] / candles[j-1]["close"] - 1 for j in range(len(candles)-30, len(candles))]
        win_std = (sum((x - sum(recent_changes)/len(recent_changes))**2 for x in recent_changes) / len(recent_changes))**0.5 if len(recent_changes) > 1 else 0.0
        if win_std > base_std * 2.0:
            volatility_expanded = True

    # Build summary
    reasons = []
    risks = []

    # Bullish signals
    if len(candles) > 50 and sma20[-1] is not None and sma50[-1] is not None and sma20[-1] > sma50[-1]:
        reasons.append("SMA20 above SMA50")
    if rsi_vals[-1] is not None and 50 < rsi_vals[-1] < 70:
        reasons.append(f"RSI {rsi_vals[-1]:.1f}, not overbought")
    if macd_vals[-1] is not None and signal_vals[-1] is not None and macd_vals[-1] > signal_vals[-1]:
        reasons.append("MACD positive")
    if volume_spike:
        reasons.append("volume spike confirmed continuation")
    if volatility_expanded:
        reasons.append("volatility expanded after breakout")

    # Bearish signals
    if len(candles) > 50 and sma20[-1] is not None and sma50[-1] is not None and sma20[-1] < sma50[-1]:
        reasons.append("SMA20 below SMA50")
    if rsi_vals[-1] is not None and 30 < rsi_vals[-1] < 50:
        reasons.append(f"RSI {rsi_vals[-1]:.1f}, not oversold")
    if macd_vals[-1] is not None and signal_vals[-1] is not None and macd_vals[-1] < signal_vals[-1]:
        reasons.append("MACD negative")

    # Risks
    if rsi_vals[-1] is not None and rsi_vals[-1] > 65:
        risks.append("RSI approaching overbought")
    if rsi_vals[-1] is not None and rsi_vals[-1] < 35:
        risks.append("RSI approaching oversold")
    if len(candles) > 1:
        last = candles[-1]
        prev = candles[-2]
        if last["high"] > last["close"] and (last["high"] - last["close"]) > (last["close"] - last["low"]) * 1.5:
            risks.append("latest candle has upper wick")
        if last["low"] < last["close"] and (last["close"] - last["low"]) > (last["high"] - last["close"]) * 1.5:
            risks.append("latest candle has lower wick")

    # Determine overall sentiment
    bullish_count = sum(1 for r in reasons if "SMA20 above" in r or "RSI" in r and "not overbought" in r or "MACD positive" in r or "volume spike" in r)
    bearish_count = sum(1 for r in reasons if "SMA20 below" in r or "RSI" in r and "not oversold" in r or "MACD negative" in r)

    if bullish_count >= 3:
        summary = f"{args.symbol} {args.period}: strongly bullish"
    elif bullish_count >= 2:
        summary = f"{args.symbol} {args.period}: moderately bullish"
    elif bearish_count >= 3:
        summary = f"{args.symbol} {args.period}: strongly bearish"
    elif bearish_count >= 2:
        summary = f"{args.symbol} {args.period}: moderately bearish"
    else:
        summary = f"{args.symbol} {args.period}: neutral"

    result = {
        "symbol": args.symbol,
        "period": args.period,
        "summary": summary,
        "reasons": reasons,
        "risks": risks,
        "secrets_printed": False,
    }

    # Output
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.format == "markdown":
        print(f"# Signal Explainer: {args.symbol} {args.period}\n")
        print(f"## Summary\n{summary}\n")
        if reasons:
            print("## Reasons\n")
            for r in reasons:
                print(f"- {r}")
            print()
        if risks:
            print("## Risks\n")
            for r in risks:
                print(f"- {r}")
            print()

if __name__ == "__main__":
    asyncio.run(main())

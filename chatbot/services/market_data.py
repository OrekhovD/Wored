"""Versioned market snapshots shared through Redis, never through collector imports."""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from typing import Any

TIMEFRAMES = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "60min"}
PERIOD_SECONDS = {"1min": 60, "5min": 300, "15min": 900, "30min": 1800,
                  "60min": 3600, "4hour": 14400, "1day": 86400}


def timestamp_age(value, now: float | None = None) -> float | None:
    try:
        if isinstance(value, (float, int)):
            stamp = float(value) / (1000 if value > 1e12 else 1)
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None
            stamp = parsed.timestamp()
        age = (time.time() if now is None else now) - stamp
        return age if math.isfinite(age) and age >= 0 else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def fresh_ticker(ticker: dict, now: float | None = None, max_age: int = 60) -> bool:
    age = timestamp_age(ticker.get("timestamp") or ticker.get("ts"), now)
    try:
        price = float(ticker.get("price", 0))
        return age is not None and age <= max_age and math.isfinite(price) and price > 0
    except (ValueError, TypeError):
        return False


def calculate_closed_indicators(candles: list[dict], period: str, now: float | None = None) -> dict:
    period = TIMEFRAMES.get(period, period)
    seconds = PERIOD_SECONDS[period]
    now = time.time() if now is None else now
    rows = sorted((r for r in candles if float(r["id"]) + seconds <= now), key=lambda r: r["id"])
    if len(rows) < 30:
        return {}
    previous_time = None
    fast = slow = signal = gain = loss = atr = 0.0
    previous_close = float(rows[0]["close"])
    for index, row in enumerate(rows):
        close, high, low = (float(row[k]) for k in ("close", "high", "low"))
        if not all(math.isfinite(v) and v > 0 for v in (close, high, low)) or not low <= close <= high:
            return {}
        if previous_time is not None and float(row["id"]) - previous_time != seconds:
            return {}
        previous_time = float(row["id"])
        delta = close - previous_close
        gain = gain * 13 / 14 + max(delta, 0) / 14
        loss = loss * 13 / 14 + max(-delta, 0) / 14
        tr = max(high - low, abs(high - previous_close), abs(low - previous_close))
        atr = tr if index == 0 else atr * 13 / 14 + tr / 14
        fast = close if index == 0 else fast + 2 / 13 * (close - fast)
        slow = close if index == 0 else slow + 2 / 27 * (close - slow)
        macd = fast - slow
        signal += 2 / 10 * (macd - signal)
        previous_close = close
    rsi = 50.0 if gain == loss == 0 else (100.0 if loss == 0 else 100 - 100 / (1 + gain / loss))
    return {"rsi": rsi, "rsi_14": rsi, "macd": macd, "macd_signal": signal,
            "macd_hist": macd - signal, "atr": atr,
            "trend": "up" if macd > 0 else "down" if macd < 0 else "flat",
            "as_of": float(rows[-1]["id"]) + seconds, "sample_count": len(rows)}


async def read_market_context(redis, symbol: str, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    result: dict[str, Any] = {"schema_version": 1, "symbol": symbol.upper(),
              "timestamp": datetime.fromtimestamp(now, timezone.utc).isoformat(),
              "price": None, "mark_price": None, "funding_context": {"rate": None},
              "timeframes": {}, "risk_flags": [], "quality": "unavailable",
              "volatility_regime": "unknown", "liquidity_regime": "unknown"}
    try:
        ticker = json.loads(await redis.get(f"ticker:{symbol.lower()}") or "{}")
        stored = json.loads(await redis.get(f"market_context:{symbol.lower()}") or "{}")
        if fresh_ticker(ticker, now):
            result["price"] = result["mark_price"] = float(ticker["price"])
        else:
            result["risk_flags"].append("ticker_stale_or_missing")
        publication_age = timestamp_age(stored.get("published_at"), now)
        valid = stored.get("schema_version") == 1 and publication_age is not None and publication_age <= 90
        for alias, period in TIMEFRAMES.items():
            item = stored.get("timeframes", {}).get(alias, {}) if valid else {}
            age = timestamp_age(item.get("as_of"), now)
            numbers = [item.get(k) for k in ("rsi", "macd_hist", "atr")]
            complete = all(isinstance(v, (int, float)) and math.isfinite(v) for v in numbers)
            if not complete or age is None or age > PERIOD_SECONDS[period] + 60:
                result["timeframes"][alias] = {"trend": None, "rsi": None, "macd_hist": None, "atr": None}
                result["risk_flags"].append(f"indicators_unavailable:{alias}")
            else:
                result["timeframes"][alias] = item
        if not result["risk_flags"]:
            result["quality"] = "ready"
    except (ValueError, TypeError, AttributeError):
        result["risk_flags"].append("invalid_snapshot")
    except Exception:
        result["risk_flags"].append("snapshot_store_unavailable")
    return result

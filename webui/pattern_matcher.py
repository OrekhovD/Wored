"""
Seasonal pattern matcher for crypto price forecasting.

Finds similar historical windows on two axes:
  - intraday seasonality (time of day)
  - weekday seasonality (day of week)

Returns top-N matching windows ordered by similarity to the current window.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from prediction_timeframes import STEP_MINUTES_MAP

log = logging.getLogger("webui.pattern_matcher")


@dataclass
class PatternMatch:
    anchor_time: int  # unix seconds
    similarity_score: float  # 0..1, higher = more similar
    match_type: str  # "intraday", "weekday", "combined"
    window_candles: list[dict[str, Any]]
    metadata: dict[str, Any]


def _candle_to_features(candles: list[dict[str, Any]]) -> dict[str, float]:
    """Convert a window of candles to a compact feature vector."""
    if not candles:
        return {"return": 0.0, "volatility": 0.0, "range": 0.0, "direction": 0.0}

    opens = [c["open"] for c in candles if "open" in c]
    closes = [c["close"] for c in candles if "close" in c]
    highs = [c.get("high", c["close"]) for c in candles]
    lows = [c.get("low", c["open"]) for c in candles]

    if not opens or not closes:
        return {"return": 0.0, "volatility": 0.0, "range": 0.0, "direction": 0.0}

    start_price = opens[0]
    end_price = closes[-1]
    total_return = ((end_price - start_price) / start_price * 100.0) if start_price else 0.0

    # simple volatility = mean absolute candle return
    returns = []
    for i in range(1, len(closes)):
        if closes[i - 1]:
            returns.append((closes[i] - closes[i - 1]) / closes[i - 1] * 100.0)
    volatility = sum(abs(r) for r in returns) / len(returns) if returns else 0.0

    # average range
    ranges = [(h - l) / ((h + l) / 2) * 100.0 for h, l in zip(highs, lows) if (h + l)]
    avg_range = sum(ranges) / len(ranges) if ranges else 0.0

    # direction = fraction of green candles
    green = sum(1 for c in candles if c.get("close", 0) >= c.get("open", 0))
    direction = green / len(candles) if candles else 0.0

    return {
        "return": total_return,
        "volatility": volatility,
        "range": avg_range,
        "direction": direction,
    }


def _window_similarity(feat_a: dict[str, float], feat_b: dict[str, float]) -> float:
    """Simple cosine-ish similarity over [return, volatility, range, direction]."""
    keys = ["return", "volatility", "range", "direction"]
    # normalize per-key to avoid scale domination
    diffs = []
    for k in keys:
        a = feat_a.get(k, 0.0)
        b = feat_b.get(k, 0.0)
        scale = max(abs(a), abs(b), 1.0)
        diffs.append(abs(a - b) / scale)
    avg_diff = sum(diffs) / len(diffs) if diffs else 1.0
    return max(0.0, 1.0 - avg_diff)


def _dt_from_ts(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def find_seasonal_patterns(
    candles: list[dict[str, Any]],
    current_window: list[dict[str, Any]],
    base_timeframe: str,
    depth: int = 3,
) -> list[PatternMatch]:
    """Search `candles` for windows similar to `current_window`.

    Args:
        candles: month-long historical OHLC list, oldest first.
        current_window: recent window to compare against.
        base_timeframe: e.g. "60min", "15min", "4hour".
        depth: how many top matches to return (3/6/9 per TZ).

    Returns:
        list of PatternMatch sorted by similarity_score desc.
    """
    if not candles or not current_window:
        return []

    window_size = len(current_window)
    if window_size < 2 or window_size > len(candles):
        return []

    current_features = _candle_to_features(current_window)
    current_end_ts = current_window[-1].get("time", 0)
    current_dt = _dt_from_ts(current_end_ts) if current_end_ts else None

    matches: list[PatternMatch] = []

    # Slide a window across historical candles
    for i in range(window_size, len(candles) - window_size + 1):
        window = candles[i : i + window_size]
        end_ts = window[-1].get("time", 0)
        if not end_ts:
            continue

        end_dt = _dt_from_ts(end_ts)
        match_types: list[str] = []

        if current_dt:
            # intraday: same hour (for hour timeframe) or same minute-of-day
            if end_dt.hour == current_dt.hour and end_dt.minute == current_dt.minute:
                match_types.append("intraday")
            # weekday
            if end_dt.weekday() == current_dt.weekday():
                match_types.append("weekday")

        if not match_types:
            continue

        feat = _candle_to_features(window)
        score = _window_similarity(current_features, feat)
        if score <= 0.3:
            continue

        matches.append(
            PatternMatch(
                anchor_time=end_ts,
                similarity_score=round(score, 4),
                match_type="+".join(match_types),
                window_candles=window,
                metadata={
                    "weekday": end_dt.strftime("%A"),
                    "time": end_dt.strftime("%H:%M"),
                    "features": feat,
                },
            )
        )

    matches.sort(key=lambda m: m.similarity_score, reverse=True)
    return matches[:depth]


def pattern_matches_to_context(
    matches: list[PatternMatch],
    include_candles: bool = False,
) -> list[dict[str, Any]]:
    """Serialize PatternMatch list for JSON context payload to AI models."""
    out: list[dict[str, Any]] = []
    for m in matches:
        item: dict[str, Any] = {
            "score": m.similarity_score,
            "match_type": m.match_type,
            "anchor_time": m.anchor_time,
            "weekday": m.metadata.get("weekday"),
            "time": m.metadata.get("time"),
            "window_features": m.metadata.get("features"),
        }
        if include_candles:
            item["candles"] = m.window_candles
        out.append(item)
    return out

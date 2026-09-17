"""Small, reproducible B0/B1 forecast baselines.

B0 is persistence and B1 is bounded close-to-close drift.  They provide a
measurable floor for later model promotion; neither can submit an order.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Sequence


ZERO = Decimal("0")
ONE = Decimal("1")
MAX_DRIFT = Decimal("0.05")


@dataclass(frozen=True)
class Candle:
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class ForecastPoint:
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    confidence: Decimal


@dataclass(frozen=True)
class ForecastRun:
    model_version: str
    data_cutoff: datetime
    horizon: int
    points: tuple[ForecastPoint, ...]


def _valid_decimal(value: Decimal, field: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field}: invalid decimal") from exc
    if not parsed.is_finite() or parsed <= ZERO:
        raise ValueError(f"{field}: must be positive")
    return parsed


def _validate(candles: Sequence[Candle], horizon: int) -> tuple[Candle, ...]:
    if horizon < 1 or horizon > 48:
        raise ValueError("horizon: expected value from 1 to 48")
    if len(candles) < 2:
        raise ValueError("candles: at least two closed candles are required")
    ordered = tuple(candles)
    previous_close: datetime | None = None
    for candle in ordered:
        if candle.open_time >= candle.close_time:
            raise ValueError("candle: open_time must precede close_time")
        if previous_close is not None and candle.open_time < previous_close:
            raise ValueError("candles: must be chronological and non-overlapping")
        for field in ("open", "high", "low", "close"):
            _valid_decimal(getattr(candle, field), "candle." + field)
        if candle.high < candle.low:
            raise ValueError("candle: high below low")
        previous_close = candle.close_time
    return ordered


def _interval(candles: tuple[Candle, ...]) -> timedelta:
    value = candles[-1].close_time - candles[-1].open_time
    if value.total_seconds() <= 0:
        raise ValueError("candle interval: must be positive")
    return value


def run_baseline_forecast(
    candles: Sequence[Candle],
    *,
    horizon: int,
    model_version: str = "B0",
) -> ForecastRun:
    """Forecast closed candles using either B0 persistence or B1 drift.

    The B1 drift is bounded to five percent per bar and gets a symmetric band
    equal to the recent median true range.  That prevents an unbounded trend
    extrapolation from becoming an implicit trade instruction.
    """
    ordered = _validate(candles, horizon)
    if model_version not in {"B0", "B1"}:
        raise ValueError("model_version: expected B0 or B1")
    interval = _interval(ordered)
    latest = ordered[-1]
    previous = ordered[-2]
    drift = ZERO
    if model_version == "B1":
        drift = (latest.close - previous.close) / previous.close
        drift = max(-MAX_DRIFT, min(MAX_DRIFT, drift))
    ranges = sorted((item.high - item.low for item in ordered[-14:]))
    band = ranges[len(ranges) // 2] if ranges else ZERO
    points: list[ForecastPoint] = []
    prior_close = latest.close
    next_open = latest.close_time
    for _ in range(horizon):
        close = prior_close * (ONE + drift)
        open_time = next_open
        close_time = open_time + interval
        high = max(prior_close, close) + band / Decimal("2")
        low = min(prior_close, close) - band / Decimal("2")
        points.append(
            ForecastPoint(
                open_time=open_time,
                close_time=close_time,
                open=prior_close,
                high=high,
                low=low,
                close=close,
                confidence=Decimal("0.5") if model_version == "B0" else Decimal("0.55"),
            )
        )
        prior_close = close
        next_open = close_time
    cutoff = latest.close_time.astimezone(timezone.utc)
    return ForecastRun(model_version, cutoff, horizon, tuple(points))

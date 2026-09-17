"""forecast_engine.indicators — technical indicators using Decimal.

All functions accept either a list of ``Decimal`` values (closes, highs,
lows, volumes) or a list of ``OHLCVBar``.  Returns ``None`` when there is
insufficient data to compute the indicator — callers must check.

No external dependencies.  All arithmetic uses ``decimal.Decimal``.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from forecast_engine.contracts import OHLCVBar

ZERO = Decimal(0)
ONE = Decimal(1)
TWO = Decimal(2)
HUNDRED = Decimal(100)


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _to_decimal(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _closes(bars: Sequence[OHLCVBar]) -> List[Decimal]:
    return [b.close for b in bars]


def _highs(bars: Sequence[OHLCVBar]) -> List[Decimal]:
    return [b.high for b in bars]


def _lows(bars: Sequence[OHLCVBar]) -> List[Decimal]:
    return [b.low for b in bars]


def _volumes(bars: Sequence[OHLCVBar]) -> List[Decimal]:
    return [b.volume for b in bars]


# --------------------------------------------------------------------------- #
# Simple Moving Average
# --------------------------------------------------------------------------- #


def ma(closes: Sequence[Decimal], period: int) -> Optional[Decimal]:
    """Simple moving average of the last *period* closes."""
    if period < 1 or len(closes) < period:
        return None
    window = closes[-period:]
    return sum(window, ZERO) / Decimal(period)


def ma_series(closes: Sequence[Decimal], period: int) -> List[Optional[Decimal]]:
    """Full SMA series; ``None`` during warmup."""
    result: List[Optional[Decimal]] = []
    for i in range(len(closes)):
        if i < period - 1:
            result.append(None)
        else:
            window = closes[i - period + 1 : i + 1]
            result.append(sum(window, ZERO) / Decimal(period))
    return result


# --------------------------------------------------------------------------- #
# Exponential Moving Average
# --------------------------------------------------------------------------- #


def ema(closes: Sequence[Decimal], period: int) -> Optional[Decimal]:
    """EMA of the full series, returning the last value.

    Seeded with the SMA of the first *period* closes.
    alpha = 2 / (period + 1).
    """
    if period < 1 or len(closes) < period:
        return None
    alpha = TWO / Decimal(period + 1)
    one_minus_alpha = ONE - alpha
    # seed
    value = sum(closes[:period], ZERO) / Decimal(period)
    for price in closes[period:]:
        value = alpha * price + one_minus_alpha * value
    return value


def ema_series(closes: Sequence[Decimal], period: int) -> List[Optional[Decimal]]:
    """Full EMA series; ``None`` during warmup."""
    if period < 1 or len(closes) < period:
        return [None] * len(closes)
    alpha = TWO / Decimal(period + 1)
    one_minus_alpha = ONE - alpha
    result: List[Optional[Decimal]] = [None] * (period - 1)
    # seed
    value = sum(closes[:period], ZERO) / Decimal(period)
    result.append(value)
    for price in closes[period:]:
        value = alpha * price + one_minus_alpha * value
        result.append(value)
    return result


# --------------------------------------------------------------------------- #
# Bollinger Bands
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BollingerBands:
    upper: Decimal
    middle: Decimal
    lower: Decimal
    std: Decimal


def bollinger_bands(
    closes: Sequence[Decimal],
    period: int = 20,
    k: int = 2,
) -> Optional[BollingerBands]:
    """Bollinger Bands with population standard deviation."""
    mid = ma(closes, period)
    if mid is None:
        return None
    window = closes[-period:]
    variance = sum((x - mid) ** 2 for x in window) / Decimal(period)
    std = variance.sqrt()
    k_dec = Decimal(k)
    return BollingerBands(
        upper=mid + k_dec * std,
        middle=mid,
        lower=mid - k_dec * std,
        std=std,
    )


# --------------------------------------------------------------------------- #
# MACD
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MACDResult:
    macd_line: Decimal
    signal_line: Decimal
    histogram: Decimal


def macd(
    closes: Sequence[Decimal],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Optional[MACDResult]:
    """MACD(fast, slow, signal) returning the last (line, signal, histogram)."""
    if len(closes) < slow:
        return None
    ema_fast_series = ema_series(closes, fast)
    ema_slow_series = ema_series(closes, slow)
    # Build MACD line from the point both EMAs are available
    macd_values: List[Decimal] = []
    for f, s in zip(ema_fast_series, ema_slow_series):
        if f is not None and s is not None:
            macd_values.append(f - s)
    if len(macd_values) < signal:
        return None
    signal_value = ema(macd_values, signal)
    if signal_value is None:
        return None
    macd_last = macd_values[-1]
    return MACDResult(
        macd_line=macd_last,
        signal_line=signal_value,
        histogram=macd_last - signal_value,
    )


# --------------------------------------------------------------------------- #
# KDJ
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class KDJResult:
    k: Decimal
    d: Decimal
    j: Decimal


def kdj(
    highs: Sequence[Decimal],
    lows: Sequence[Decimal],
    closes: Sequence[Decimal],
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> Optional[KDJResult]:
    """KDJ(n, m1, m2) stochastic oscillator.

    RSV = (close - lowest_low(n)) / (highest_high(n) - lowest_low(n)) * 100
    K = ((m1-1)/m1) * prev_K + (1/m1) * RSV   (seeded at 50)
    D = ((m2-1)/m2) * prev_D + (1/m2) * K     (seeded at 50)
    J = 3*K - 2*D
    """
    length = len(closes)
    if length < n:
        return None
    # Compute RSV series
    rsvs: List[Decimal] = []
    for i in range(n - 1, length):
        hh = max(highs[i - n + 1 : i + 1])
        ll = min(lows[i - n + 1 : i + 1])
        if hh == ll:
            rsvs.append(Decimal(50))
        else:
            rsvs.append((closes[i] - ll) / (hh - ll) * HUNDRED)
    if len(rsvs) < 1:
        return None
    # Smooth K and D
    k = Decimal(50)
    d = Decimal(50)
    m1_dec = Decimal(m1)
    m2_dec = Decimal(m2)
    for rsv in rsvs:
        k = (m1_dec - ONE) / m1_dec * k + ONE / m1_dec * rsv
        d = (m2_dec - ONE) / m2_dec * d + ONE / m2_dec * k
    j = Decimal(3) * k - Decimal(2) * d
    return KDJResult(k=k, d=d, j=j)


# --------------------------------------------------------------------------- #
# ATR (Wilder's smoothing)
# --------------------------------------------------------------------------- #


def atr(bars: Sequence[OHLCVBar], period: int = 14) -> Optional[Decimal]:
    """Average True Range using Wilder's smoothing method.

    Requires at least *period* + 1 bars (the first TR needs a prior close).
    """
    if len(bars) < period + 1:
        return None
    trs: List[Decimal] = []
    for i in range(1, len(bars)):
        high = bars[i].high
        low = bars[i].low
        prev_close = bars[i - 1].close
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        trs.append(tr)
    if len(trs) < period:
        return None
    # Seed with SMA
    value = sum(trs[:period], ZERO) / Decimal(period)
    n_dec = Decimal(period)
    for tr in trs[period:]:
        value = (value * (n_dec - ONE) + tr) / n_dec
    return value


# --------------------------------------------------------------------------- #
# AVL — Average Volume
# --------------------------------------------------------------------------- #


def avl(volumes: Sequence[Decimal]) -> Optional[Decimal]:
    """Average volume over the entire input."""
    if not volumes:
        return None
    return sum(volumes, ZERO) / Decimal(len(volumes))


# --------------------------------------------------------------------------- #
# Convenience: compute all indicators from bars
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class IndicatorSnapshot:
    """All indicators computed at the last bar."""

    ma_20: Optional[Decimal]
    ema_20: Optional[Decimal]
    ema_50: Optional[Decimal]
    bb: Optional[BollingerBands]
    macd: Optional[MACDResult]
    kdj: Optional[KDJResult]
    atr_14: Optional[Decimal]
    avl: Optional[Decimal]


def snapshot(bars: Sequence[OHLCVBar]) -> IndicatorSnapshot:
    """Compute a full indicator snapshot from a list of closed bars."""
    closes = _closes(bars)
    highs = _highs(bars)
    lows = _lows(bars)
    vols = _volumes(bars)
    return IndicatorSnapshot(
        ma_20=ma(closes, 20),
        ema_20=ema(closes, 20),
        ema_50=ema(closes, 50),
        bb=bollinger_bands(closes, 20, 2),
        macd=macd(closes, 12, 26, 9),
        kdj=kdj(highs, lows, closes, 9, 3, 3),
        atr_14=atr(bars, 14),
        avl=avl(vols),
    )
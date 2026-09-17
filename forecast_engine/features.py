"""forecast_engine.features — feature extraction with no look-ahead.

Extracts a flat dict of features from a list of closed OHLCV bars.  All
features are computable from data available at the close of the last bar
(no future data is used).  The dict includes ``features_hash``, a SHA-256
of the sorted feature key-value pairs (excluding ``features_hash`` itself),
for reproducibility and dataset deduplication.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any, Dict, Sequence

from forecast_engine.contracts import OHLCVBar
from forecast_engine.indicators import (
    BollingerBands,
    MACDResult,
    atr,
    avl,
    bollinger_bands,
    ema,
    kdj,
    ma,
)

ZERO = Decimal(0)
ONE = Decimal(1)


def _safe_div(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Safe division returning ZERO on zero denominator."""
    if denominator == ZERO:
        return ZERO
    return numerator / denominator


def _pct(current: Decimal, previous: Decimal) -> Decimal:
    """Percentage change (current/previous - 1), ZERO if previous is zero."""
    if previous == ZERO:
        return ZERO
    return current / previous - ONE


def extract_features(bars: Sequence[OHLCVBar]) -> Dict[str, Any]:
    """Extract a flat feature dict from closed bars.

    The returned dict contains only features known at the close of the
    last bar.  ``features_hash`` is the SHA-256 of the deterministic
    string representation of all features (excluding itself).
    """
    if not bars:
        return {"features_hash": _hash_dict({}), "n_bars": 0}

    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    volumes = [b.volume for b in bars]
    last = bars[-1]

    features: Dict[str, Any] = {}
    features["n_bars"] = len(bars)
    features["last_close"] = last.close
    features["last_open"] = last.open
    features["last_high"] = last.high
    features["last_low"] = last.low
    features["last_volume"] = last.volume

    # Returns
    if len(closes) >= 2:
        features["return_1"] = _pct(closes[-1], closes[-2])
    else:
        features["return_1"] = ZERO

    if len(closes) >= 6:
        features["return_5"] = _pct(closes[-1], closes[-6])
    else:
        features["return_5"] = ZERO

    # Moving averages
    ma_20 = ma(closes, 20)
    features["ma_20"] = ma_20
    if ma_20 is not None:
        features["close_to_ma20"] = _safe_div(last.close, ma_20) - ONE
    else:
        features["close_to_ma20"] = ZERO

    ema_20 = ema(closes, 20)
    ema_50 = ema(closes, 50)
    features["ema_20"] = ema_20
    features["ema_50"] = ema_50
    if ema_20 is not None and ema_50 is not None:
        features["ema_spread"] = _safe_div(ema_20, ema_50) - ONE
    else:
        features["ema_spread"] = ZERO

    # Bollinger Bands
    bb = bollinger_bands(closes, 20, 2)
    if bb is not None:
        features["bb_upper"] = bb.upper
        features["bb_middle"] = bb.middle
        features["bb_lower"] = bb.lower
        features["bb_width"] = bb.upper - bb.lower
        bb_range = bb.upper - bb.lower
        if bb_range > ZERO:
            features["bb_position"] = _safe_div(last.close - bb.lower, bb_range)
        else:
            features["bb_position"] = Decimal("0.5")
    else:
        features["bb_upper"] = None
        features["bb_middle"] = None
        features["bb_lower"] = None
        features["bb_width"] = ZERO
        features["bb_position"] = Decimal("0.5")

    # MACD
    macd_res = _compute_macd(closes)
    if macd_res is not None:
        features["macd_line"] = macd_res.macd_line
        features["macd_signal"] = macd_res.signal_line
        features["macd_histogram"] = macd_res.histogram
    else:
        features["macd_line"] = None
        features["macd_signal"] = None
        features["macd_histogram"] = ZERO

    # KDJ
    kdj_res = kdj(highs, lows, closes, 9, 3, 3)
    if kdj_res is not None:
        features["kdj_k"] = kdj_res.k
        features["kdj_d"] = kdj_res.d
        features["kdj_j"] = kdj_res.j
    else:
        features["kdj_k"] = None
        features["kdj_d"] = None
        features["kdj_j"] = None

    # ATR
    atr_val = atr(bars, 14)
    features["atr_14"] = atr_val
    if atr_val is not None and atr_val > ZERO:
        features["atr_pct"] = _safe_div(atr_val, last.close)
    else:
        features["atr_pct"] = ZERO

    # Volume
    avl_val = avl(volumes)
    features["avl"] = avl_val
    if avl_val is not None and avl_val > ZERO:
        features["volume_ratio"] = _safe_div(last.volume, avl_val)
    else:
        features["volume_ratio"] = ONE

    # Up/down ratio of recent returns
    if len(closes) >= 10:
        recent_returns = [
            closes[i] - closes[i - 1] for i in range(len(closes) - 10, len(closes))
        ]
        up_count = sum(1 for r in recent_returns if r > ZERO)
        features["up_ratio_10"] = Decimal(up_count) / Decimal(10)
    else:
        features["up_ratio_10"] = Decimal("0.5")

    # Hash
    features["features_hash"] = _hash_dict(features)
    return features


def _hash_dict(d: Dict[str, Any]) -> str:
    """SHA-256 of a deterministic string representation of *d*.

    The ``features_hash`` key is excluded from the hash input.  ``None``
    values are serialised as ``"null"``.  All keys are sorted.
    """
    items = []
    for key in sorted(d.keys()):
        if key == "features_hash":
            continue
        value = d[key]
        if value is None:
            value_str = "null"
        elif isinstance(value, Decimal):
            value_str = str(value)
        else:
            value_str = str(value)
        items.append(f"{key}={value_str}")
    payload = "|".join(items)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _compute_macd(closes):
    """Local MACD wrapper to avoid circular import."""
    from forecast_engine.indicators import macd as _macd

    return _macd(closes, 12, 26, 9)
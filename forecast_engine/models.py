"""forecast_engine.models — B0 naive persistence and B1 quantile baseline.

B0 (``B0_NAIVE``): predicts close = last close for all horizon steps.
    High/low = close (zero volatility), confidence = 0.5, p_up = 0.5.

B1 (``B1_QUANTILE``): deterministic quantile baseline using EWMA
    volatility of close-to-close returns.  Quantile bands q10/q50/q90
    are constructed with fixed normal z-scores.  The predicted close
    follows the EWMA drift, bounded to ±5 % per bar.

Both models return ``list[ForecastCandle]`` for t+1..t+horizon.
All arithmetic uses ``Decimal``.  No external dependencies.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import List, Sequence

from forecast_engine.contracts import ForecastCandle, OHLCVBar

ZERO = Decimal(0)
ONE = Decimal(1)
TWO = Decimal(2)
HALF = Decimal("0.5")
MAX_DRIFT = Decimal("0.05")

# Standard normal quantiles (deterministic constants)
Z_10 = Decimal("-1.2815515655446004")
Z_50 = ZERO
Z_90 = Decimal("1.2815515655446004")

# EWMA decay for volatility estimation
EWMA_LAMBDA = Decimal("0.94")


def _interval(bars: Sequence[OHLCVBar]) -> timedelta:
    """Infer the bar interval from the last two bars."""
    if len(bars) < 2:
        return timedelta(minutes=5)
    delta = bars[-1].time - bars[-2].time
    if delta.total_seconds() <= 0:
        return timedelta(minutes=5)
    return delta


def _ewma_volatility(returns: Sequence[Decimal]) -> Decimal:
    """EWMA volatility of squared returns.

    sigma^2 = lambda * sigma^2_prev + (1 - lambda) * return^2
    Seeded with the simple variance of the available returns.
    """
    if not returns:
        return ZERO
    squared = [r ** 2 for r in returns]
    # Seed: simple variance
    seed = sum(squared, ZERO) / Decimal(len(squared))
    sigma_sq = seed
    for sq in squared:
        sigma_sq = EWMA_LAMBDA * sigma_sq + (ONE - EWMA_LAMBDA) * sq
    return sigma_sq.sqrt()


def _bounded_drift(returns: Sequence[Decimal]) -> Decimal:
    """EWMA drift of returns, bounded to ±MAX_DRIFT."""
    if not returns:
        return ZERO
    # EWMA mean
    alpha = ONE - EWMA_LAMBDA
    drift = returns[0]
    for r in returns[1:]:
        drift = EWMA_LAMBDA * drift + alpha * r
    # Bound
    if drift > MAX_DRIFT:
        return MAX_DRIFT
    if drift < -MAX_DRIFT:
        return -MAX_DRIFT
    return drift


# --------------------------------------------------------------------------- #
# B0 — Naive Persistence
# --------------------------------------------------------------------------- #


def predict_b0(
    bars: Sequence[OHLCVBar],
    horizon: int = 3,
) -> List[ForecastCandle]:
    """B0 naive persistence: close = last close for all steps.

    Returns ``horizon`` ForecastCandle entries for t+1..t+horizon.
    """
    if not bars:
        return []
    if horizon < 1:
        raise ValueError("horizon must be >= 1")

    last = bars[-1]
    interval = _interval(bars)
    last_close = last.close
    candles: List[ForecastCandle] = []

    next_open_time = last.time + interval
    for step in range(1, horizon + 1):
        open_time = next_open_time
        close_time = open_time + interval
        fc = ForecastCandle(
            open_time=open_time,
            close_time=close_time,
            predicted_open=last_close,
            predicted_high=last_close,
            predicted_low=last_close,
            predicted_close=last_close,
            confidence=HALF,
            p_up=HALF,
        )
        candles.append(fc)
        next_open_time = close_time

    return candles


# --------------------------------------------------------------------------- #
# B1 — Deterministic Quantile Baseline
# --------------------------------------------------------------------------- #


def predict_b1(
    bars: Sequence[OHLCVBar],
    horizon: int = 3,
) -> List[ForecastCandle]:
    """B1 deterministic quantile baseline.

    Uses EWMA volatility and EWMA drift (bounded to ±5 %) to construct
    q10/q50/q90 quantile bands for each horizon step.  The predicted
    close follows the bounded drift; high/low are the q90/q10 bands.

    Returns ``horizon`` ForecastCandle entries for t+1..t+horizon.
    """
    if not bars:
        return []
    if horizon < 1:
        raise ValueError("horizon must be >= 1")

    last = bars[-1]
    interval = _interval(bars)
    last_close = last.close

    # Compute close-to-close returns
    closes = [b.close for b in bars]
    returns: List[Decimal] = []
    for i in range(1, len(closes)):
        if closes[i - 1] != ZERO:
            returns.append(closes[i] / closes[i - 1] - ONE)
        else:
            returns.append(ZERO)

    volatility = _ewma_volatility(returns)
    drift = _bounded_drift(returns)

    # p_up: fraction of positive historical returns
    if returns:
        up_count = sum(1 for r in returns if r > ZERO)
        p_up = Decimal(up_count) / Decimal(len(returns))
    else:
        p_up = HALF

    # Confidence: inverse of volatility (tighter bands → higher confidence)
    # Normalised so that confidence is in [0, 1]
    if volatility > ZERO:
        confidence = ONE / (ONE + volatility * Decimal(100))
    else:
        confidence = Decimal("0.9")

    candles: List[ForecastCandle] = []
    prior_close = last_close
    next_open_time = last.time + interval

    for step in range(1, horizon + 1):
        open_time = next_open_time
        close_time = open_time + interval

        # Predicted close follows bounded drift
        predicted_close = prior_close * (ONE + drift)
        predicted_open = prior_close

        # Time-scaling of volatility: sigma * sqrt(step)
        step_factor = Decimal(step).sqrt()
        sigma_step = volatility * step_factor

        # Quantile bands
        predicted_high = predicted_close * (ONE + Z_90 * sigma_step)
        predicted_low = predicted_close * (ONE + Z_10 * sigma_step)

        # Ensure high >= close >= low
        if predicted_high < predicted_close:
            predicted_high = predicted_close
        if predicted_low > predicted_close:
            predicted_low = predicted_close

        fc = ForecastCandle(
            open_time=open_time,
            close_time=close_time,
            predicted_open=predicted_open,
            predicted_high=predicted_high,
            predicted_low=predicted_low,
            predicted_close=predicted_close,
            confidence=confidence,
            p_up=p_up,
        )
        candles.append(fc)
        prior_close = predicted_close
        next_open_time = close_time

    return candles


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #

from forecast_engine.contracts import ModelVersion


def predict(
    bars: Sequence[OHLCVBar],
    model_version: ModelVersion,
    horizon: int = 3,
) -> List[ForecastCandle]:
    """Dispatch to the appropriate model."""
    if model_version == ModelVersion.B0_NAIVE:
        return predict_b0(bars, horizon)
    if model_version == ModelVersion.B1_QUANTILE:
        return predict_b1(bars, horizon)
    raise ValueError(f"Unknown model version: {model_version}")
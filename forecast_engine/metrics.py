"""Quantile / probabilistic forecast metrics (self-learn block C, D8).

The engine previously scored forecasts with only MAE and directional accuracy —
incapable of judging a *distributional* (quantile) forecast.  These functions add
the three metrics the acceptance gate needs:

* :func:`pinball_loss` — quantile (pinball) loss for the q10/q50/q90 triple,
  lower is better, and the proper scoring rule for the B1 quantile model.
* :func:`interval_coverage` — empirical fraction of actuals inside the nominal
  80 % (q10–q90) band; should sit near 0.80 if the model is calibrated.
* :func:`brier_score` — mean squared error of the predicted up-probability
  against the realised direction; lower is better, in ``[0, 1]``.

All pure functions on ``Decimal``/float scalars; no I/O.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Sequence

__all__ = ["QUANTILES", "pinball_loss", "interval_coverage", "brier_score", "skill_score"]

# The three evaluated quantiles (levels are the nominal τ, not the band edge).
QUANTILES = (Decimal("0.1"), Decimal("0.5"), Decimal("0.9"))


def _f(x) -> float:
    return float(x) if isinstance(x, Decimal) else float(x)


def _pinball_single(tau: float, actual: float, pred: float) -> float:
    """Single-observation pinball (quantile) loss.

    ``L = tau * (actual - pred)`` when the actual is at or above the prediction,
    otherwise ``(1 - tau) * (pred - actual)``.
    """
    diff = actual - pred
    if diff >= 0:
        return tau * diff
    return (tau - 1.0) * diff


def pinball_loss(actuals: Sequence, quantile_preds: Sequence[Sequence]) -> float:
    """Mean pinball loss over a ``q10/q50/q90`` forecast matrix.

    ``quantile_preds`` is a sequence (one per horizon step) of triples
    ``[q10, q50, q90]`` aligned index-for-index with ``actuals``.  Returns ``nan``
    as ``0.0`` for empty input to keep callers simple.
    """
    taus = [_f(t) for t in QUANTILES]
    total = 0.0
    n = 0
    for actual, triple in zip(actuals, quantile_preds):
        a = _f(actual)
        for tau, pred in zip(taus, triple):
            total += _pinball_single(tau, a, _f(pred))
            n += 1
    return (total / n) if n else 0.0


def interval_coverage(actuals: Sequence, lowers: Sequence, uppers: Sequence) -> float:
    """Empirical coverage of the ``[lower, upper]`` prediction-interval band."""
    n = 0
    hits = 0
    for actual, lo, hi in zip(actuals, lowers, uppers):
        a, low, high = _f(actual), _f(lo), _f(hi)
        n += 1
        if low <= a <= high:
            hits += 1
    return (hits / n) if n else 0.0


def brier_score(p_ups: Sequence, actual_ups: Sequence) -> float:
    """Mean squared error of predicted up-probabilities vs realised direction.

    ``actual_ups`` holds 0/1 labels (1 = the bar closed up).  Lower is better.
    """
    n = 0
    total = 0.0
    for p, y in zip(p_ups, actual_ups):
        diff = _f(p) - _f(y)
        total += diff * diff
        n += 1
    return (total / n) if n else 0.0


def skill_score(candidate: float, baseline: float) -> float:
    """Fractional improvement of a lower-is-better metric over a baseline.

    ``1 - candidate/baseline``; ``0`` means parity, ``>0`` beats the baseline.
    A zero/negative baseline is undefined and reported as ``0``.
    """
    if baseline <= 0:
        return 0.0
    return 1.0 - (candidate / baseline)

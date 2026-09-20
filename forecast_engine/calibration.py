"""forecast_engine.calibration — isotonic (PAVA) probability calibration (block E.3).

The baselines emitted a *constant* ``confidence`` (0.5 / 0.55 in ``core``), which is
not a probability at all and cannot be scored.  This module learns a monotone map
from a raw model score (an uncalibrated up-probability) to an empirical
probability using **isotonic regression** solved by the pool-adjacent-violators
algorithm, fitted on a sliding window of recent observations (the ТЗ window is
30 days of closed bars).

Deterministic, dependency-free, and *online-friendly*: ``add`` a labelled
observation, then ``calibrate`` a raw score.  Until a minimum sample is seen the
calibrator passes scores through unchanged so the ensemble never degrades to a
guess on thin data.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, List, Optional, Tuple

__all__ = ["IsotonicCalibrator", "isotonic_regression"]


def isotonic_regression(y: List[float], w: Optional[List[float]] = None) -> List[float]:
    """Pool-adjacent-violators: least-squares non-decreasing fit of ``y``.

    Returns a list the same length as ``y`` (which must be ordered by the
    predictor, ascending).  ``w`` are non-negative weights (default all-ones).
    """
    n = len(y)
    if n == 0:
        return []
    if w is None:
        w = [1.0] * n
    # Blocks are (weighted_sum, weight, value, count).
    sums: List[float] = []
    weights: List[float] = []
    values: List[float] = []
    counts: List[int] = []
    for i in range(n):
        wi = w[i]
        if wi <= 0:
            continue
        sums.append(y[i] * wi)
        weights.append(wi)
        values.append(y[i])
        counts.append(1)
        # Merge with previous blocks while monotonicity is violated.
        while len(values) > 1 and values[-2] > values[-1]:
            s2 = sums.pop(); w2 = weights.pop(); v2 = values.pop(); c2 = counts.pop()
            s1 = sums.pop(); w1 = weights.pop(); v1 = values.pop(); c1 = counts.pop()
            sums.append(s1 + s2)
            weights.append(w1 + w2)
            values.append((s1 + s2) / (w1 + w2))
            counts.append(c1 + c2)
    # Expand blocks back to per-point fitted values.
    out: List[float] = []
    for v, c in zip(values, counts):
        out.extend([v] * c)
    return out


class IsotonicCalibrator:
    """Sliding-window isotonic probability calibrator.

    ``window`` bounds how many recent ``(raw_score, label)`` observations are
    retained (the effective recency horizon — the ТЗ default corresponds to 30
    days of bars); ``min_points`` is the smallest sample before calibration is
    trusted (below it :meth:`calibrate` returns the raw score unchanged).
    """

    def __init__(self, window: int = 4320, min_points: int = 50) -> None:
        if window < 1:
            raise ValueError("window must be >= 1")
        if min_points < 2:
            raise ValueError("min_points must be >= 2")
        self._buffer: Deque[Tuple[float, float]] = deque(maxlen=window)
        self.min_points = min_points
        self._dirty = True
        self._fit_x: List[float] = []
        self._fit_y: List[float] = []

    def __len__(self) -> int:
        return len(self._buffer)

    @property
    def ready(self) -> bool:
        return len(self._buffer) >= self.min_points

    def add(self, raw_score: float, label: int | float) -> None:
        """Record one closed observation; ``label`` is 1 when the move was up."""
        y = 1.0 if float(label) >= 0.5 else 0.0
        self._buffer.append((float(raw_score), y))
        self._dirty = True

    def _refit(self) -> None:
        if not self._dirty:
            return
        pts = sorted(self._buffer, key=lambda p: p[0])
        # Aggregate identical scores first: PAVA on the per-point 0/1 labels
        # would not average ties, so a bucket's calibrated value must be the
        # weighted mean of its labels (weight = how many times the score seen).
        xs: List[float] = []
        means: List[float] = []
        weights: List[float] = []
        i = 0
        n = len(pts)
        while i < n:
            j = i
            total = 0.0
            while j < n and pts[j][0] == pts[i][0]:
                total += pts[j][1]
                j += 1
            count = j - i
            xs.append(pts[i][0])
            means.append(total / count)
            weights.append(float(count))
            i = j
        self._fit_x = xs
        self._fit_y = isotonic_regression(means, weights)
        self._dirty = False

    def calibrate(self, raw_score: float) -> float:
        """Map a raw score to a calibrated probability in ``[0, 1]``."""
        if not self.ready:
            return max(0.0, min(1.0, float(raw_score)))
        self._refit()
        s = float(raw_score)
        xs, ys = self._fit_x, self._fit_y
        if s <= xs[0]:
            out = ys[0]
        elif s >= xs[-1]:
            out = ys[-1]
        else:
            # step function: first breakpoint at or above s
            lo, hi = 0, len(xs)
            while lo < hi:
                mid = (lo + hi) // 2
                if xs[mid] < s:
                    lo = mid + 1
                else:
                    hi = mid
            out = ys[lo]
        return max(0.0, min(1.0, out))

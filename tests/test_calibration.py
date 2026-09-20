"""Block E.3 — isotonic probability calibration tests."""
from __future__ import annotations

import random

from forecast_engine.calibration import IsotonicCalibrator, isotonic_regression


def test_isotonic_regression_is_nondecreasing() -> None:
    y = [1.0, 0.0, 0.0, 1.0, 1.0, 0.0]
    fit = isotonic_regression(y)
    assert len(fit) == len(y)
    for a, b in zip(fit, fit[1:]):
        assert b >= a - 1e-9


def test_isotonic_regression_pools_violators() -> None:
    # strictly decreasing → collapses to the single mean
    fit = isotonic_regression([3.0, 2.0, 1.0])
    assert all(abs(v - 2.0) < 1e-9 for v in fit)


def test_not_ready_passes_score_through() -> None:
    cal = IsotonicCalibrator(min_points=50)
    for _ in range(10):
        cal.add(0.7, 1)
    assert cal.ready is False
    assert cal.calibrate(0.7) == 0.7


def test_calibrated_confidence_within_005_in_buckets() -> None:
    """Acceptance: for a well-specified scorer the calibrated probability must
    match the empirical up-rate within 0.05 at score levels 0.5 / 0.6 / 0.7."""
    rng = random.Random(20260101)
    cal = IsotonicCalibrator(window=20000, min_points=200)
    buckets = [0.5, 0.6, 0.7]
    # Feed each score level with labels drawn at exactly that probability so the
    # true conditional up-rate equals the raw score.
    for _ in range(6000):
        s = rng.choice(buckets)
        label = 1 if rng.random() < s else 0
        cal.add(s, label)
    assert cal.ready
    for s in buckets:
        calibrated = cal.calibrate(s)
        assert abs(calibrated - s) <= 0.05, f"score {s} calibrated to {calibrated}"


def test_calibrate_output_is_probability() -> None:
    rng = random.Random(3)
    cal = IsotonicCalibrator(min_points=20)
    for _ in range(200):
        s = rng.random()
        cal.add(s, 1 if rng.random() < s else 0)
    for probe in (0.0, 0.25, 0.5, 0.8, 1.0):
        v = cal.calibrate(probe)
        assert 0.0 <= v <= 1.0

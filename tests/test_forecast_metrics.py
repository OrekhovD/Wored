"""Block C — quantile/probabilistic forecast metrics + evaluator wiring."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from forecast_engine.contracts import ForecastCandle, ForecastRun, ModelVersion, OHLCVBar
from forecast_engine.evaluator import evaluate_forecast, walk_forward_compare
from forecast_engine.metrics import (
    brier_score,
    interval_coverage,
    pinball_loss,
    skill_score,
)


# --------------------------------------------------------------------------- #
# Unit: hand-computed values
# --------------------------------------------------------------------------- #
def test_pinball_loss_single_bar():
    # triple [9,10,11], actual 12 -> 0.3 + 1.0 + 0.9 = 2.2 over 3 quantiles
    assert pinball_loss([Decimal(12)], [[Decimal(9), Decimal(10), Decimal(11)]]) == pytest.approx(2.2 / 3)


def test_pinball_perfect_median_low_dispersion():
    # a point forecast (all quantiles == actual) has zero pinball loss
    assert pinball_loss([Decimal(10)], [[Decimal(10), Decimal(10), Decimal(10)]]) == pytest.approx(0.0)


def test_brier_score_values():
    assert brier_score([Decimal(1)], [1]) == pytest.approx(0.0)
    assert brier_score([Decimal(0)], [1]) == pytest.approx(1.0)
    assert brier_score([Decimal("0.5"), Decimal("0.5")], [1, 0]) == pytest.approx(0.25)


def test_interval_coverage():
    assert interval_coverage([Decimal(10)], [Decimal(9)], [Decimal(11)]) == pytest.approx(1.0)
    assert interval_coverage([Decimal(12)], [Decimal(9)], [Decimal(11)]) == pytest.approx(0.0)


def test_skill_score():
    assert skill_score(8.0, 10.0) == pytest.approx(0.2)
    assert skill_score(10.0, 10.0) == pytest.approx(0.0)
    assert skill_score(5.0, 0.0) == 0.0  # degenerate baseline guarded


# --------------------------------------------------------------------------- #
# Integration: evaluate_forecast now exposes distributional metrics
# --------------------------------------------------------------------------- #
def _make_run_and_actuals():
    t0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    cutoff = t0 - timedelta(minutes=1)
    candles = [
        ForecastCandle(
            open_time=t0, close_time=t0 + timedelta(hours=1),
            predicted_open=Decimal(10), predicted_high=Decimal(11),
            predicted_low=Decimal(9), predicted_close=Decimal(10),
            confidence=Decimal("0.55"), p_up=Decimal("0.7"),
        )
    ]
    run = ForecastRun(
        run_id=uuid4(), contract_code="BTC-USDT", horizon_minutes=60,
        model_version=ModelVersion.B1_QUANTILE, data_cutoff=cutoff,
        status="completed", candles=candles,
    )
    actual = [OHLCVBar(time=t0, open=Decimal(10), high=Decimal(13),
                       low=Decimal(9), close=Decimal(12), volume=Decimal(1))]
    return run, actual


def test_evaluate_forecast_populates_new_metrics():
    run, actual = _make_run_and_actuals()
    ev = evaluate_forecast(run, actual)
    assert ev.pinball_loss == pytest.approx(2.2 / 3)
    # actual close 12 is outside the [q10=9, q90=11] band -> coverage 0
    assert ev.coverage == pytest.approx(0.0)
    assert ev.brier == pytest.approx((0.7 - 1.0) ** 2)


def test_walk_forward_reports_pinball_skill():
    run, actual = _make_run_and_actuals()
    b1 = evaluate_forecast(run, actual)
    # B0 point forecast == actual close -> zero pinball, dominates B1 here
    b0_run = _make_b0_run(actual[0].close)
    b0 = evaluate_forecast(b0_run, actual)
    res = walk_forward_compare(b0, b1)
    assert res.skill_pinball is not None
    assert res.b1_beats_b0_pinball is (b1.pinball_loss < b0.pinball_loss)


def _make_b0_run(actual_close):
    t0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    cutoff = t0 - timedelta(minutes=1)
    candles = [
        ForecastCandle(
            open_time=t0, close_time=t0 + timedelta(hours=1),
            predicted_open=actual_close, predicted_high=actual_close,
            predicted_low=actual_close, predicted_close=actual_close,
            confidence=Decimal("0.5"), p_up=Decimal("0.5"),
        )
    ]
    return ForecastRun(
        run_id=uuid4(), contract_code="BTC-USDT", horizon_minutes=60,
        model_version=ModelVersion.B0_NAIVE, data_cutoff=cutoff,
        status="completed", candles=candles,
    )

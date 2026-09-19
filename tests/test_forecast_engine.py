from datetime import datetime, timedelta, timezone
from decimal import Decimal

from forecast_engine.core import Candle, run_baseline_forecast
from forecast_engine.evaluator import evaluate_forecast


def candle(index: int, close: str) -> Candle:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=index)
    value = Decimal(close)
    return Candle(start, start + timedelta(minutes=1), value, value + 1, value - 1, value)


def test_b0_persists_last_close_and_produces_contiguous_horizon() -> None:
    result = run_baseline_forecast([candle(0, "100"), candle(1, "105")], horizon=2)

    assert result.model_version == "B0"
    assert [point.close for point in result.points] == [Decimal("105"), Decimal("105")]
    assert result.points[0].close_time == result.points[1].open_time


def test_b1_uses_bounded_drift() -> None:
    result = run_baseline_forecast(
        [candle(0, "100"), candle(1, "110")], horizon=1, model_version="B1"
    )

    assert result.points[0].close == Decimal("115.50")


def test_evaluation_uses_only_matching_post_cutoff_closed_candle() -> None:
    run = run_baseline_forecast(
        [candle(0, "100"), candle(1, "110")], horizon=2, model_version="B1"
    )
    actual = [
        candle(1, "999"),  # same open time as the cutoff candle: must not be used
        Candle(
            datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=2),
            datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=3),
            Decimal("110"),
            Decimal("121"),
            Decimal("109"),
            Decimal("120"),
        ),
    ]

    evaluation = evaluate_forecast(run, actual)

    assert evaluation.points_evaluated == 1
    assert evaluation.mae == Decimal("4.50")
    assert evaluation.directional_accuracy == Decimal("1")

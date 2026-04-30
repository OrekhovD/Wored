import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from predictions.evaluator import score_forecast


def test_score_forecast_rewards_close_prediction():
    score = score_forecast(
        base_price=100.0,
        predicted_price=101.0,
        predicted_change_pct=1.0,
        actual_price=100.9,
    )

    assert score.direction_match is True
    assert score.accuracy_score == 90.0
    assert score.failure_score == 10.0


def test_score_forecast_returns_100_for_exact_change_match():
    score = score_forecast(
        base_price=100.0,
        predicted_price=102.0,
        predicted_change_pct=2.0,
        actual_price=102.0,
    )

    assert score.direction_match is True
    assert score.accuracy_score == 100.0
    assert score.failure_score == 0.0


def test_score_forecast_penalizes_direction_mismatch():
    score = score_forecast(
        base_price=100.0,
        predicted_price=103.0,
        predicted_change_pct=3.0,
        actual_price=98.0,
    )

    assert score.direction_match is False
    assert score.accuracy_score == 0.0
    assert score.failure_score == 100.0

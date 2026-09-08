import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from predictions.scoring import score_forecast, closed_target_price


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


def test_current_candle_cannot_supply_actual_price():
    candles = [{"time": 3600, "close": 101}, {"time": 7200, "close": 999}]
    assert closed_target_price(candles, 7200, 3600, 7201) == 101
    assert closed_target_price(candles, 7500, 3600, 8000) is None


def test_missing_target_candle_is_not_replaced_with_older_data():
    assert closed_target_price([{"time": 0, "close": 100}], 7200, 3600, 12000) is None


def test_direction_bonus_cannot_saturate_imprecise_prediction():
    result = score_forecast(100, 101.5, 1.5, 101)
    assert result.direction_match
    assert result.accuracy_score == 50
    assert result.skill_vs_baseline == 0.5


def test_baseline_beats_wrong_forecast_and_zero_baseline_has_no_relative_skill():
    assert score_forecast(100, 105, 5, 101).skill_vs_baseline < 0
    assert score_forecast(100, 101, 1, 100).skill_vs_baseline is None

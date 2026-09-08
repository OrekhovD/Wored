"""Version 2 outcome metrics. Scores are heuristic, never calibrated probabilities."""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ForecastScore:
    direction_match: bool
    accuracy_score: float
    failure_score: float
    price_error_pct: float
    change_error_pct: float
    baseline_error_pct: float
    skill_vs_baseline: float | None


def score_forecast(base_price: float, predicted_price: float, predicted_change_pct: float,
                   actual_price: float) -> ForecastScore:
    if not all(math.isfinite(v) for v in (base_price, predicted_price, predicted_change_pct, actual_price)):
        raise ValueError("Forecast metrics require finite values")
    if min(base_price, predicted_price, actual_price) <= 0:
        raise ValueError("Forecast metrics require positive prices")
    actual_change = (actual_price - base_price) / base_price * 100
    expected_change = (predicted_price - base_price) / base_price * 100
    error = abs(actual_price - predicted_price) / actual_price * 100
    change_error = abs(actual_change - expected_change)
    baseline = abs(actual_price - base_price) / actual_price * 100
    # 1 percentage point error consumes the full 100-point heuristic scale.
    score = round(max(0.0, 100 - 100 * change_error), 2)
    def sign(value):
        return 1 if value > 1e-9 else -1 if value < -1e-9 else 0
    direction = sign(actual_change) == sign(expected_change)
    return ForecastScore(direction, score, round(100 - score, 2), error, change_error,
                         baseline, 1 - error / baseline if baseline > 0 else None)


def closed_target_price(candles: list[dict], target_ts: int, period_seconds: int, now_ts: float) -> float | None:
    # Legacy targets within a candle are explicitly evaluated at that candle's
    # close. No older/newer available candle is silently substituted across gaps.
    boundary = math.ceil(target_ts / period_seconds) * period_seconds
    if boundary > now_ts:
        return None
    expected_start = boundary - period_seconds
    for candle in candles:
        if candle["time"] == expected_start:
            price = float(candle["close"])
            return price if math.isfinite(price) and price > 0 else None
    return None

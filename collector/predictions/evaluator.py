import json
import logging
from dataclasses import dataclass
from typing import Optional

from htx.rest import get_symbol_ticker
from storage.postgres_client import (
    finalize_completed_forecast_requests,
    get_due_forecast_points,
    get_scored_forecast_points,
    save_forecast_point_evaluation,
)
from storage.redis_client import get_redis

log = logging.getLogger(__name__)

NEUTRAL_BAND_PCT = 0.15


@dataclass
class ForecastScore:
    actual_price: float
    actual_change_pct: float
    price_error_pct: float
    change_error_pct: float
    accuracy_score: float
    failure_score: float
    direction_match: bool
    verdict: str


def _normalize_direction(change_pct: float) -> int:
    if abs(change_pct) <= NEUTRAL_BAND_PCT:
        return 0
    return 1 if change_pct > 0 else -1


def _direction_label(change_pct: float) -> str:
    normalized = _normalize_direction(change_pct)
    if normalized > 0:
        return "rise"
    if normalized < 0:
        return "fall"
    return "flat"


def _compute_directional_accuracy(predicted_change_pct: float, actual_change_pct: float, direction_match: bool) -> float:
    if not direction_match:
        return 0.0

    scale = max(abs(actual_change_pct), abs(predicted_change_pct), NEUTRAL_BAND_PCT)
    if scale <= 0:
        return 100.0

    miss_ratio = min(abs(predicted_change_pct - actual_change_pct) / scale, 1.0)
    return round((1.0 - miss_ratio) * 100.0, 2)


def score_forecast(base_price: float, predicted_price: float, predicted_change_pct: float, actual_price: float) -> ForecastScore:
    actual_change_pct = ((actual_price - base_price) / base_price * 100.0) if base_price else 0.0
    price_error_pct = abs(predicted_price - actual_price) / actual_price * 100.0 if actual_price else 0.0
    change_error_pct = abs(predicted_change_pct - actual_change_pct)
    direction_match = _normalize_direction(predicted_change_pct) == _normalize_direction(actual_change_pct)

    accuracy_score = _compute_directional_accuracy(predicted_change_pct, actual_change_pct, direction_match)
    failure_score = round(100.0 - accuracy_score, 2)

    if accuracy_score == 100.0:
        verdict = "Perfect hit: direction and realized move matched exactly."
    elif direction_match and accuracy_score >= 75:
        verdict = "Strong hit: direction was correct and the realized move stayed close."
    elif direction_match and accuracy_score > 0:
        verdict = "Partial hit: direction was correct, but the move size drifted."
    else:
        verdict = (
            f"Direction miss: forecast called {_direction_label(predicted_change_pct)}, "
            f"market realized {_direction_label(actual_change_pct)}."
        )

    verdict += f" Success {accuracy_score:.2f}% / miss {failure_score:.2f}%."

    return ForecastScore(
        actual_price=round(actual_price, 8),
        actual_change_pct=round(actual_change_pct, 4),
        price_error_pct=round(price_error_pct, 4),
        change_error_pct=round(change_error_pct, 4),
        accuracy_score=accuracy_score,
        failure_score=failure_score,
        direction_match=direction_match,
        verdict=verdict,
    )


async def get_actual_price(symbol: str) -> Optional[float]:
    redis = get_redis()
    raw = await redis.get(f"ticker:{symbol.lower()}")
    if raw:
        try:
            payload = json.loads(raw)
            price = float(payload.get("price", 0.0))
            if price > 0:
                return price
        except Exception as exc:
            log.warning("Failed to parse ticker cache for %s: %s", symbol, exc)

    ticker = await get_symbol_ticker(symbol)
    if ticker:
        return float(ticker["price"])
    return None


async def evaluate_due_forecasts():
    due_points = await get_due_forecast_points(limit=200)
    if not due_points:
        return

    updated = 0
    for point in due_points:
        actual_price = await get_actual_price(point["symbol"])
        if not actual_price:
            continue

        score = score_forecast(
            base_price=float(point["base_price"]),
            predicted_price=float(point["predicted_price"]),
            predicted_change_pct=float(point["predicted_change_pct"]),
            actual_price=actual_price,
        )

        await save_forecast_point_evaluation(
            point_id=point["point_id"],
            actual_price=score.actual_price,
            actual_change_pct=score.actual_change_pct,
            price_error_pct=score.price_error_pct,
            change_error_pct=score.change_error_pct,
            accuracy_score=score.accuracy_score,
            failure_score=score.failure_score,
            direction_match=score.direction_match,
            verdict=score.verdict,
        )
        updated += 1

    if updated:
        await finalize_completed_forecast_requests()
        log.info("Evaluated %s due forecast point(s).", updated)


async def refresh_historical_forecast_scores(limit: int = 2000):
    scored_points = await get_scored_forecast_points(limit=limit)
    if not scored_points:
        return

    refreshed = 0
    for point in scored_points:
        actual_price = float(point["actual_price"] or 0.0)
        if actual_price <= 0:
            continue

        score = score_forecast(
            base_price=float(point["base_price"]),
            predicted_price=float(point["predicted_price"]),
            predicted_change_pct=float(point["predicted_change_pct"]),
            actual_price=actual_price,
        )
        await save_forecast_point_evaluation(
            point_id=point["point_id"],
            actual_price=score.actual_price,
            actual_change_pct=score.actual_change_pct,
            price_error_pct=score.price_error_pct,
            change_error_pct=score.change_error_pct,
            accuracy_score=score.accuracy_score,
            failure_score=score.failure_score,
            direction_match=score.direction_match,
            verdict=score.verdict,
        )
        refreshed += 1

    if refreshed:
        log.info("Refreshed %s historical forecast score(s) with directional accuracy.", refreshed)

"""
Forecast evaluator - checks due forecast points against actual HTX prices,
saves accuracy metrics to forecast_points and self-evaluation reports to forecast_reports.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import httpx
from predictions.scoring import score_forecast, closed_target_price
from services.market_data import PERIOD_SECONDS

try:  # runtime sys.path layout inside the collector image
    from storage.perp_candles import list_contracts, load_closes_at
except ImportError:  # repository imports
    from collector.storage.perp_candles import list_contracts, load_closes_at  # type: ignore

log = logging.getLogger(__name__)


async def _get_pool():
    from storage.postgres_client import get_pool
    return await get_pool()


def _normalize_symbol(symbol: str) -> str:
    """HTX API expects lowercase symbol without dash (e.g. 'btcusdt', not 'btc-usdt')."""
    return symbol.strip().lower().replace("-", "").replace("/", "")


def _contract_map(contracts: list[str]) -> dict[str, str]:
    """Invert the stored contract codes onto forecast symbols.

    Uses the same transform as ``forecast_workspace._symbol_for_contract``
    (``BTC-USDT`` -> ``btcusdt``), so a forecast row is matched to the contract
    the collector actually persists instead of a hard-coded guess.
    """
    return {_normalize_symbol(code): code for code in contracts}


async def _store_actuals(
    pool: asyncpg.Pool,
    contract_code: str | None,
    period: str,
    group_rows: list[asyncpg.Record],
) -> dict[int, float]:
    """Resolve realised prices from the persisted 1m perpetual series.

    ``trader_v1_perp_candles`` is the declared price source-of-truth for the
    learning loop, and the V3 forecast base price is taken from that same linear
    swap venue.  Resolving the realised fact from the store keeps prediction and
    outcome on one venue; the HTX spot REST path stays as a fallback for symbols
    the collector does not persist.  Bucket matching is exact, so a missing
    candle is never replaced by a neighbouring one.
    """
    seconds = PERIOD_SECONDS.get(period)
    if contract_code is None or seconds is None:
        return {}
    buckets: dict[datetime, list[int]] = {}
    for row in group_rows:
        target_ts = int(row["target_time"].replace(tzinfo=timezone.utc).timestamp())
        boundary = math.ceil(target_ts / seconds) * seconds
        bucket = datetime.fromtimestamp(boundary - 60, timezone.utc)
        buckets.setdefault(bucket, []).append(row["point_id"])
    closes = await load_closes_at(pool, contract_code, list(buckets))
    actuals: dict[int, float] = {}
    for bucket, point_ids in buckets.items():
        close = closes.get(bucket)
        if close is None:
            continue
        value = float(close)
        if not math.isfinite(value) or value <= 0:
            continue
        for point_id in point_ids:
            actuals[point_id] = value
    return actuals


async def _fetch_htx_kline(symbol: str, period: str = "60min", size: int = 1) -> list[dict[str, Any]]:
    """Fetch latest kline(s) from HTX for a symbol/period."""
    normalized = _normalize_symbol(symbol)
    url = "https://api.huobi.pro/market/history/kline"
    params = {"period": period, "size": size, "symbol": normalized}
    try:
        async with httpx.AsyncClient(timeout=20.0) as hc:
            resp = await hc.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") != "ok":
                return []
            out = []
            for item in data.get("data", []):
                out.append({
                    "time": item["id"],
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                    "volume": float(item.get("vol", 0)),
                })
            return list(reversed(out))
    except Exception as exc:
        log.warning("HTX kline fetch failed for %s %s: %s", symbol, period, exc)
        return []


async def evaluate_due_forecasts():
    """Find forecast points whose target_time has passed and evaluate them."""
    pool = await _get_pool()
    if not pool:
        log.warning("evaluate_due_forecasts: no DB pool")
        return

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                fp.id AS point_id,
                fp.request_id,
                fp.model_run_id,
                fmr.agent_role,
                fp.step_index,
                fp.forecast_hour,
                fp.target_time,
                fp.predicted_price,
                fp.predicted_change_pct,
                fp.predicted_low,
                fp.predicted_high,
                fp.confidence,
                fp.rationale,
                fr.symbol,
                fr.base_price,
                fr.base_timeframe
            FROM forecast_points fp
            JOIN forecast_requests fr ON fr.id = fp.request_id
            JOIN forecast_model_runs fmr ON fmr.id = fp.model_run_id
            WHERE fp.evaluated_at IS NULL
              AND fp.target_time <= NOW() AT TIME ZONE 'UTC'
            ORDER BY fp.target_time DESC
            LIMIT 200
            """
        )

    if not rows:
        return

    # Group by symbol/period to batch the price lookups per group.
    groups: dict[tuple[str, str], list[asyncpg.Record]] = {}
    for row in rows:
        key = (row["symbol"], row["base_timeframe"] or "60min")
        groups.setdefault(key, []).append(row)

    contracts = _contract_map(await list_contracts(pool))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for (symbol, period), group_rows in groups.items():
        # Store first: the persisted series is the same venue the forecast was
        # priced from and is not capped by the ~33h HTX reach-back.  HTX spot
        # remains the fallback for symbols the collector does not persist.
        actuals = await _store_actuals(pool, contracts.get(_normalize_symbol(symbol)), period, group_rows)
        candles: list[dict[str, Any]] | None = None
        for row in group_rows:
            target_ts = int(row["target_time"].replace(tzinfo=timezone.utc).timestamp())
            actual_close = actuals.get(row["point_id"])
            if actual_close is None:
                seconds = PERIOD_SECONDS.get(period)
                if seconds is None:
                    log.warning("evaluate_due_forecasts: unknown period %s for %s; skipping", period, symbol)
                    continue
                if candles is None:
                    try:
                        candles = await _fetch_htx_kline(symbol, period, size=200)
                    except Exception as exc:
                        log.warning("evaluate_due_forecasts: HTX fallback failed for %s %s: %s",
                                    symbol, period, exc)
                        candles = []
                if not candles:
                    continue
                actual_close = closed_target_price(
                    candles, target_ts, seconds, datetime.now(timezone.utc).timestamp(),
                )
            if actual_close is None:
                continue

            predicted = float(row["predicted_price"])
            base_price = float(row["base_price"]) if row["base_price"] else predicted
            actual_change_pct = ((actual_close - base_price) / base_price * 100.0) if base_price else 0.0
            price_error_pct = abs(actual_close - predicted) / actual_close * 100.0 if actual_close else 0.0
            change_error_pct = abs(actual_change_pct - float(row["predicted_change_pct"]))
            direction_match = (actual_change_pct >= 0) == (float(row["predicted_change_pct"]) >= 0)

            low = row["predicted_low"]
            high = row["predicted_high"]
            in_range = None
            if low is not None and high is not None:
                in_range = float(low) <= actual_close <= float(high)

            outcome = score_forecast(base_price, predicted, float(row["predicted_change_pct"]), actual_close)
            score = outcome.accuracy_score
            direction_match = outcome.direction_match
            change_error_pct = outcome.change_error_pct

            verdict_parts = ["metrics_v2"]
            if direction_match:
                verdict_parts.append("direction_match")
            if in_range:
                verdict_parts.append("in_range")
            if score >= 90:
                verdict_parts.append("excellent")
            elif score >= 70:
                verdict_parts.append("good")
            elif score >= 50:
                verdict_parts.append("fair")
            else:
                verdict_parts.append("poor")
            verdict = " ".join(verdict_parts)

            try:
                async with pool.acquire() as conn, conn.transaction():
                    await conn.execute(
                        """
                        UPDATE forecast_points
                        SET actual_price = $1,
                            actual_change_pct = $2,
                            price_error_pct = $3,
                            change_error_pct = $4,
                            accuracy_score = $5,
                            direction_match = $6,
                            in_range = $7,
                            verdict = $8,
                            evaluated_at = $9,
                            metrics_version = 2,
                            failure_score = 100 - $5::numeric,
                            baseline_error_pct = $11,
                            skill_vs_baseline = $12
                        WHERE id = $10 AND evaluated_at IS NULL
                        """,
                        actual_close,
                        actual_change_pct,
                        price_error_pct,
                        change_error_pct,
                        round(score, 2),
                        direction_match,
                        in_range,
                        verdict,
                        now,
                        row["point_id"],
                        outcome.baseline_error_pct,
                        outcome.skill_vs_baseline,
                    )
                    await conn.execute(
                        """
                        INSERT INTO forecast_reports (
                            request_id, model_run_id, agent_role, evaluated_at, step_index,
                            target_time, factual_price, error_pct, in_range,
                            confidence_before, reason_text
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                        ON CONFLICT (model_run_id, step_index) DO UPDATE SET
                            factual_price = EXCLUDED.factual_price,
                            error_pct = EXCLUDED.error_pct,
                            in_range = EXCLUDED.in_range,
                            confidence_before = EXCLUDED.confidence_before,
                            reason_text = EXCLUDED.reason_text,
                            evaluated_at = EXCLUDED.evaluated_at
                        """,
                        row["request_id"],
                        row["model_run_id"],
                        row.get("agent_role", "neutral"),
                        now,
                        row["step_index"],
                        row["target_time"],
                        actual_close,
                        price_error_pct,
                        in_range,
                        row["confidence"],
                        verdict,
                    )
            except Exception as exc:
                log.warning("Failed to update forecast point %s: %s", row["point_id"], exc)

    log.info("evaluate_due_forecasts: evaluated %s due points", len(rows))


async def refresh_historical_forecast_scores():
    raise RuntimeError("Historical forecast scores are immutable; use an explicit versioned evaluation migration")


async def regenerate_hourly_correction():
    raise RuntimeError("In-place corrections are disabled; create a new forecast request")

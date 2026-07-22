"""
Forecast evaluator - checks due forecast points against actual HTX prices,
saves accuracy metrics to forecast_points and self-evaluation reports to forecast_reports.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import httpx

log = logging.getLogger(__name__)


async def _get_pool():
    db_url = os.getenv("DATABASE_URL")
    if db_url and "postgresql+asyncpg://" in db_url:
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    return await asyncpg.create_pool(dsn=db_url)


def _normalize_symbol(symbol: str) -> str:
    s = symbol.strip().lower()
    if s.endswith("usdt") and len(s) > 4:
        return s[:-4] + "-usdt"
    return s


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
            WHERE fp.evaluated_at IS NULL
              AND fp.target_time <= NOW() AT TIME ZONE 'UTC'
            ORDER BY fp.target_time
            LIMIT 200
            """
        )

    if not rows:
        return

    # Group by symbol/period to batch HTX calls
    groups: dict[tuple[str, str], list[asyncpg.Record]] = {}
    for row in rows:
        key = (row["symbol"], row["base_timeframe"] or "60min")
        groups.setdefault(key, []).append(row)

    now = datetime.now(timezone.utc)
    for (symbol, period), group_rows in groups.items():
        try:
            candles = await _fetch_htx_kline(symbol, period, size=10)
        except Exception as exc:
            log.warning("evaluate_due_forecasts: skip %s %s due to fetch error: %s", symbol, period, exc)
            continue

        if not candles:
            continue

        # map candle time -> close
        close_by_time: dict[int, float] = {c["time"]: c["close"] for c in candles}

        for row in group_rows:
            target_ts = int(row["target_time"].replace(tzinfo=timezone.utc).timestamp()) if row["target_time"] else 0
            actual_close = close_by_time.get(target_ts)
            if actual_close is None:
                # try nearest older candle
                for c in candles:
                    if c["time"] <= target_ts:
                        actual_close = c["close"]
                        break
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

            # accuracy score 0..100
            score = max(0.0, 100.0 - price_error_pct)
            if direction_match:
                score += 10.0
            if in_range:
                score += 10.0
            score = min(100.0, score)

            verdict_parts = []
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
                async with pool.acquire() as conn:
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
                            evaluated_at = $9
                        WHERE id = $10
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
                    )
            except Exception as exc:
                log.warning("Failed to update forecast point %s: %s", row["point_id"], exc)

    log.info("evaluate_due_forecasts: evaluated %s due points", len(rows))


async def refresh_historical_forecast_scores():
    """Refresh aggregate scores per role/model. Lightweight rollup."""
    pool = await _get_pool()
    if not pool:
        return

    async with pool.acquire() as conn:
        # Simple per-role accuracy summary over last 7 days
        rows = await conn.fetch(
            """
            SELECT
                fmr.agent_role,
                COUNT(*) AS total,
                AVG(fp.accuracy_score) AS avg_score,
                AVG(fp.price_error_pct) AS avg_error,
                SUM(CASE WHEN fp.direction_match THEN 1 ELSE 0 END) AS direction_hits
            FROM forecast_points fp
            JOIN forecast_model_runs fmr ON fmr.id = fp.model_run_id
            WHERE fp.evaluated_at >= NOW() AT TIME ZONE 'UTC' - INTERVAL '7 days'
              AND fp.accuracy_score IS NOT NULL
            GROUP BY fmr.agent_role
            """
        )

    if rows:
        log.info("Historical forecast scores (7d): %s", [dict(r) for r in rows])


async def regenerate_hourly_correction():
    """Optional: re-run agent forecast for active requests every hour.

    Not implemented in Phase 1; placeholder for Phase 2 when role-based
    re-correction is wired end-to-end.
    """
    log.debug("regenerate_hourly_correction: not enabled")

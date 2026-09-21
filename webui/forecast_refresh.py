"""Session forecast auto-refresh — keeps the Trader Deck's forecast live.

Background (Trader review, item B): nothing produced new ``forecast_requests``
on a schedule. The collector only *evaluates* due forecasts
(``collector/main.py`` → ``evaluate_due_forecasts``), so once the last completed
request aged past its horizon the Trader tab was left with an expired overlay —
and previously with a silently broken chart.

This module owns the *decision*, not the AI call: it inspects Postgres, decides
whether a fresh session forecast is due, and hands the work to an injected
enqueue callback (in production: ``app.queue_prediction``), so the durable
queue, the worker and the deadline rules stay in one place.

Configuration (see docs/ENV-REGISTRY.md):
  FORECAST_AUTO_REFRESH              enable/disable the loop (default true)
  FORECAST_AUTO_REFRESH_SYMBOL       symbol to keep covered (default btcusdt)
  FORECAST_AUTO_REFRESH_HOURS        horizon in hours (default 4)
  FORECAST_AUTO_REFRESH_TIMEFRAME    base timeframe (default 60min)
  FORECAST_AUTO_REFRESH_DEPTH        analyst depth (default 3)
  FORECAST_AUTO_REFRESH_INTERVAL     poll interval seconds (default 60)
  FORECAST_AUTO_REFRESH_MAX_PER_DAY  budget cap per 24h (default 6)
  FORECAST_AUTO_REFRESH_COOLDOWN     min seconds between requests (default 900)
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from prediction_timeframes import normalize_period, period_to_minutes

log = logging.getLogger(__name__)

DEFAULT_SYMBOL = "btcusdt"
DEFAULT_HOURS = 4
DEFAULT_TIMEFRAME = "60min"
DEFAULT_DEPTH = 3
DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_MAX_PER_DAY = 6
DEFAULT_COOLDOWN_SECONDS = 900

# Requests created by this loop are tagged with this ``source`` so the budget
# guard can tell them apart from operator-triggered ones.
AUTO_SOURCE = "auto-trader-session"


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize DB timestamps: forecast_points are naive UTC, timestamptz are aware."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_int(raw: str | None, default: int) -> int:
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RefreshDecision:
    should_run: bool
    reason: str


def decide_refresh(
    *,
    now: datetime,
    last_target: datetime | None,
    in_flight: int,
    auto_requests_24h: int,
    max_per_day: int,
    last_auto_at: datetime | None,
    cooldown_seconds: int,
    enabled: bool,
) -> RefreshDecision:
    """Pure refresh policy — no I/O, so every branch is testable.

    Order matters: safety guards (disabled / in-flight / budget / cooldown)
    short-circuit before we look at staleness, so a broken pipeline can never
    turn into an unbounded AI spend loop.
    """
    if not enabled:
        return RefreshDecision(False, "disabled")
    if in_flight > 0:
        return RefreshDecision(False, "job_in_flight")
    if auto_requests_24h >= max_per_day:
        return RefreshDecision(False, "budget_exhausted")

    now_utc = _as_utc(now)
    if last_auto_at is not None:
        since_last = (now_utc - _as_utc(last_auto_at)).total_seconds()
        if since_last < cooldown_seconds:
            return RefreshDecision(False, "cooldown")

    if last_target is None:
        return RefreshDecision(True, "no_forecast")
    if _as_utc(last_target) <= now_utc:
        return RefreshDecision(True, "expired")
    return RefreshDecision(False, "covered")


# ─── SQL ──────────────────────────────────────────────────────────────────

LATEST_COMPLETED_TARGET_SQL = (
    "SELECT r.id, max(p.target_time) AS last_target "
    "FROM forecast_requests r JOIN forecast_points p ON p.request_id = r.id "
    "WHERE r.symbol = $1 AND r.status = 'completed' "
    "GROUP BY r.id ORDER BY r.id DESC LIMIT 1"
)
IN_FLIGHT_SQL = (
    "SELECT count(*) AS n FROM forecast_jobs j "
    "JOIN forecast_requests r ON r.id = j.request_id "
    "WHERE r.symbol = $1 AND j.state IN ('queued','running')"
)
AUTO_BUDGET_SQL = (
    "SELECT count(*) AS n, max(created_at) AS last_at FROM forecast_requests "
    "WHERE symbol = $1 AND source = $2 AND created_at >= $3"
)


async def run_forecast_refresh_cycle(
    pool: Any,
    enqueue_fn: Callable[[str, int, str, int], Awaitable[Any]],
    *,
    symbol: str = DEFAULT_SYMBOL,
    horizon_hours: int = DEFAULT_HOURS,
    base_timeframe: str = DEFAULT_TIMEFRAME,
    depth: int = DEFAULT_DEPTH,
    max_per_day: int = DEFAULT_MAX_PER_DAY,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    enabled: bool = True,
    now: datetime | None = None,
) -> str:
    """Evaluate one cycle and enqueue a session forecast when due.

    Returns the decision reason (``"enqueued"`` when a request was created).
    Never raises: a refresh loop must not take the dashboard down with it.
    """
    now_utc = _as_utc(now) or datetime.now(timezone.utc)
    # Steps, not hours: a 4h horizon is 4 bars on 60min but 1 bar on 4hour.
    step_minutes = period_to_minutes(normalize_period(base_timeframe))
    horizon_steps = max(1, int(round(horizon_hours * 60 / step_minutes)))

    async with pool.acquire() as conn:
        target_row = await conn.fetchrow(LATEST_COMPLETED_TARGET_SQL, symbol)
        flight_row = await conn.fetchrow(IN_FLIGHT_SQL, symbol)
        # forecast_requests.created_at / forecast_points.target_time are naive UTC
        # (verified against the app log for request #135), while the Postgres
        # session runs in Asia/Bangkok. asyncpg rejects an aware datetime for a
        # "timestamp without time zone" comparison, so hand it naive UTC.
        budget_row = await conn.fetchrow(
            AUTO_BUDGET_SQL, symbol, AUTO_SOURCE,
            (now_utc - timedelta(hours=24)).replace(tzinfo=None),
        )

    decision = decide_refresh(
        now=now_utc,
        last_target=target_row["last_target"] if target_row else None,
        in_flight=int(flight_row["n"]) if flight_row else 0,
        auto_requests_24h=int(budget_row["n"]) if budget_row else 0,
        max_per_day=max_per_day,
        last_auto_at=_as_utc(budget_row["last_at"]) if budget_row else None,
        cooldown_seconds=cooldown_seconds,
        enabled=enabled,
    )
    if not decision.should_run:
        log.debug("forecast auto-refresh skipped for %s: %s", symbol, decision.reason)
        return decision.reason

    result = await enqueue_fn(symbol, horizon_steps, base_timeframe, depth)
    request_id = None
    if isinstance(result, dict):
        request_id = result.get("id") or result.get("request_id")
    log.info("forecast auto-refresh enqueued %s for %s (reason=%s)",
             f"#{request_id}" if request_id else "request", symbol, decision.reason)
    return "enqueued"


async def forecast_refresh_loop(app: Any, enqueue_fn: Callable[..., Awaitable[Any]]) -> None:
    """Long-running task started next to ``forecast_worker`` in the app lifespan."""
    symbol = os.getenv("FORECAST_AUTO_REFRESH_SYMBOL", DEFAULT_SYMBOL).strip() or DEFAULT_SYMBOL
    horizon_hours = _parse_int(os.getenv("FORECAST_AUTO_REFRESH_HOURS"), DEFAULT_HOURS)
    base_timeframe = os.getenv("FORECAST_AUTO_REFRESH_TIMEFRAME", DEFAULT_TIMEFRAME).strip() or DEFAULT_TIMEFRAME
    depth = _parse_int(os.getenv("FORECAST_AUTO_REFRESH_DEPTH"), DEFAULT_DEPTH)
    interval = _parse_int(os.getenv("FORECAST_AUTO_REFRESH_INTERVAL"), DEFAULT_INTERVAL_SECONDS)
    max_per_day = _parse_int(os.getenv("FORECAST_AUTO_REFRESH_MAX_PER_DAY"), DEFAULT_MAX_PER_DAY)
    cooldown = _parse_int(os.getenv("FORECAST_AUTO_REFRESH_COOLDOWN"), DEFAULT_COOLDOWN_SECONDS)
    interval = max(10, min(interval, 3600))

    log.info("forecast auto-refresh loop started: symbol=%s horizon=%dh every=%ds max/day=%d",
             symbol, horizon_hours, interval, max_per_day)
    while True:
        pool = getattr(app.state, "pg_pool", None)
        # Re-read the switch every cycle so the loop can be stopped from the env
        # (or a container restart) without dropping the task.
        enabled = _parse_bool(os.getenv("FORECAST_AUTO_REFRESH"), default=True)
        if pool is not None and enabled:
            try:
                await run_forecast_refresh_cycle(
                    pool, enqueue_fn,
                    symbol=symbol, horizon_hours=horizon_hours,
                    base_timeframe=base_timeframe, depth=depth,
                    max_per_day=max_per_day, cooldown_seconds=cooldown,
                    enabled=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — the loop must survive anything
                log.warning("forecast auto-refresh cycle failed: %s: %s", type(exc).__name__, exc)
        await asyncio.sleep(interval)

"""V3 forecast *command*: ``POST /api/v3/forecasts`` + request status read-back.

ТЗ V3 §55 / residual gap G2.  The read-model (``forecast_workspace``) can only
*show* stored forecasts; the workspace had no V3 way to *ask* for one, so a chart
selection had to fall back to the legacy ``POST /api/forecast/quick`` form, whose
pattern context is built by ``app.fetch_klines`` → HTX **spot**
``/market/history/kline`` and is labelled ``spot_snapshot``.  A perpetual
instrument must never be forecast from a spot series (ТЗ §51), so this module
derives the request from ``trader_v1_perp_candles`` — the same validated
closed-candle source the V3 chart renders — and passes the exact V3 parameters to
the existing durable queue.

Guarantees:

  * **Acknowledgement, not a result** (§47): ``202`` carries
    ``accepted=true, completed=false``.  Nothing here may declare a forecast
    finished; the caller reads the domain (``/api/v3/forecast/{id}``) after the
    worker commits.
  * **Base = last CLOSED fact candle** (§57): not bid/ask/mark, never the forming
    period.  ``base_snapshot_id`` names that point so chart, table and export can
    prove they used the same base.
  * **Horizon↔period matrix enforced *before* the request** (§59), together with
    the exact predicted-candle count.
  * **Fail-closed on history quality**: a short or gapped perpetual series is a
    ``503`` with required/available counts and ``last_good_end_at`` — never a
    silently shortened history and never a spot backfill.
  * **Idempotent**: ``Idempotency-Key`` + advisory transaction lock.  Same key and
    same parameters replay the original request (``202``); same key with
    different parameters is a ``409``.
  * **Decimal strings, ISO-8601 UTC.**

Staged scope (recorded in ``docs/V3-ACCEPTANCE-MATRIX-20260930.md``): the router is
still **not mounted** in ``webui/app.py``, so these routes do not exist in the
running service.  The additive identity columns this module writes
(``db/migrations/20260930_forecast_request_v3_identity.sql``, mirrored in
``forecast_schema.PREDICTION_TABLES_SQL`` for the startup path) make the stored row
self-describing.  What is still open: the queue worker builds its *pattern
context* from spot klines, so ``market_data_source`` describes what this command
validated and stored — not what the model learned from — until the runner is given
a perpetual-context path.  The base price and the candle history validated here are
already perpetual-only.  Two caveats stay open: because the startup mirror runs with
every ``webui`` boot, those nullable columns land in the live database only at the
*next* restart (not performed here), and the DDL has so far been exercised only by
source-contract tests — never against a real Postgres.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from forecast_input import validate_idempotency_key
from forecast_queue import COMPLETED, EXPIRED, FAILED, QUEUED, enqueue
from forecast_workspace import (
    HORIZON_ALLOWED_PERIODS,
    HORIZON_SECONDS,
    PERIOD_SECONDS,
    _symbol_for_contract,
    forecast_candle_count,
    is_compatible,
)
from instrument_registry import InstrumentRegistry, InstrumentSpec, load_registry
from market_workspace import (
    DERIVE_BASE,
    PERIOD_TO_STORED,
    STORED_SECONDS,
    CandleRow,
    CandleSourceError,
    _require_spec,
    aggregate_candles,
    detect_gaps,
    validate_candle_rows,
)
from prediction_timeframes import normalize_period

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3", tags=["forecast-v3-command"])

# The legacy runner consumes a 36-step recent window (app.build_prediction_context
# ``history_candles[-36:]``); the same floor is used here as an honest "enough
# history to ask" guard, expressed in the *requested* period, not in spot bars.
MIN_HISTORY_CANDLES = 36
# ``app.ALLOWED_PREDICTION_HORIZONS`` is range(1, 49); a V3 request must stay
# inside the budget the worker can actually execute.
MAX_HORIZON_STEPS = 48
DEFAULT_DEPTH = 3
COMMAND_SOURCE = "market-workspace-v3"
HISTORY_TABLE = "trader_v1_perp_candles"

# ``forecast_requests.status`` is the legacy lifecycle column; the V3 status
# endpoint speaks the normalized vocabulary of ``forecast_queue``.
_LEGACY_STATE_MAP = {
    "pending": QUEUED,
    "active": "running",
    "completed": COMPLETED,
    "failed": FAILED,
}
_TERMINAL_STATES = frozenset({COMPLETED, FAILED, EXPIRED})


def _db_ts(value: datetime) -> datetime:
    """``TIMESTAMP WITHOUT TIME ZONE`` columns take naive UTC (app.to_db_timestamp)."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _iso_utc(value: Any) -> str | None:
    """Render a DB timestamp as ISO-8601 UTC; naive rows are already UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _requested_by(request: Request) -> str:
    """Session owner for audit; the command works without a session (API client).

    Read from ``scope`` rather than ``request.session``: the property asserts when
    ``SessionMiddleware`` is absent, and a machine caller must still be served.
    """
    session = request.scope.get("session")
    if isinstance(session, dict):
        username = session.get("username")
        if isinstance(username, str) and username.strip():
            return username.strip()
    return "telegram-admin"


def _pool(request: Request):
    pool = getattr(request.app.state, "pg_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail={
                "reason_code": "history_unavailable",
                "message": "PostgreSQL pool is not connected",
                "last_good_end_at": None,
            },
        )
    return pool


def _payload_of(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _v3_block(payload: dict[str, Any]) -> dict[str, Any]:
    block = payload.get("v3")
    return block if isinstance(block, dict) else {}


def _intent_of(payload: dict[str, Any]) -> dict[str, Any]:
    """Parameters that define the request.

    Deliberately excludes ``base_price``/``base_time``: a retry with the same key
    must replay the original request even if a new candle closed in between, so
    the guard compares *intent*, not the re-derived market snapshot.
    """
    v3 = _v3_block(payload)
    return {
        "symbol": payload.get("symbol"),
        "horizon_steps": payload.get("horizon_steps"),
        "base_timeframe": payload.get("base_timeframe"),
        "depth": payload.get("depth"),
        "instrument_key": v3.get("instrument_key"),
        "period": v3.get("period"),
        "horizon": v3.get("horizon"),
    }


async def _closed_candles(
    pool: Any, spec: InstrumentSpec, period: str, limit: int, until: datetime
) -> list[dict[str, Any]]:
    """Read ``limit`` validated CLOSED candles for a perpetual, newest last.

    Mirrors ``market_workspace.market_candles`` (same table, same validation,
    same derivation rules) so the command and the chart can never disagree about
    what the last closed fact candle was.
    """
    stored = PERIOD_TO_STORED[period]
    base_period, factor = period, 1
    if stored is None:
        base_period, factor = DERIVE_BASE[period]
        stored = PERIOD_TO_STORED[base_period]
    assert stored is not None
    stored_seconds = STORED_SECONDS[stored]

    try:
        rows = await pool.fetch(
            f"""
            SELECT open_time, close_time, open, high, low, close, volume, source, venue
            FROM {HISTORY_TABLE}
            WHERE venue = $1 AND contract_code = $2 AND timeframe = $3 AND open_time <= $4
            ORDER BY open_time DESC
            LIMIT $5
            """,
            spec.venue, spec.contract_code, stored, until, limit * factor,
        )
    except Exception as exc:  # noqa: BLE001 — a source outage is not a silent fallback
        log.warning("v3 forecast-command candle query failed for %s/%s: %s", spec.instrument_key, period, exc)
        raise HTTPException(
            status_code=503,
            detail={
                "reason_code": "history_query_failed",
                "message": str(exc),
                "last_good_end_at": None,
            },
        ) from exc

    candles: list[CandleRow] = []
    for row in rows:
        if str(row["venue"]) != spec.venue:
            raise CandleSourceError("venue_mixture")
        candles.append(
            CandleRow(
                start_at=row["open_time"].astimezone(timezone.utc),
                end_at=row["close_time"].astimezone(timezone.utc),
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
                source=str(row["source"]),
            )
        )
    candles.reverse()

    validated = validate_candle_rows(candles, stored_seconds=stored_seconds)
    if factor > 1:
        validated = aggregate_candles(validated, factor=factor, period_seconds=stored_seconds * factor)
        for item in validated:
            item["derived_from"] = base_period
    return validated[-limit:]


def _reject(detail: dict[str, Any], status: int) -> None:
    raise HTTPException(status_code=status, detail=detail)


@router.post("/forecasts")
async def create_forecast(request: Request) -> JSONResponse:
    """Queue a perpetual forecast; returns an acknowledgement, never a result."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — malformed JSON is a client error
        _reject({"reason_code": "invalid_body", "message": "Ожидался JSON-объект"}, 400)
    if not isinstance(body, dict):
        _reject({"reason_code": "invalid_body", "message": "Тело запроса должно быть объектом"}, 400)

    instrument_key = body.get("instrument_key")
    if not isinstance(instrument_key, str) or not instrument_key.strip():
        _reject({"reason_code": "instrument_key_required", "message": "instrument_key is required"}, 400)
    instrument_key = instrument_key.strip()
    period = str(body.get("period") or "15m")
    horizon = str(body.get("horizon") or "1h")

    depth = body.get("depth", DEFAULT_DEPTH)
    if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 10:
        _reject(
            {"reason_code": "invalid_depth", "message": "depth must be an integer from 1 to 10", "allowed": [1, 10]},
            400,
        )

    registry: InstrumentRegistry = load_registry()
    spec = _require_spec(registry, instrument_key)  # 404 / 410 — spot never substitutes

    if period not in spec.supported_periods or period not in PERIOD_SECONDS:
        _reject(
            {
                "reason_code": "unsupported_period",
                "period": period,
                "supported": list(spec.supported_periods),
            },
            400,
        )
    if horizon not in HORIZON_SECONDS:
        _reject({"reason_code": "unsupported_horizon", "horizon": horizon, "supported": sorted(HORIZON_SECONDS)}, 400)
    if not is_compatible(horizon, period):
        _reject(
            {
                "reason_code": "incompatible_horizon_period",
                "horizon": horizon,
                "period": period,
                "suggested_periods": sorted(HORIZON_ALLOWED_PERIODS[horizon]),
                "exact_candles_if_compatible": None,
            },
            400,
        )
    horizon_steps = forecast_candle_count(horizon, period)
    assert horizon_steps is not None
    if horizon_steps > MAX_HORIZON_STEPS:
        _reject(
            {
                "reason_code": "horizon_step_budget_exceeded",
                "horizon_steps": horizon_steps,
                "max_horizon_steps": MAX_HORIZON_STEPS,
            },
            400,
        )

    raw_key = request.headers.get("Idempotency-Key", "")
    try:
        request_key = validate_idempotency_key(raw_key) if raw_key else None
    except ValueError as exc:
        _reject({"reason_code": "invalid_idempotency_key", "message": str(exc)}, 400)
    requested_by = _requested_by(request)
    scoped_key = f"{requested_by}:{COMMAND_SOURCE}:{request_key}" if request_key else None

    pool = _pool(request)
    now = datetime.now(timezone.utc)

    # ── fact history guard (perpetual only, closed candles only) ───────────────
    try:
        candles = await _closed_candles(pool, spec, period, MIN_HISTORY_CANDLES, now)
    except CandleSourceError as exc:
        _reject({"reason_code": exc.reason_code, "last_good_end_at": None}, 503)
    if len(candles) < MIN_HISTORY_CANDLES:
        _reject(
            {
                "reason_code": "insufficient_history",
                "required_candles": MIN_HISTORY_CANDLES,
                "available_candles": len(candles),
                "period": period,
                "last_good_end_at": candles[-1]["end_at"] if candles else None,
                "message": "Истории perpetual-свечей недостаточно; spot не используется как подставка",
            },
            503,
        )
    gaps = detect_gaps(candles, period_seconds=PERIOD_SECONDS[period])
    if gaps:
        _reject(
            {
                "reason_code": "missing_intervals",
                "required_candles": MIN_HISTORY_CANDLES,
                "available_candles": len(candles),
                "gaps": gaps[:5],
                "last_good_end_at": candles[-1]["end_at"],
                "message": "В истории есть разрывы; прогноз по разорванному ряду не выдаётся",
            },
            503,
        )

    base = candles[-1]
    base_time = str(base["end_at"])
    base_price = Decimal(str(base["close"]))
    base_snapshot_id = f"{spec.instrument_key}|{period}|{base_time}|{base['source']}"

    symbol = _symbol_for_contract(spec.contract_code)
    base_timeframe = normalize_period(period)
    horizon_hours = max(1, math.ceil(HORIZON_SECONDS[horizon] / 3600))

    job_payload: dict[str, Any] = {
        "symbol": symbol,
        "horizon_steps": horizon_steps,
        "base_price": float(base_price),
        "requested_by": requested_by,
        "source": COMMAND_SOURCE,
        "base_timeframe": base_timeframe,
        "depth": depth,
        "v3": {
            "schema": "ForecastRequestV3",
            "instrument_key": spec.instrument_key,
            "contract_code": spec.contract_code,
            "period": period,
            "horizon": horizon,
            "horizon_steps": horizon_steps,
            "forecast_candle_count": horizon_steps,
            "base_time": base_time,
            "base_price": format(base_price, "f"),
            "base_snapshot_id": base_snapshot_id,
            "history_candles": len(candles),
            "market_data_source": HISTORY_TABLE,
            "requested_at": now.isoformat(),
        },
    }

    accepted_at = now
    async with pool.acquire() as connection:
        async with connection.transaction():
            if scoped_key:
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", scoped_key
                )
                previous = await connection.fetchrow(
                    "SELECT j.request_id, j.payload, r.status, r.execution_state "
                    "FROM forecast_jobs j JOIN forecast_requests r ON r.id = j.request_id "
                    "WHERE j.idempotency_key = $1",
                    scoped_key,
                )
                if previous is not None:
                    saved = _payload_of(previous["payload"])
                    if _intent_of(saved) != _intent_of(job_payload):
                        _reject(
                            {
                                "reason_code": "idempotency_conflict",
                                "message": "Idempotency-Key уже использован с другими параметрами",
                                "request_id": str(previous["request_id"]),
                            },
                            409,
                        )
                    # A replay echoes the *original* accepted base, not a freshly
                    # re-derived one — otherwise the same key could report two
                    # different bases for one stored request.
                    saved_v3 = _v3_block(saved)
                    return _accepted_response(
                        request_id=int(previous["request_id"]),
                        spec_key=str(saved_v3.get("instrument_key") or spec.instrument_key),
                        period=str(saved_v3.get("period") or period),
                        horizon=str(saved_v3.get("horizon") or horizon),
                        horizon_steps=int(saved_v3.get("horizon_steps") or horizon_steps),
                        base_time=str(saved_v3.get("base_time") or base_time),
                        base_price=Decimal(str(saved_v3.get("base_price") or base_price)),
                        base_snapshot_id=str(saved_v3.get("base_snapshot_id") or base_snapshot_id),
                        execution_state=_normalized_state(previous["execution_state"], previous["status"]),
                        replayed=True,
                    )

            row = await connection.fetchrow(
                "INSERT INTO forecast_requests "
                "(symbol, horizon_hours, base_timeframe, depth, base_price, status, source, requested_by, as_of, "
                "instrument_key, period, horizon, horizon_steps, base_snapshot_id, market_data_source) "
                "VALUES ($1,$2,$3,$4,$5,'pending',$6,$7,$8,$9,$10,$11,$12,$13,$14) RETURNING id",
                symbol, horizon_hours, base_timeframe, depth, float(base_price),
                COMMAND_SOURCE, requested_by, _db_ts(accepted_at),
                spec.instrument_key, period, horizon, horizon_steps, base_snapshot_id, HISTORY_TABLE,
            )
            request_id = int(row["id"])
            await enqueue(connection, request_id, job_payload, scoped_key)

    return _accepted_response(
        request_id=request_id,
        spec_key=spec.instrument_key,
        period=period,
        horizon=horizon,
        horizon_steps=horizon_steps,
        base_time=base_time,
        base_price=base_price,
        base_snapshot_id=base_snapshot_id,
        execution_state=QUEUED,
        replayed=False,
    )


def _normalized_state(execution_state: Any, legacy_status: Any) -> str:
    if isinstance(execution_state, str) and execution_state:
        return execution_state
    return _LEGACY_STATE_MAP.get(str(legacy_status or ""), QUEUED)


def _accepted_response(
    *,
    request_id: int,
    spec_key: str,
    period: str,
    horizon: str,
    horizon_steps: int,
    base_time: str,
    base_price: Decimal,
    base_snapshot_id: str,
    execution_state: str,
    replayed: bool,
) -> JSONResponse:
    """§47: the 202 body is an acknowledgement — ``completed`` is always false."""
    return JSONResponse(
        {
            "accepted": True,
            "completed": False,
            "idempotent_replay": replayed,
            "request_id": str(request_id),
            "execution_state": execution_state,
            "instrument_key": spec_key,
            "period": period,
            "horizon": horizon,
            "forecast_candle_count": horizon_steps,
            "base_time": base_time,
            "base_price": format(base_price, "f"),
            "base_snapshot_id": base_snapshot_id,
            "market_data_source": HISTORY_TABLE,
            "status_path": f"/api/v3/forecasts/requests/{request_id}",
            "result_path": f"/api/v3/forecast/{request_id}",
            "message": "Прогноз принят в очередь; результат читается из domain, не из этого ответа",
        },
        status_code=202,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/forecasts/requests/{request_id}")
async def forecast_request_status(request: Request, request_id: str) -> JSONResponse:
    """Durable execution status of one command (no result numbers live here)."""
    if not request_id.isdigit():
        _reject({"reason_code": "invalid_request_id", "request_id": request_id}, 400)
    pool = _pool(request)
    row = await pool.fetchrow(
        """
        SELECT r.id, r.status, r.execution_state, r.evaluation_state, r.failure_code,
               r.as_of, r.valid_until, r.deadline_at,
               r.instrument_key, r.period, r.horizon, r.base_snapshot_id, r.market_data_source,
               j.error_code AS job_error_code, j.state AS job_state
        FROM forecast_requests r
        LEFT JOIN forecast_jobs j ON j.request_id = r.id
        WHERE r.id = $1
        """,
        int(request_id),
    )
    if row is None:
        _reject({"reason_code": "request_not_found", "request_id": request_id}, 404)

    state = _normalized_state(row["execution_state"], row["status"])
    completed = state == COMPLETED
    failure_code = row["failure_code"] or row["job_error_code"]
    return JSONResponse(
        {
            "request_id": str(row["id"]),
            "legacy_status": row["status"],
            "execution_state": state,
            "evaluation_state": row["evaluation_state"],
            "failure_code": failure_code,
            "accepted": True,
            "completed": completed,
            "terminal": state in _TERMINAL_STATES,
            "forecast_id": str(row["id"]) if completed else None,
            "result_path": f"/api/v3/forecast/{row['id']}" if completed else None,
            "as_of": _iso_utc(row["as_of"]),
            "valid_until": _iso_utc(row["valid_until"]),
            "deadline_at": _iso_utc(row["deadline_at"]),
            # V3 identity lives in the row since the additive migration
            # 20260930_forecast_request_v3_identity.sql.  A request that predates
            # it is reported as NULL with an explicit legacy marker — the status
            # endpoint never guesses an instrument it cannot prove.
            "instrument_key": row["instrument_key"],
            "period": row["period"],
            "horizon": row["horizon"],
            "base_snapshot_id": row["base_snapshot_id"],
            "market_data_source": row["market_data_source"],
            "identity_provenance": "row" if row["instrument_key"] else "legacy_row_no_v3_identity",
            "note": "Этот ответ — статус исполнения, а не результат прогноза (ТЗ §47)",
        },
        headers={"Cache-Control": "no-store"},
    )

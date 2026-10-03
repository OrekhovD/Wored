"""V3 forecast read-model: ``/api/v3/forecasts`` and ``/api/v3/forecast/{id}``.

ТЗ V3 §5 / P2 (MC-05…MC-08). This is the forecast half of the unified
``/workspace`` screen. It deliberately builds on the *existing* honest
aggregation in ``trader_api`` (``_role_votes`` + ``aggregate_forecast_steps``)
rather than invent a second one, so the q10/q50/q90 band, the "missing wick
degrades to close, never zero" rule and the role-coverage accounting stay a
single source of truth.

What this module guarantees (ТЗ §4, §5):

  * **No invented wicks.** The ensemble median path has open=close and its
    "high/low" are the spread across model voices — an uncertainty band, not a
    proven intra-candle extreme. So every interval is emitted with
    ``has_valid_ohlc=False``: the client must draw ``predicted_close`` as a
    line plus the ``band_low..band_high`` interval, never a candle with a
    fabricated body (MC-06).
  * **Horizon is bound to a compatible period.** 15m/1h/4h/24h each accept only
    the period set in ``HORIZON_ALLOWED_PERIODS`` (ТЗ §5 minimum matrix); an
    incompatible pair is rejected *before* a request with the suggested period
    and the exact forecast-candle count.
  * **Instrument identity.** ``instrument_key`` resolves through the registry;
    the perpetual's ``contract_code`` maps to the legacy forecast ``symbol``.
    Spot keys 404 (never substituted).
  * **Fail-closed, honest.** No completed forecast yields an empty interval
    list + ``no_verified_forecast`` reason, not a made-up price path.
  * **Decimal-strings + ISO-8601 UTC.** Numbers leave as fixed-point strings.

MC-08 (accuracy / coverage on holdout vs a baseline) is computed from stored
evaluation columns; an insufficient sample returns ``N/A`` (null), never a
spurious number.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from instrument_registry import InstrumentNotFound, load_registry
from trader_api import _role_votes, aggregate_forecast_steps

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3", tags=["forecast-v3"])

# ── horizon / period contract (ТЗ §5 minimum matrix) ──────────────────────────

PERIOD_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400,
}
HORIZON_SECONDS: dict[str, int] = {
    "15m": 900, "1h": 3600, "4h": 14400, "24h": 86400,
}
HORIZON_ALLOWED_PERIODS: dict[str, set[str]] = {
    "15m": {"1m", "5m", "15m"},
    "1h": {"5m", "15m", "1h"},
    "4h": {"15m", "1h", "4h"},
    "24h": {"1h", "4h", "1d"},
}
# A single forecast is trusted only once enough evaluated points exist; below
# this the accuracy block is N/A (ТЗ §6 "недостаточная выборка даёт N/A").
MIN_ACCURACY_SAMPLE = 5


def _dstr(value: Any) -> str | None:
    """Fixed-point Decimal string; None passes through, junk becomes None."""
    if value is None:
        return None
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not dec.is_finite():
        return None
    return format(dec.quantize(Decimal("0.0001")) if dec == dec else dec, "f")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _to_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    return None


def is_compatible(horizon: str, period: str) -> bool:
    if horizon not in HORIZON_ALLOWED_PERIODS or period not in PERIOD_SECONDS:
        return False
    return period in HORIZON_ALLOWED_PERIODS[horizon]


def forecast_candle_count(horizon: str, period: str) -> int | None:
    """Exact number of predicted candles for a horizon at a period, or None if
    the pair is incompatible (ТЗ §5: show the count before the request)."""
    if not is_compatible(horizon, period):
        return None
    return HORIZON_SECONDS[horizon] // PERIOD_SECONDS[period]


def _symbol_for_contract(contract_code: str) -> str:
    # ``BTC-USDT`` (linear swap, USDT-settled) → legacy forecast key ``btcusdt``.
    return contract_code.replace("-", "").lower()


# ── pure builder: rows in, ForecastIntervalPredictionV1 out ────────────────────


def build_forecast_prediction(
    *,
    instrument_key: str,
    contract_code: str,
    request_row: dict[str, Any] | None,
    points: list[dict[str, Any]],
    horizon: str,
    period: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    expected = forecast_candle_count(horizon, period)

    base_frame: dict[str, Any] = {
        "forecast_id": None,
        "instrument_key": instrument_key,
        "contract_code": contract_code,
        "period": period,
        "horizon": horizon,
        "base_time": None,
        "base_price": None,
        "generated_at": None,
        "target_at": None,
        "expires_at": None,
        "model_id": None,
        "role": None,
        "prompt_version": "unknown",
        "execution_state": "failed",
        "error_code": None,
        "coverage": None,
        "expected_candles": expected,
        "band_method": "ensemble_q10_q50_q90",
        "confidence_kind": "raw",
        "rationale": "",
        "origin": "model",
        "intervals": [],
    }

    if request_row is None or not points or expected is None:
        base_frame["error_code"] = (
            "incompatible_horizon_period" if expected is None else "no_verified_forecast"
        )
        base_frame["message"] = (
            "Нет проверенного прогноза: горизонт и период несовместимы."
            if expected is None
            else "Нет проверенного прогноза: завершённого расчёта для этого горизонта нет."
        )
        return base_frame

    steps, coverage_detail = aggregate_forecast_steps(_role_votes(points))
    if not steps:
        base_frame["execution_state"] = request_row.get("execution_state") or "stale"
        base_frame["error_code"] = "no_verified_forecast"
        base_frame["message"] = "Нет проверенного прогноза: точки расчёта не найдены."
        return base_frame

    per = PERIOD_SECONDS[period]
    base_price = _dstr(request_row.get("base_price"))
    base_time = _to_utc(request_row.get("created_at")) or now

    intervals: list[dict[str, Any]] = []
    prev_close = base_price
    for idx, s in enumerate(steps[:expected]):
        end_dt = datetime.fromtimestamp(int(s["time"]), tz=timezone.utc)
        start_dt = end_dt - timedelta(seconds=per)
        band_low = _dstr(min(s["c10"], s["c90"]))
        band_high = _dstr(max(s["c10"], s["c90"]))
        close_str = _dstr(s["close"])
        intervals.append(
            {
                "target_start_at": _iso(start_dt),
                "target_end_at": _iso(end_dt),
                "predicted_close": close_str,
                # First predicted bar opens at the base (last closed fact candle);
                # later bars open at the previous predicted close. This is a path
                # anchor, NOT a claimed tradeable open, so has_valid_ohlc stays False.
                "predicted_open": base_price if idx == 0 else prev_close,
                "predicted_high": None,  # ensemble has no proven intra-candle extreme
                "predicted_low": None,
                "band_low": band_low,
                "band_high": band_high,
                "has_valid_ohlc": False,
                "p_up": s.get("p_up"),
                "sigma": s.get("sigma"),
                "confidence": s.get("confidence"),
                "samples": s.get("samples"),
            }
        )
        prev_close = close_str

    coverage = round(len(intervals) / expected, 4) if expected else 0.0
    roles_present = coverage_detail.get("roles_present") or []
    models = "+".join(
        m for m in (
            coverage_detail.get("roles", {}).get(r, {}).get("model_id")
            for r in roles_present
        ) if m
    ) or None
    any_conf = any(i["confidence"] is not None for i in intervals)
    last = intervals[-1]

    return {
        **base_frame,
        "forecast_id": str(request_row.get("id")),
        "base_time": _iso(base_time),
        "base_price": base_price,
        "generated_at": _iso(base_time) or _iso(now),
        "target_at": last["target_end_at"],
        "expires_at": _iso(base_time + timedelta(seconds=HORIZON_SECONDS[horizon])),
        "model_id": models,
        "role": ",".join(roles_present) or "neutral",
        "prompt_version": str(request_row.get("plan_version") or "unknown"),
        "execution_state": request_row.get("execution_state") or "completed",
        "error_code": None,
        "coverage": coverage,
        "coverage_detail": coverage_detail,
        "confidence_kind": "calibrated" if any_conf else "raw",
        "rationale": str(request_row.get("rationale") or ""),
        "intervals": intervals,
    }


def compute_forecast_accuracy(points: list[dict[str, Any]]) -> dict[str, Any]:
    """MC-08: accuracy / coverage against a naive baseline over evaluated points.

    Only points that have both a prediction and a settled ``actual_price`` count
    toward the sample. Below ``MIN_ACCURACY_SAMPLE`` every metric is ``None``
    with ``verdict='N/A'`` — a small sample must not masquerade as skill.
    """
    errors: list[float] = []
    skills: list[float] = []
    in_range_hits = 0
    in_range_total = 0
    for p in points:
        if p.get("actual_price") is None:
            continue
        err = p.get("price_error_pct")
        if err is not None:
            try:
                errors.append(abs(float(err)))
            except (TypeError, ValueError):
                pass
        sk = p.get("skill_vs_baseline")
        if sk is not None:
            try:
                skills.append(float(sk))
            except (TypeError, ValueError):
                pass
        ir = p.get("in_range")
        if ir is not None:
            in_range_total += 1
            if ir:
                in_range_hits += 1

    sample_n = len(errors)
    if sample_n < MIN_ACCURACY_SAMPLE:
        return {
            "sample_n": sample_n,
            "mape": None,
            "coverage": None,
            "skill_vs_baseline": None,
            "verdict": "N/A",
            "reason": "insufficient_sample",
            "min_sample": MIN_ACCURACY_SAMPLE,
        }
    mape = round(sum(errors) / sample_n, 4)
    coverage = round(in_range_hits / in_range_total, 4) if in_range_total else None
    skill = round(sum(skills) / len(skills), 4) if skills else None
    return {
        "sample_n": sample_n,
        "mape": mape,
        "coverage": coverage,
        "skill_vs_baseline": skill,
        "verdict": "beats_baseline" if (skill is not None and skill > 0) else (
            "worse_than_baseline" if skill is not None else "unmeasured"
        ),
        "reason": None,
        "min_sample": MIN_ACCURACY_SAMPLE,
    }


# ── MC-08: deterministic holdout replay against a persistence baseline ─────────
#
# The stored ``skill_vs_baseline`` / ``baseline_error_pct`` columns are frequently
# NULL (only populated for ``metrics_version=2`` rows), so a holdout built from
# them alone silently degrades to "unmeasured".  For MC-08 we must reproduce the
# number *deterministically* from the raw predicted path and the realised fact,
# against a well-defined naive baseline: **persistence** — "price stays at
# ``base_price``".  If the ensemble median cannot beat a flat carry-forward, it
# has no demonstrated skill; a sample below ``MIN_ACCURACY_SAMPLE`` yields N/A.


def _fnum(value: Any) -> float | None:
    """Float coercion that treats NULL / NaN / inf as absent (no silent 0.0)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _holdout_samples(points: list[dict[str, Any]], base_price: Any) -> list[dict[str, Any]]:
    """One sample per predicted step whose target window has a realised fact.

    Uses the *same* honest median/band aggregation as the chart (so the number
    quoted for accuracy is the number drawn on screen), paired with the actual
    close at that step.  Emits model and persistence-baseline % error and
    whether the fact landed inside the q10..q90 band.
    """
    base = _fnum(base_price)
    steps, _ = aggregate_forecast_steps(_role_votes(points))
    actual_by_step: dict[int, float] = {}
    for p in points:
        st = p.get("step_index")
        if st is None:
            continue
        a = _fnum(p.get("actual_price"))
        if a is not None:
            actual_by_step.setdefault(int(st), a)

    samples: list[dict[str, Any]] = []
    for s in steps:
        a = actual_by_step.get(int(s["step"]))
        if a is None or a == 0:
            continue
        pred = s["close"]
        lo = min(s["c10"], s["c90"])
        hi = max(s["c10"], s["c90"])
        sample: dict[str, Any] = {
            "model_error_pct": abs(pred - a) / abs(a) * 100.0,
            "in_band": lo <= a <= hi,
        }
        if base is not None and base > 0:
            sample["baseline_error_pct"] = abs(base - a) / abs(a) * 100.0
        samples.append(sample)
    return samples


def summarize_holdout(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate holdout samples into the MC-08 block, or N/A when too few."""
    n = len(samples)
    base_errs = [s["baseline_error_pct"] for s in samples if "baseline_error_pct" in s]
    if n < MIN_ACCURACY_SAMPLE or not base_errs:
        return {
            "sample_n": n,
            "mape": None,
            "baseline_mape": None,
            "skill_vs_baseline": None,
            "coverage": None,
            "verdict": "N/A",
            "reason": "insufficient_sample" if n < MIN_ACCURACY_SAMPLE else "no_baseline",
            "min_sample": MIN_ACCURACY_SAMPLE,
            "baseline_method": "persistence_base_price",
            "replay": "deterministic",
        }
    mape = round(sum(s["model_error_pct"] for s in samples) / n, 4)
    baseline_mape = round(sum(base_errs) / len(base_errs), 4)
    band_hits = sum(1 for s in samples if s["in_band"])
    coverage = round(band_hits / n, 4)
    skill = round((baseline_mape - mape) / baseline_mape, 4) if baseline_mape else None
    if skill is None:
        verdict = "unmeasured"
    elif skill > 0:
        verdict = "beats_baseline"
    elif skill < 0:
        verdict = "worse_than_baseline"
    else:
        verdict = "matches_baseline"
    return {
        "sample_n": n,
        "mape": mape,
        "baseline_mape": baseline_mape,
        "skill_vs_baseline": skill,
        "coverage": coverage,
        "verdict": verdict,
        "reason": None,
        "min_sample": MIN_ACCURACY_SAMPLE,
        "baseline_method": "persistence_base_price",
        "replay": "deterministic",
    }


def replay_forecast_holdout(points: list[dict[str, Any]], base_price: Any) -> dict[str, Any]:
    """Deterministic MC-08 holdout for a single forecast's points."""
    return summarize_holdout(_holdout_samples(points, base_price))


# ── HTTP endpoints ─────────────────────────────────────────────────────────────


def _require_spec_key(registry, instrument_key: str):
    try:
        spec = registry.get(instrument_key)
    except InstrumentNotFound:
        raise HTTPException(
            status_code=404,
            detail={
                "reason_code": "instrument_not_registered",
                "message": f"{instrument_key!r} отсутствует в реестре; spot не подменяет perpetual",
            },
        ) from None
    return spec


def _pool(request: Request):
    pool = getattr(request.app.state, "pg_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=503,
            detail={"reason_code": "forecast_store_unavailable", "message": "PostgreSQL pool is not connected"},
        )
    return pool


@router.get("/forecasts")
async def list_forecasts(
    request: Request,
    instrument_key: str,
    horizon: str = "1h",
    period: str = "15m",
    limit: int = 6,
) -> JSONResponse:
    """Latest completed forecasts for one perpetual, sliced to a horizon/period."""
    registry = load_registry()
    spec = _require_spec_key(registry, instrument_key)
    if horizon not in HORIZON_SECONDS:
        raise HTTPException(
            status_code=400,
            detail={
                "reason_code": "unsupported_horizon",
                "supported": sorted(HORIZON_SECONDS),
            },
        )
    if not is_compatible(horizon, period):
        raise HTTPException(
            status_code=400,
            detail={
                "reason_code": "incompatible_horizon_period",
                "horizon": horizon,
                "period": period,
                "suggested_periods": sorted(HORIZON_ALLOWED_PERIODS[horizon]),
                "exact_candles_if_compatible": None,
            },
        )

    symbol = _symbol_for_contract(spec.contract_code)
    pool = _pool(request)
    now = datetime.now(timezone.utc)
    predictions: list[dict[str, Any]] = []
    try:
        rows = await pool.fetch(
            "SELECT id, symbol, base_price, horizon_hours, base_timeframe, created_at, "
            "execution_state, plan_version "
            "FROM forecast_requests WHERE symbol = $1 AND status = 'completed' "
            "ORDER BY created_at DESC LIMIT $2",
            symbol, limit,
        )
        for r in rows:
            points = await pool.fetch(
                "SELECT p.step_index, p.target_time, p.predicted_price, p.predicted_high, "
                "p.predicted_low, p.confidence, p.predicted_change_pct, "
                "p.model_run_id AS run_id, r.agent_role, r.model_id "
                "FROM forecast_points p "
                "LEFT JOIN forecast_model_runs r ON r.id = p.model_run_id "
                "WHERE p.request_id = $1 ORDER BY p.step_index, p.model_run_id",
                r["id"],
            )
            predictions.append(
                build_forecast_prediction(
                    instrument_key=spec.instrument_key,
                    contract_code=spec.contract_code,
                    request_row=dict(r),
                    points=[dict(p) for p in points],
                    horizon=horizon,
                    period=period,
                    now=now,
                )
            )
    except Exception as exc:  # noqa: BLE001 — surface as store outage, never fake data
        log.warning("v3 forecasts query failed for %s/%s: %s", instrument_key, horizon, exc)
        raise HTTPException(
            status_code=503,
            detail={"reason_code": "forecast_query_failed", "message": str(exc)},
        ) from exc

    if not predictions:
        predictions = [
            build_forecast_prediction(
                instrument_key=spec.instrument_key,
                contract_code=spec.contract_code,
                request_row=None,
                points=[],
                horizon=horizon,
                period=period,
                now=now,
            )
        ]

    return JSONResponse(
        {
            "schema_version": 1,
            "instrument_key": spec.instrument_key,
            "horizon": horizon,
            "period": period,
            "expected_candles": forecast_candle_count(horizon, period),
            "forecasts": predictions,
            "as_of": now.isoformat(),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/forecast/{forecast_id}")
async def get_forecast_detail(request: Request, forecast_id: int) -> JSONResponse:
    """Immutable single forecast + its settled-vs-forecast accuracy block (MC-07/08)."""
    pool = _pool(request)
    now = datetime.now(timezone.utc)
    try:
        req = await pool.fetchrow(
            "SELECT id, symbol, contract_code, base_price, horizon_hours, base_timeframe, "
            "created_at, execution_state, plan_version FROM forecast_requests WHERE id = $1",
            forecast_id,
        )
        if req is None:
            raise HTTPException(status_code=404, detail={"reason_code": "forecast_not_found"})
        points = await pool.fetch(
            "SELECT p.step_index, p.target_time, p.predicted_price, p.predicted_high, "
            "p.predicted_low, p.confidence, p.predicted_change_pct, "
            "p.actual_price, p.price_error_pct, p.skill_vs_baseline, p.in_range, "
            "p.model_run_id AS run_id, r.agent_role, r.model_id "
            "FROM forecast_points p "
            "LEFT JOIN forecast_model_runs r ON r.id = p.model_run_id "
            "WHERE p.request_id = $1 ORDER BY p.step_index, p.model_run_id",
            forecast_id,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("v3 forecast detail query failed for %s: %s", forecast_id, exc)
        raise HTTPException(
            status_code=503, detail={"reason_code": "forecast_query_failed", "message": str(exc)}
        ) from exc

    req = dict(req)
    point_dicts = [dict(p) for p in points]
    contract = req.get("contract_code") or (req.get("symbol", "").upper() or "UNKNOWN")
    horizon = "1h"  # detail view echoes the request's own horizon bucket if mappable
    hh = req.get("horizon_hours")
    if hh is not None:
        try:
            secs = int(hh) * 3600
            horizon = {900: "15m", 3600: "1h", 14400: "4h", 86400: "24h"}.get(secs, "24h")
        except (TypeError, ValueError):
            pass
    prediction = build_forecast_prediction(
        instrument_key=f"htx:linear-swap:{contract}",
        contract_code=contract,
        request_row=req,
        points=point_dicts,
        horizon=horizon,
        period=req.get("base_timeframe") or "1h",
        now=now,
    )
    prediction["accuracy"] = compute_forecast_accuracy(point_dicts)
    prediction["holdout"] = replay_forecast_holdout(point_dicts, req.get("base_price"))
    return JSONResponse(prediction, headers={"Cache-Control": "no-store"})


@router.get("/forecasts/accuracy")
async def forecasts_accuracy(
    request: Request,
    instrument_key: str,
    horizon: str = "1h",
    period: str = "15m",
    limit: int = 20,
) -> JSONResponse:
    """MC-08 holdout across the most recent completed forecasts.

    A *single* prediction is rarely enough to claim skill, so this pools every
    realised step across the last ``limit`` completed forecasts for the
    instrument/horizon and reports MAPE, the persistence baseline's MAPE, the
    skill of the model over that baseline, and band coverage.  A pooled sample
    below ``MIN_ACCURACY_SAMPLE`` yields ``N/A`` — never a spurious number.
    """
    registry = load_registry()
    spec = _require_spec_key(registry, instrument_key)
    if horizon not in HORIZON_SECONDS:
        raise HTTPException(
            status_code=400,
            detail={"reason_code": "unsupported_horizon", "supported": sorted(HORIZON_SECONDS)},
        )
    if not is_compatible(horizon, period):
        raise HTTPException(
            status_code=400,
            detail={
                "reason_code": "incompatible_horizon_period",
                "horizon": horizon,
                "period": period,
                "suggested_periods": sorted(HORIZON_ALLOWED_PERIODS[horizon]),
            },
        )

    symbol = _symbol_for_contract(spec.contract_code)
    pool = _pool(request)
    now = datetime.now(timezone.utc)
    samples: list[dict[str, Any]] = []
    forecasts_evaluated = 0
    try:
        rows = await pool.fetch(
            "SELECT id, base_price FROM forecast_requests "
            "WHERE symbol = $1 AND status = 'completed' "
            "ORDER BY created_at DESC LIMIT $2",
            symbol, limit,
        )
        for r in rows:
            points = await pool.fetch(
                "SELECT p.step_index, p.target_time, p.predicted_price, p.predicted_high, "
                "p.predicted_low, p.confidence, p.predicted_change_pct, p.actual_price, "
                "p.model_run_id AS run_id, r.agent_role, r.model_id "
                "FROM forecast_points p "
                "LEFT JOIN forecast_model_runs r ON r.id = p.model_run_id "
                "WHERE p.request_id = $1 ORDER BY p.step_index, p.model_run_id",
                r["id"],
            )
            pts = [dict(p) for p in points]
            step_samples = _holdout_samples(pts, r["base_price"])
            if step_samples:
                forecasts_evaluated += 1
            samples.extend(step_samples)
    except Exception as exc:  # noqa: BLE001 — surface as store outage, never fake numbers
        log.warning("v3 holdout query failed for %s/%s: %s", instrument_key, horizon, exc)
        raise HTTPException(
            status_code=503,
            detail={"reason_code": "forecast_query_failed", "message": str(exc)},
        ) from exc

    holdout = summarize_holdout(samples)
    return JSONResponse(
        {
            "schema_version": 1,
            "instrument_key": spec.instrument_key,
            "horizon": horizon,
            "period": period,
            "forecasts_evaluated": forecasts_evaluated,
            "as_of": now.isoformat(),
            "holdout": holdout,
        },
        headers={"Cache-Control": "no-store"},
    )

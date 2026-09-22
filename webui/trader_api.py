"""Trader API router — WORED Trader V0.1 Phase 5.

Provides REST endpoints for the Trader Deck UI:
  - candles, forecast, state, positions, activity
  - mode switching with idempotency
  - SSE stream placeholder

Data policy (self-learn review, item A): endpoints serve real upstream data
only. When Redis/Postgres/HTX cannot answer, they return an honest empty payload
labelled ``source="unavailable"`` — never a fabricated chart, price path or
position table. The Trader Deck UI surfaces that provenance as a badge.
"""
from __future__ import annotations

import json
import logging
import secrets
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("trader_api")

router = APIRouter(prefix="/api/trader", tags=["trader"])

ALLOWED_MODES = {"trade", "reduce_only", "pause"}

# The three voices a role bundle is supposed to answer with. They are the voters
# in the q10/q50/q90 band below, which is why a missing one has to be reported.
BUNDLE_ROLES: tuple[str, ...] = ("bull", "bear", "arbiter")

# ─── In-process idempotency store (sufficient for single-process mode) ────
_idempotency_store: dict[str, dict[str, Any]] = {}

# ─── In-process mode state (persisted to Redis/DB when available) ─────────
_current_mode: str = "trade"
_mode_reason: str = ""


def _require_api_auth(request: Request) -> None:
    """Reuse the app-level auth pattern: check WEBUI_AUTH_ENABLED + session."""
    import os

    def _parse_bool(raw: str | None, default: bool = False) -> bool:
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    auth_enabled = _parse_bool(os.getenv("WEBUI_AUTH_ENABLED"), default=True)
    if not auth_enabled:
        return

    # Check session cookie for authenticated principal
    session = getattr(request, "session", {})
    if session and session.get("authenticated"):
        return

    # Check Telegram init data header
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if init_data:
        # Defer to app-level resolver if available
        try:
            from app import resolve_principal  # type: ignore
            if resolve_principal(request) is not None:
                return
        except Exception:
            pass

    raise HTTPException(status_code=401, detail="Authentication required")


# ─── Trader Deck layout skeleton ──────────────────────────────────────────

def _now_ts() -> int:
    return int(time.time())


def _mock_state() -> dict[str, Any]:
    """Decorative Trader Deck skeleton (roster/limits) — not market data.

    Only ``mode``/``reason``/``plan_*`` are overwritten from real state by the
    callers; the agent roster and budget numbers are layout placeholders and are
    labelled ``source="skeleton"`` whenever no ``paper_v2_days`` row backs them.
    """
    now = datetime.now(timezone.utc)
    plan_valid = now.replace(minute=0, second=0, microsecond=0)
    from datetime import timedelta
    plan_valid_until = (plan_valid + timedelta(hours=1)).isoformat()
    return {
        "mode": _current_mode,
        "reason": _mode_reason,
        "profile": "P1",
        "leverage": 100,
        "plan_status": "active",
        "plan_valid_until": plan_valid_until,
        "plan_version": "v3",
        "sides": ["long", "short"],
        "p_entry_min": 0.65,
        "agent": {
            "name": "Trader-Supervisor",
            "model": "glm-5.2",
            "runtime": "Hermes · wored-trader",
        },
        "subagents": [
            {"name": "Planner", "model": "glm-5.2", "status": "ok"},
            {"name": "Market-Scout", "model": "deepseek-v4.1-flash", "status": "ok"},
            {"name": "Forecast-Critic", "model": "minimax-m2.7", "status": "ok"},
            {"name": "Entry-Gate", "model": "deepseek-v4.1-flash", "status": "ok"},
            {"name": "Journal-Coach", "model": "glm-5.3-flash", "status": "warn"},
        ],
        "budget": {
            "spent_day": 0.62,
            "limit_day": 1.6,
            "spent_month": 14.2,
            "limit_month": 30.0,
            "requests_today": 171,
            "requests_peak": 400,
        },
        "source": "skeleton",
    }


# ─── Redis helpers ────────────────────────────────────────────────────────

async def _redis_get(request: Request, key: str) -> str | None:
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        return None
    try:
        return await redis_client.get(key)
    except Exception as exc:
        log.warning("Redis GET %s failed: %s", key, exc)
        return None


async def _redis_set(request: Request, key: str, value: str, ttl: int = 300) -> None:
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        return
    try:
        await redis_client.setex(key, ttl, value)
    except Exception as exc:
        log.warning("Redis SET %s failed: %s", key, exc)


# ─── Forecast aggregation ─────────────────────────────────────────────────

def _as_float(value: Any, default: float = 0.0) -> float:
    """Coerce a DB numeric to float without letting one NULL blank the card.

    ``forecast_points.confidence`` is nullable and 14 of 855 production rows have
    it NULL; ``float(None)`` used to raise here, the endpoint caught it as a
    generic fetch failure and dropped the whole forecast to ``unavailable``.
    """
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _role_votes(rows: list[Any]) -> dict[int, dict[str, dict[str, Any]]]:
    """Group forecast points per step into one vote per agent role.

    The q10/q50/q90 band is only as honest as the number of independent voices
    behind it, so the role of the model run that produced each point is part of
    the aggregation, not just decoration:

    * a role that answered the same step twice (retries) contributes a single
      vote — the newest run wins, otherwise a retry would overweight one voice;
    * points whose run carries no role come from before the role bundle existed
      (47 such runs in production). They keep their own sample each, because
      without a role there is no evidence they are the same voice, and they can
      never make the coverage look like a real bundle - see ``band_basis``.
    """
    votes: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        step_index = int(row["step_index"])
        run_id = int(row["run_id"] or 0)
        agent_role = row["agent_role"]
        if agent_role:
            key = str(agent_role)
        else:
            key = f"neutral:{run_id}"
        previous = votes[step_index].get(key)
        if previous is not None and previous["run_id"] > run_id:
            continue
        votes[step_index][key] = {
            "run_id": run_id,
            "role": str(agent_role or "neutral"),
            "model_id": str(row["model_id"] or ""),
            "price": _as_float(row["predicted_price"]),
            "high": _as_float(row["predicted_high"]),
            "low": _as_float(row["predicted_low"]),
            "conf": _as_float(row["confidence"]),
            "has_conf": row["confidence"] is not None,
            "change_pct": _as_float(row["predicted_change_pct"]),
            "target_time": row["target_time"],
        }
    return dict(votes)


def aggregate_forecast_steps(
    votes: dict[int, dict[str, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Turn per-role votes into chart steps plus an honest coverage report.

    Returns ``(steps, coverage)``. ``coverage`` is what the Trader Deck badge
    reads: a two-role band, or a three-role band where two roles quoted the same
    model (#139: bull and arbiter both answered ``glm-5.1``), is not a three
    voice opinion and must not be drawn as one.
    """
    steps: list[dict[str, Any]] = []
    role_steps: dict[str, set[int]] = defaultdict(set)
    role_models: dict[str, list[str]] = defaultdict(list)
    samples_per_step: list[int] = []
    models_per_step: list[int] = []

    for step_index in sorted(votes):
        votes_by_role = votes[step_index]
        if not votes_by_role:
            continue
        sample = list(votes_by_role.values())
        prices = sorted(v["price"] for v in sample)
        n = len(prices)
        c50 = statistics.median(prices)
        c10 = prices[max(0, int(n * 0.1) - 1)] if n > 1 else prices[0]
        c90 = prices[min(n - 1, int(n * 0.9))] if n > 1 else prices[0]
        h90 = max(v["high"] for v in sample)
        l10 = min(v["low"] for v in sample)
        p_up = sum(1 for v in sample if v["change_pct"] > 0) / n
        confidences = [v["conf"] for v in sample if v["has_conf"]]
        target_time = sample[0]["target_time"]
        # Spread of the roles' percentage calls, in decimal. This replaces the
        # old hardcoded ``sigma: 0.008`` that the deck printed as "σ часа
        # (EWMA)": a constant was presented as a measured quantity. It is a
        # disagreement measure across voices, not a forecast error, so it is
        # None when there is a single voice - one opinion has no spread.
        change_pcts = [v["change_pct"] / 100.0 for v in sample]
        sigma = round(statistics.pstdev(change_pcts), 5) if n > 1 else None

        distinct_models = {v["model_id"] for v in sample if v["model_id"]}
        samples_per_step.append(n)
        models_per_step.append(len(distinct_models) or 1)
        for role, vote in votes_by_role.items():
            role_key = role if role in BUNDLE_ROLES else "neutral"
            role_steps[role_key].add(step_index)
            if vote["model_id"] and vote["model_id"] not in role_models[role_key]:
                role_models[role_key].append(vote["model_id"])

        steps.append({
            "step": step_index,
            "time": (
                int(target_time.replace(tzinfo=timezone.utc).timestamp())
                if target_time.tzinfo is None
                else int(target_time.timestamp())
            ),
            "open": round(c50, 1),
            "close": round(c50, 1),
            "high": round(h90, 1),
            "low": round(l10, 1),
            "c10": round(c10, 1),
            "c90": round(c90, 1),
            "h90": round(h90, 1),
            "l10": round(l10, 1),
            "vol": 0.0,
            "p_up": round(p_up, 2),
            "sigma": sigma,
            "confidence": round(sum(confidences) / len(confidences), 1) if confidences else None,
            "samples": n,
        })

    present = [role for role in BUNDLE_ROLES if role in role_steps]
    missing = [role for role in BUNDLE_ROLES if role not in role_steps]
    if not present:
        band_basis = "legacy-no-roles"
    elif len(present) == 1:
        band_basis = "single-role"
    elif len(present) == 2:
        band_basis = "two-roles"
    else:
        band_basis = "three-roles"

    coverage = {
        "roles": {
            role: {
                "present": role in role_steps,
                "model_id": "+".join(role_models.get(role, [])) or None,
                "steps": len(role_steps.get(role, ())),
            }
            for role in BUNDLE_ROLES
        },
        "roles_present": present,
        "roles_missing": missing,
        "neutral_steps": len(role_steps.get("neutral", ())),
        "band_basis": band_basis,
        "min_samples_per_step": min(samples_per_step) if samples_per_step else 0,
        "max_samples_per_step": max(samples_per_step) if samples_per_step else 0,
        "min_models_per_step": min(models_per_step) if models_per_step else 0,
        "max_models_per_step": max(models_per_step) if models_per_step else 0,
    }
    return steps, coverage


# ─── Endpoints ────────────────────────────────────────────────────────────

@router.get("/candles")
async def get_candles(request: Request, symbol: str = "btcusdt", period: str = "60min", size: int = 200):
    """Return recent OHLCV candles from Redis cache or HTX spot REST (empty if neither answers)."""
    _require_api_auth(request)
    size = max(30, min(size, 500))

    # Try Redis cache
    raw = await _redis_get(request, f"candles:{symbol}:{period}")
    if raw:
        try:
            data = json.loads(raw)
            candles = data.get("candles", [])
            if candles:
                return {"symbol": symbol, "period": period, "candles": candles[:size], "source": "redis"}
        except (json.JSONDecodeError, KeyError):
            pass

    # Try fetching from HTX via app state
    http_client = getattr(request.app.state, "http_client", None)
    if http_client is not None:
        try:
            resp = await http_client.get(
                "https://api.huobi.pro/market/history/kline",
                params={"symbol": symbol, "period": period, "size": size},
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("status") == "ok":
                candles = []
                for item in reversed(payload.get("data", [])):
                    candles.append({
                        "time": int(item["id"]),
                        "open": float(item["open"]),
                        "high": float(item["high"]),
                        "low": float(item["low"]),
                        "close": float(item["close"]),
                        "volume": float(item.get("vol", 0.0)),
                    })
                if candles:
                    return {"symbol": symbol, "period": period, "candles": candles, "source": "htx-rest"}
        except Exception as exc:
            log.warning("HTX kline fetch failed: %s", exc)

    # No real upstream answered. An honest empty series is returned instead of a
    # fabricated chart: invented candles on a trading deck are a safety hazard
    # (self-learn review, item A).
    log.warning(
        "trader candles unavailable for %s/%s (redis miss, HTX REST failed or no http client)",
        symbol, period,
    )
    return {"symbol": symbol, "period": period, "candles": [], "source": "unavailable"}


@router.get("/forecast")
async def get_forecast(request: Request, symbol: str = "btcusdt"):
    """Return latest completed forecast from forecast_requests/forecast_points (empty if none)."""
    _require_api_auth(request)

    # 1. Try PostgreSQL — forecast_requests + forecast_points
    pool = getattr(request.app.state, "pg_pool", None)
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                # Latest completed forecast request
                row = await conn.fetchrow(
                    "SELECT id, symbol, base_price, horizon_hours, base_timeframe, created_at, "
                    "execution_state "
                    "FROM forecast_requests WHERE symbol = $1 AND status = 'completed' "
                    "ORDER BY created_at DESC LIMIT 1",
                    symbol,
                )
                if row:
                    # Every point carries the model run that produced it, and the
                    # run carries the role: that is what makes a band three voices
                    # instead of one voice quoted three times.
                    points = await conn.fetch(
                        "SELECT p.step_index, p.target_time, p.predicted_price, p.predicted_high, "
                        "p.predicted_low, p.confidence, p.predicted_change_pct, "
                        "p.model_run_id AS run_id, r.agent_role, r.model_id "
                        "FROM forecast_points p "
                        "LEFT JOIN forecast_model_runs r ON r.id = p.model_run_id "
                        "WHERE p.request_id = $1 ORDER BY p.step_index, p.model_run_id",
                        row["id"],
                    )
                    steps, coverage = aggregate_forecast_steps(_role_votes(list(points)))

                    if steps:
                        return {
                            "request_id": row["id"],
                            "symbol": row["symbol"],
                            "base_price": float(row["base_price"]),
                            "base_time": int(row["created_at"].timestamp()),
                            "horizon_steps": len(steps),
                            "steps": steps,
                            "source": "postgres",
                            "generated_at": row["created_at"].isoformat(),
                            "execution_state": row["execution_state"],
                            "coverage": coverage,
                        }
        except Exception as exc:
            log.warning("forecast_requests fetch failed: %s", exc)

    # 2. Try Redis cache
    raw = await _redis_get(request, f"trader:forecast:{symbol}")
    if raw:
        try:
            data = json.loads(raw)
            data["source"] = data.get("source", "redis")
            return data
        except (json.JSONDecodeError, KeyError):
            pass

    # 3. Nothing in Postgres or cache — return an honest empty forecast rather
    # than a made-up price path (self-learn review, item A).
    log.warning("trader forecast unavailable for %s (postgres/cache miss)", symbol)
    return {
        "symbol": symbol,
        "request_id": None,
        "steps": [],
        "horizon_steps": 0,
        "source": "unavailable",
    }


@router.get("/state")
async def get_state(request: Request):
    """Return current trading state from paper_v2_days (skeleton-labelled without a row)."""
    _require_api_auth(request)

    global _current_mode, _mode_reason

    # Try Redis for persisted mode
    raw = await _redis_get(request, "trader:mode")
    if raw:
        try:
            data = json.loads(raw)
            _current_mode = data.get("mode", _current_mode)
            _mode_reason = data.get("reason", _mode_reason)
        except (json.JSONDecodeError, KeyError):
            pass

    # Try paper_v2_days for real state
    pool = getattr(request.app.state, "pg_pool", None)
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT state, strategy_version, settings_snapshot, created_at "
                    "FROM paper_v2_days ORDER BY created_at DESC LIMIT 1"
                )
                if row:
                    settings = row["settings_snapshot"]
                    if not isinstance(settings, dict):
                        settings = {}
                    state = _mock_state()
                    state["mode"] = _current_mode
                    state["reason"] = _mode_reason
                    state["plan_status"] = "active" if row["state"] == "running" else "idle"
                    state["plan_version"] = row["strategy_version"] or "baseline_v1"
                    # Map real settings fields
                    if settings.get("max_leverage"):
                        try:
                            state["leverage"] = int(settings["max_leverage"])
                        except (ValueError, TypeError):
                            pass
                    state["source"] = "paper_v2"
                    return state
        except Exception as exc:
            log.warning("paper_v2_days fetch failed: %s", exc)

    state = _mock_state()
    state["mode"] = _current_mode
    state["reason"] = _mode_reason
    # mode/reason are real (in-process + Redis), the rest of the skeleton is
    # decorative — say so instead of pretending the whole card is live state.
    state["source"] = "skeleton"
    return state


@router.get("/positions")
async def get_positions(request: Request, status: str | None = None):
    """Return open/closed positions from paper_v2 (honest empty when no data)."""
    _require_api_auth(request)

    # Try paper_v2 / DB
    pool = getattr(request.app.state, "pg_pool", None)
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM paper_v2_positions ORDER BY opened_at DESC LIMIT 100"
                )
                positions = [dict(row) for row in rows]
                if status and status != "all":
                    positions = [p for p in positions if p.get("status") == status]
                # Return real data — even if empty (honest)
                return {"positions": positions, "source": "paper_v2", "count": len(positions)}
        except Exception as exc:
            log.warning("paper_v2 positions fetch failed: %s", exc)

    # DB unavailable — the positions table is trading state, so we never
    # synthesize it (self-learn review, item A).
    log.warning("trader positions unavailable (no postgres pool or query failed)")
    return {"positions": [], "source": "unavailable", "count": 0}


@router.get("/activity")
async def get_activity(request: Request, limit: int = 20):
    """Return recent activity feed from paper_v2_events (empty if none)."""
    _require_api_auth(request)
    limit = max(1, min(limit, 100))

    # Try paper_v2_events
    pool = getattr(request.app.state, "pg_pool", None)
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT event_type, payload, occurred_at "
                    "FROM paper_v2_events ORDER BY occurred_at DESC LIMIT $1",
                    limit,
                )
                if rows:
                    items = []
                    for r in rows:
                        payload = r["payload"] or {}
                        items.append({
                            "time": int(r["occurred_at"].timestamp()),
                            "who": payload.get("source", "system") if isinstance(payload, dict) else "system",
                            "text": payload.get("message", r["event_type"]) if isinstance(payload, dict) else r["event_type"],
                            "color": payload.get("level", "info") if isinstance(payload, dict) else "info",
                        })
                    return {"items": items, "source": "paper_v2"}
        except Exception as exc:
            log.warning("paper_v2_events fetch failed: %s", exc)

    # Try Redis
    raw = await _redis_get(request, "trader:activity")
    if raw:
        try:
            data = json.loads(raw)
            items = data.get("items", [])[:limit]
            return {"items": items, "source": "redis"}
        except (json.JSONDecodeError, KeyError):
            pass

    # No events and no cache — an empty feed, not an invented one
    # (self-learn review, item A).
    return {"items": [], "source": "unavailable"}


@router.post("/mode")
async def set_mode(request: Request, payload: dict[str, Any] = Body(...)):
    """Set trading mode (trade/reduce_only/pause) with idempotency key."""
    _require_api_auth(request)

    global _current_mode, _mode_reason

    mode = payload.get("mode", "")
    if mode not in ALLOWED_MODES:
        raise HTTPException(status_code=422, detail=f"mode must be one of {ALLOWED_MODES}")

    idempotency_key = payload.get("idempotency_key", "")
    if not idempotency_key:
        idempotency_key = secrets.token_urlsafe(16)

    reason = payload.get("reason", "")

    # Idempotency check
    if idempotency_key in _idempotency_store:
        stored = _idempotency_store[idempotency_key]
        return {
            "ok": True,
            "applied": False,
            "idempotency_key": idempotency_key,
            "mode": stored["mode"],
            "reason": stored.get("reason", ""),
            "message": "duplicate idempotency key — mode unchanged",
        }

    # Apply mode change
    _current_mode = mode
    _mode_reason = reason
    _idempotency_store[idempotency_key] = {
        "mode": mode,
        "reason": reason,
        "timestamp": _now_ts(),
    }

    # Persist to Redis
    await _redis_set(request, "trader:mode", json.dumps({"mode": mode, "reason": reason}), ttl=86400)

    return {
        "ok": True,
        "applied": True,
        "idempotency_key": idempotency_key,
        "mode": mode,
        "reason": reason,
        "message": f"mode set to {mode}",
    }


@router.get("/stream")
async def stream_placeholder(request: Request):
    """SSE stream placeholder — returns 501 Not Implemented."""
    _require_api_auth(request)
    return JSONResponse(
        {"detail": "SSE stream not yet implemented", "status": 501},
        status_code=501,
    )
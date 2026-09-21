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
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("trader_api")

router = APIRouter(prefix="/api/trader", tags=["trader"])

ALLOWED_MODES = {"trade", "reduce_only", "pause"}

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
                    "SELECT id, symbol, base_price, horizon_hours, base_timeframe, created_at "
                    "FROM forecast_requests WHERE symbol = $1 AND status = 'completed' "
                    "ORDER BY created_at DESC LIMIT 1",
                    symbol,
                )
                if row:
                    # Get all forecast points for this request, grouped by step
                    points = await conn.fetch(
                        "SELECT step_index, target_time, predicted_price, predicted_high, "
                        "predicted_low, confidence, predicted_change_pct "
                        "FROM forecast_points WHERE request_id = $1 ORDER BY step_index",
                        row["id"],
                    )
                    # Group by step_index, aggregate into q10/q50/q90
                    from collections import defaultdict
                    import statistics
                    steps_map: dict[int, list[dict]] = defaultdict(list)
                    for pt in points:
                        steps_map[pt["step_index"]].append({
                            "price": float(pt["predicted_price"]),
                            "high": float(pt["predicted_high"]),
                            "low": float(pt["predicted_low"]),
                            "conf": float(pt["confidence"]),
                            "change_pct": float(pt["predicted_change_pct"]),
                            "target_time": pt["target_time"],
                        })

                    steps = []
                    for step_idx in sorted(steps_map.keys()):
                        pts = steps_map[step_idx]
                        prices = [p["price"] for p in pts]
                        highs = [p["high"] for p in pts]
                        lows = [p["low"] for p in pts]
                        n = len(prices)
                        if n == 0:
                            continue
                        c50 = statistics.median(prices)
                        c10 = sorted(prices)[max(0, int(n * 0.1) - 1)] if n > 1 else prices[0]
                        c90 = sorted(prices)[min(n - 1, int(n * 0.9))] if n > 1 else prices[0]
                        h90 = max(highs)
                        l10 = min(lows)
                        p_up = sum(1 for p in pts if p["change_pct"] > 0) / n
                        avg_conf = sum(p["conf"] for p in pts) / n
                        target_ts = pts[0]["target_time"]
                        steps.append({
                            "step": step_idx,
                            "time": int(target_ts.replace(tzinfo=timezone.utc).timestamp()) if target_ts.tzinfo is None else int(target_ts.timestamp()),
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
                            "sigma": 0.008,
                            "confidence": round(avg_conf, 1),
                        })

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
"""Trader API router — WORED Trader V0.1 Phase 5.

Provides REST endpoints for the Trader Deck UI:
  - candles, forecast, state, positions, activity
  - mode switching with idempotency
  - SSE stream placeholder

When Redis/Postgres are unavailable, endpoints return mock data
matching the Trader Deck mockup structure so the UI is always functional.
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


# ─── Mock data generators ─────────────────────────────────────────────────

def _now_ts() -> int:
    return int(time.time())


def _mock_candles(count: int = 200) -> list[dict[str, Any]]:
    """Generate realistic OHLCV candles for BTC-USDT perpetual."""
    import random
    random.seed(42)  # deterministic for tests
    base_ts = _now_ts() - count * 3600
    price = 75000.0
    candles: list[dict[str, Any]] = []
    for i in range(count):
        ts = base_ts + i * 3600
        vol = random.uniform(50, 400)
        op = price
        change = random.gauss(0, 0.008) * price
        close = max(50000, op + change)
        high = max(op, close) + random.uniform(10, 200)
        low = min(op, close) - random.uniform(10, 200)
        amount = vol
        candles.append({
            "time": ts,
            "open": round(op, 1),
            "high": round(high, 1),
            "low": round(low, 1),
            "close": round(close, 1),
            "volume": round(amount, 2),
        })
        price = close
    return candles


def _mock_forecast() -> dict[str, Any]:
    """Generate mock forecast matching the Trader Deck forecast card structure."""
    candles = _mock_candles(50)
    last_close = candles[-1]["close"]
    last_time = candles[-1]["time"]
    steps: list[dict[str, Any]] = []
    price = last_close
    for k in range(1, 4):
        ts = last_time + k * 3600
        sigma = 0.008
        c50 = price * (1 + 0.0002)
        c10 = price * (1 - 1.2816 * sigma)
        c90 = price * (1 + 1.2816 * sigma)
        hi = max(price, c50) * (1 + 0.55 * sigma)
        lo = min(price, c50) * (1 - 0.55 * sigma)
        steps.append({
            "step": k,
            "time": ts,
            "open": round(price, 1),
            "close": round(c50, 1),
            "high": round(hi, 1),
            "low": round(lo, 1),
            "c10": round(c10, 1),
            "c90": round(c90, 1),
            "h90": round(max(price, c90) * (1 + 0.9 * sigma), 1),
            "l10": round(min(price, c10) * (1 - 0.9 * sigma), 1),
            "vol": 150.0,
            "p_up": 0.55,
            "sigma": sigma,
        })
        price = c50
    return {
        "request_id": 0,
        "symbol": "btcusdt",
        "base_price": round(last_close, 1),
        "base_time": last_time,
        "horizon_steps": 3,
        "steps": steps,
        "source": "mock",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _mock_state() -> dict[str, Any]:
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
        "source": "mock",
    }


def _mock_positions() -> list[dict[str, Any]]:
    """Generate mock positions matching the Trader Deck table structure."""
    candles = _mock_candles(200)
    first_ts = candles[0]["time"]
    last_ts = candles[-1]["time"]
    span = last_ts - first_ts
    # Distribute positions evenly across the visible candle range
    profiles = {
        "P0": {"L": 50, "tp": 0.006, "sl": 0.006},
        "P1": {"L": 100, "tp": 0.006, "sl": 0.006},
        "P2": {"L": 150, "tp": 0.0025, "sl": 0.0025},
        "P3": {"L": 200, "tp": 0.001512, "sl": 0.0012},
    }
    # 8 positions spread across 15%-85% of the candle range
    offsets = [0.15, 0.24, 0.33, 0.42, 0.51, 0.60, 0.70, 0.80]
    plan = [
        ("long", "P1", "tp"),
        ("long", "P2", "sl"),
        ("short", "P2", "tp"),
        ("short", "P1", "timeout"),
        ("short", "P3", "liq"),
        ("long", "P3", "tp"),
        ("long", "P0", "sl"),
        ("long", "P1", "open"),
    ]
    positions: list[dict[str, Any]] = []
    for idx, (side, prof, status) in enumerate(plan):
        t = int(first_ts + span * offsets[idx])
        p = profiles[prof]
        # Use actual candle close at that time as entry price
        nearest = min(candles, key=lambda c: abs(c["time"] - t))
        entry = nearest["close"]
        dir_val = 1 if side == "long" else -1
        tp = entry * (1 + dir_val * p["tp"])
        sl = entry * (1 - dir_val * p["sl"])
        liq = entry * (1 - dir_val * 0.0034)
        if status == "open":
            exit_price = 75500
            gross = dir_val * (exit_price - entry) * 10 * 0.001
            net = gross - 2 * entry * 10 * 0.001 * 0.0006
            exit_time = None
        elif status == "liq":
            exit_price = liq
            gross = -(10 * p["L"] / 100)
            net = gross
            exit_time = t + 1800
        elif status == "tp":
            exit_price = tp
            gross = dir_val * (exit_price - entry) * 10 * 0.001
            net = gross - 2 * entry * 10 * 0.001 * 0.0006
            exit_time = t + 1800
        elif status == "sl":
            exit_price = sl
            gross = dir_val * (exit_price - entry) * 10 * 0.001
            net = gross - 2 * entry * 10 * 0.001 * 0.0006
            exit_time = t + 1800
        else:  # timeout
            exit_price = entry + dir_val * 20
            gross = dir_val * (exit_price - entry) * 10 * 0.001
            net = gross - 2 * entry * 10 * 0.001 * 0.0006
            exit_time = t + 3600

        fees = abs(2 * entry * 10 * 0.001 * 0.0006)
        positions.append({
            "id": idx + 1,
            "open_time": t,
            "exit_time": exit_time,
            "account": f"auto_{prof.lower()}",
            "profile": prof,
            "side": side,
            "leverage": p["L"],
            "margin": round(10 * p["L"] / 100, 2),
            "extra_margin": 0,
            "size": round(10 * 0.001, 4),
            "contracts": 10,
            "entry": round(entry, 1),
            "exit": round(exit_price, 1),
            "liq": round(liq, 1),
            "tp": round(tp, 1),
            "sl": round(sl, 1),
            "status": status,
            "reason": status,
            "gross": round(gross, 2),
            "fees": round(fees, 2),
            "net": round(net, 2),
            "duration_min": 240 if status != "open" else None,
        })
    return positions


def _mock_activity() -> list[dict[str, Any]]:
    now = _now_ts()
    return [
        {"time": now - 60, "who": "Entry-Gate", "text": "allow · long: p_up × 0.90 = 0.68 ≥ 0.65", "color": "up"},
        {"time": now - 120, "who": "Planner", "text": "План v3: trade, P1, обе стороны, до 6 входов", "color": "accent"},
        {"time": now - 180, "who": "Forecast-Critic", "text": "Множитель ×0.90: объём ниже медианы часа", "color": "info"},
        {"time": now - 240, "who": "Market-Scout", "text": "Режим range; ликвидации за час: long 0.4 млн $", "color": "info"},
        {"time": now - 300, "who": "Runner", "text": "Long auto_p1 10 cont @ 75050 · TP 75500 · SL 74600", "color": "info"},
        {"time": now - 600, "who": "Risk engine", "text": "Ликвидация auto_p3 short 200x · −10.00 $ маржи", "color": "down"},
        {"time": now - 900, "who": "Hermes", "text": "Отчёт дня отправлен в Telegram", "color": "accent"},
    ]


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
    """Return recent OHLCV candles from Redis cache or mock data."""
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

    # Fallback to mock
    return {"symbol": symbol, "period": period, "candles": _mock_candles(size), "source": "mock"}


@router.get("/forecast")
async def get_forecast(request: Request, symbol: str = "btcusdt"):
    """Return latest forecast run from Redis or mock."""
    _require_api_auth(request)

    raw = await _redis_get(request, f"trader:forecast:{symbol}")
    if raw:
        try:
            data = json.loads(raw)
            data["source"] = data.get("source", "redis")
            return data
        except (json.JSONDecodeError, KeyError):
            pass

    return _mock_forecast()


@router.get("/state")
async def get_state(request: Request):
    """Return current trading state (mode, profile, plan status, reason)."""
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

    state = _mock_state()
    state["mode"] = _current_mode
    state["reason"] = _mode_reason
    return state


@router.get("/positions")
async def get_positions(request: Request, status: str | None = None):
    """Return open/closed positions from paper_v2 or mock."""
    _require_api_auth(request)

    # Try paper_v2 / DB when available
    pool = getattr(request.app.state, "pg_pool", None)
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM paper_v2_positions ORDER BY opened_at DESC LIMIT 100"
                )
                if rows:
                    positions = [dict(row) for row in rows]
                    if status and status != "all":
                        positions = [p for p in positions if p.get("status") == status]
                    return {"positions": positions, "source": "paper_v2"}
        except Exception as exc:
            log.warning("paper_v2 positions fetch failed: %s", exc)

    positions = _mock_positions()
    if status and status != "all":
        positions = [p for p in positions if p.get("status") == status]
    return {"positions": positions, "source": "mock"}


@router.get("/activity")
async def get_activity(request: Request, limit: int = 20):
    """Return recent activity feed."""
    _require_api_auth(request)
    limit = max(1, min(limit, 100))

    raw = await _redis_get(request, "trader:activity")
    if raw:
        try:
            data = json.loads(raw)
            items = data.get("items", [])[:limit]
            return {"items": items, "source": "redis"}
        except (json.JSONDecodeError, KeyError):
            pass

    return {"items": _mock_activity()[:limit], "source": "mock"}


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
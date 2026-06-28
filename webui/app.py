from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import asyncpg
import httpx
import redis.asyncio as redis
from fastapi import BackgroundTasks, Body, FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from prediction_engine import MODEL_CONFIGS, generate_model_prediction, generate_prediction_bundle, list_prediction_models

log = logging.getLogger("webui")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

HTX_REST_URL = os.getenv("HTX_REST_URL", "https://api.huobi.pro")
DEFAULT_WATCHLIST = "btcusdt,ethusdt"
DEFAULT_PERIOD = "60min"
DEFAULT_SIZE = 240
MAX_KLINE_SIZE = 500
ALLOWED_PERIODS = {"1min", "5min", "15min", "30min", "60min", "4hour", "1day"}
ALLOWED_PREDICTION_HORIZONS = {1, 2, 3, 4, 8, 16, 24}
DEFAULT_PAGE_SIZE = 25
SYNC_PREDICTION_MODEL_KEYS = ("analyst", "premium")
CHART_NOTICE = "TradingView Lightweight Charts. Copyright (c) 2025 TradingView, Inc."
PREDICTION_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS forecast_requests (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(15) NOT NULL,
    horizon_hours INT NOT NULL,
    base_price DECIMAL(20, 8) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    source VARCHAR(20) NOT NULL DEFAULT 'webui',
    requested_by VARCHAR(64),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_forecast_requests_created_at ON forecast_requests (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_forecast_requests_symbol_status ON forecast_requests (symbol, status);

CREATE TABLE IF NOT EXISTS forecast_model_runs (
    id SERIAL PRIMARY KEY,
    request_id INT NOT NULL REFERENCES forecast_requests(id) ON DELETE CASCADE,
    model_key VARCHAR(32) NOT NULL,
    model_name VARCHAR(128) NOT NULL,
    model_id VARCHAR(128) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'completed',
    summary TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_forecast_model_runs_request_id ON forecast_model_runs (request_id);

CREATE TABLE IF NOT EXISTS forecast_points (
    id SERIAL PRIMARY KEY,
    request_id INT NOT NULL REFERENCES forecast_requests(id) ON DELETE CASCADE,
    model_run_id INT NOT NULL REFERENCES forecast_model_runs(id) ON DELETE CASCADE,
    forecast_hour INT NOT NULL,
    target_time TIMESTAMP NOT NULL,
    predicted_price DECIMAL(20, 8) NOT NULL,
    predicted_change_pct DECIMAL(10, 4) NOT NULL,
    confidence DECIMAL(5, 2),
    rationale TEXT,
    actual_price DECIMAL(20, 8),
    actual_change_pct DECIMAL(10, 4),
    price_error_pct DECIMAL(10, 4),
    change_error_pct DECIMAL(10, 4),
    accuracy_score DECIMAL(6, 2),
    failure_score DECIMAL(6, 2),
    direction_match BOOLEAN,
    verdict TEXT,
    evaluated_at TIMESTAMP,
    UNIQUE (model_run_id, forecast_hour)
);

CREATE INDEX IF NOT EXISTS idx_forecast_points_target_time ON forecast_points (target_time, evaluated_at);
CREATE INDEX IF NOT EXISTS idx_forecast_points_request_id ON forecast_points (request_id, forecast_hour);
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS failure_score DECIMAL(6, 2);
"""


def get_watchlist() -> list[str]:
    return [item.strip().lower() for item in os.getenv("WATCHLIST", DEFAULT_WATCHLIST).split(",") if item.strip()]


def parse_bool(raw_value: str | None, default: bool = False) -> bool:
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def normalize_db_url(raw_url: str | None) -> str | None:
    if raw_url and raw_url.startswith("postgresql+asyncpg://"):
        return raw_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return raw_url


def safe_json(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def serialize_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def format_ui_timestamp(value: datetime | str | None) -> str | None:
    if value is None:
        return None

    parsed = value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def to_db_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().lower()
    if not normalized:
        raise HTTPException(status_code=400, detail="symbol is required")
    return normalized


def normalize_prediction_horizon(horizon: int) -> int:
    normalized = int(horizon)
    if normalized not in ALLOWED_PREDICTION_HORIZONS:
        raise HTTPException(status_code=400, detail=f"Unsupported prediction horizon '{horizon}'")
    return normalized


def ensure_prediction_symbol(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    if normalized not in get_watchlist():
        raise HTTPException(status_code=400, detail=f"Symbol '{symbol}' is outside the configured watchlist")
    return normalized


def normalize_period(period: str) -> str:
    normalized = period.strip().lower()
    if normalized not in ALLOWED_PERIODS:
        raise HTTPException(status_code=400, detail=f"Unsupported period '{period}'")
    return normalized


def clamp_size(size: int) -> int:
    return max(30, min(size, MAX_KLINE_SIZE))


def get_auth_enabled() -> bool:
    password = os.getenv("WEBUI_ADMIN_PASSWORD", "")
    flag = os.getenv("WEBUI_AUTH_ENABLED")
    if flag is None:
        return bool(password)
    return parse_bool(flag, default=False)


def get_admin_username() -> str:
    return os.getenv("WEBUI_ADMIN_USERNAME", "admin")


def get_admin_password() -> str:
    return os.getenv("WEBUI_ADMIN_PASSWORD", "")


def get_session_secret() -> str:
    raw_secret = os.getenv("WEBUI_SESSION_SECRET")
    if raw_secret:
        return raw_secret
    # Fallback to a random secret if not configured
    return secrets.token_urlsafe(32)


def get_internal_api_token() -> str:
    explicit = os.getenv("WEBUI_INTERNAL_TOKEN", "").strip()
    if explicit:
        return explicit
    # Fallback to a random token if not configured
    return secrets.token_urlsafe(32)


def verify_internal_api_token(token: str | None) -> None:
    expected = get_internal_api_token()
    if not token or not hmac.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="Invalid internal API token")


def validate_auth_config() -> None:
    if get_auth_enabled() and not get_admin_password():
        raise RuntimeError("WEBUI_AUTH_ENABLED=true requires WEBUI_ADMIN_PASSWORD to be set")


def is_authenticated(request: Request) -> bool:
    if not get_auth_enabled():
        return True
    return bool(request.session.get("authenticated")) and request.session.get("username") == get_admin_username()


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf_token"] = token
    return token


def verify_csrf_token(request: Request, token: str | None) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not token or not hmac.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def set_flash(request: Request, level: str, message: str) -> None:
    request.session["flash"] = {"level": level, "message": message}


def pop_flash(request: Request) -> dict[str, str] | None:
    flash = request.session.get("flash")
    if flash:
        request.session.pop("flash", None)
    return flash


def build_login_redirect(request: Request) -> RedirectResponse | None:
    if is_authenticated(request):
        return None
    next_target = request.url.path
    if request.url.query:
        next_target = f"{next_target}?{request.url.query}"
    encoded_target = quote(next_target, safe="/?=&")
    return RedirectResponse(url=f"/login?next={encoded_target}", status_code=303)


def require_page_auth(request: Request) -> RedirectResponse | None:
    if not get_auth_enabled():
        return None
    return build_login_redirect(request)


def require_api_auth(request: Request) -> None:
    if get_auth_enabled() and not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")


def build_template_context(request: Request, **extra: Any) -> dict[str, Any]:
    watchlist = get_watchlist()
    context = {
        "request": request,
        "watchlist": watchlist,
        "default_symbol": watchlist[0] if watchlist else "btcusdt",
        "chart_notice": CHART_NOTICE,
        "auth_enabled": get_auth_enabled(),
        "authenticated": is_authenticated(request),
        "admin_username": get_admin_username(),
        "csrf_token": ensure_csrf_token(request),
        "flash": pop_flash(request),
        "current_path": request.url.path,
        "can_admin": is_authenticated(request),
    }
    context.update(extra)
    return context


def template_response(request: Request, name: str, **extra: Any) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request,
        name=name,
        context=build_template_context(request, **extra),
    )


def json_pretty(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)


def normalize_klines(raw_klines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    for item in reversed(raw_klines):
        candles.append(
            {
                "time": int(item["id"]),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": float(item.get("vol", 0.0)),
            }
        )
    return candles


def build_volume_series(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    for candle in candles:
        bars.append(
            {
                "time": candle["time"],
                "value": candle["volume"],
                "color": "#1fd6a3" if candle["close"] >= candle["open"] else "#ff5d73",
            }
        )
    return bars


def compute_sma_series(candles: list[dict[str, Any]], window: int) -> list[dict[str, Any]]:
    rolling = deque()
    total = 0.0
    points: list[dict[str, Any]] = []
    for candle in candles:
        close = candle["close"]
        rolling.append(close)
        total += close
        if len(rolling) > window:
            total -= rolling.popleft()
        if len(rolling) == window:
            points.append({"time": candle["time"], "value": round(total / window, 6)})
    return points


def compute_rsi_series(candles: list[dict[str, Any]], period: int = 14) -> list[dict[str, Any]]:
    if len(candles) <= period:
        return []

    closes = [candle["close"] for candle in candles]
    gains: list[float] = []
    losses: list[float] = []
    for idx in range(1, len(closes)):
        delta = closes[idx] - closes[idx - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    points: list[dict[str, Any]] = []

    def as_rsi(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 100.0
        relative_strength = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + relative_strength))

    points.append({"time": candles[period]["time"], "value": round(as_rsi(average_gain, average_loss), 4)})

    for idx in range(period, len(gains)):
        average_gain = ((average_gain * (period - 1)) + gains[idx]) / period
        average_loss = ((average_loss * (period - 1)) + losses[idx]) / period
        points.append({"time": candles[idx + 1]["time"], "value": round(as_rsi(average_gain, average_loss), 4)})

    return points


def compute_ema(values: list[float], span: int) -> list[float]:
    multiplier = 2.0 / (span + 1)
    output: list[float] = []
    ema_value: float | None = None
    for value in values:
        ema_value = value if ema_value is None else (value - ema_value) * multiplier + ema_value
        output.append(ema_value)
    return output


def compute_macd_payload(candles: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    if not candles:
        return {"macd": [], "signal": [], "histogram": []}

    closes = [candle["close"] for candle in candles]
    ema_fast = compute_ema(closes, 12)
    ema_slow = compute_ema(closes, 26)
    macd_values = [fast - slow for fast, slow in zip(ema_fast, ema_slow)]
    signal_values = compute_ema(macd_values, 9)

    macd_points: list[dict[str, Any]] = []
    signal_points: list[dict[str, Any]] = []
    histogram_points: list[dict[str, Any]] = []

    for candle, macd_value, signal_value in zip(candles, macd_values, signal_values):
        histogram_value = macd_value - signal_value
        macd_points.append({"time": candle["time"], "value": round(macd_value, 6)})
        signal_points.append({"time": candle["time"], "value": round(signal_value, 6)})
        histogram_points.append(
            {
                "time": candle["time"],
                "value": round(histogram_value, 6),
                "color": "#1fd6a3" if histogram_value >= 0 else "#ff5d73",
            }
        )

    return {"macd": macd_points, "signal": signal_points, "histogram": histogram_points}


def compute_return_pct(candles: list[dict[str, Any]], hours: int) -> float | None:
    if len(candles) <= hours:
        return None
    base_close = candles[-(hours + 1)]["close"]
    latest_close = candles[-1]["close"]
    if not base_close:
        return None
    return round(((latest_close - base_close) / base_close) * 100.0, 4)


def compact_candle_context(candles: list[dict[str, Any]], limit: int = 36) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for candle in candles[-limit:]:
        compact.append(
            {
                "time": datetime.fromtimestamp(candle["time"], tz=timezone.utc).isoformat(),
                "open": round(candle["open"], 6),
                "high": round(candle["high"], 6),
                "low": round(candle["low"], 6),
                "close": round(candle["close"], 6),
                "volume": round(candle["volume"], 3),
            }
        )
    return compact


async def ensure_prediction_schema(pool: asyncpg.Pool | None) -> None:
    if pool is None:
        return
    async with pool.acquire() as connection:
        for statement in [item.strip() for item in PREDICTION_TABLES_SQL.split(";") if item.strip()]:
            await connection.execute(statement)


async def get_symbol_snapshot(request: Request, symbol: str) -> dict[str, Any]:
    normalized_symbol = normalize_symbol(symbol)
    redis_client = request.app.state.redis_client
    if redis_client is not None:
        raw = await redis_client.get(f"ticker:{normalized_symbol}")
        if raw:
            snapshot = safe_json(raw)
            price = float(snapshot.get("price", 0.0) or 0.0)
            if price > 0:
                return {
                    "symbol": normalized_symbol,
                    "price": price,
                    "change_pct": float(snapshot.get("change_pct", 0.0) or 0.0),
                    "volume": float(snapshot.get("volume", 0.0) or 0.0),
                    "source": snapshot.get("source", "redis-cache"),
                }

    client: httpx.AsyncClient = request.app.state.http_client
    response = await client.get(f"{HTX_REST_URL}/market/detail/merged", params={"symbol": normalized_symbol})
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "ok":
        raise HTTPException(status_code=502, detail=f"HTX detail endpoint returned unexpected payload for {normalized_symbol}")

    tick = payload.get("tick") or {}
    open_price = float(tick.get("open", 0.0) or 0.0)
    close_price = float(tick.get("close", 0.0) or 0.0)
    change_pct = ((close_price - open_price) / open_price * 100.0) if open_price else 0.0
    return {
        "symbol": normalized_symbol,
        "price": close_price,
        "change_pct": change_pct,
        "volume": float(tick.get("vol", 0.0) or 0.0),
        "source": "htx-rest-detail",
    }


async def fetch_recent_symbol_journal(request: Request, symbol: str, limit: int = 3) -> list[dict[str, Any]]:
    pool = request.app.state.pg_pool
    if pool is None:
        return []

    query = """
    SELECT id, snapshot, indicators, market_context, timestamp
    FROM ai_journal
    ORDER BY timestamp DESC
    LIMIT $1
    """
    async with pool.acquire() as connection:
        rows = await connection.fetch(query, max(limit * 3, 6))

    items: list[dict[str, Any]] = []
    for row in rows:
        entry = format_journal_row(row, symbol=symbol)
        if not entry["symbols"]:
            continue
        items.append(entry)
        if len(items) >= limit:
            break
    return items


async def build_prediction_context(request: Request, symbol: str, horizon_hours: int) -> dict[str, Any]:
    normalized_symbol = ensure_prediction_symbol(symbol)
    snapshot = await get_symbol_snapshot(request, normalized_symbol)
    if snapshot["price"] <= 0:
        raise HTTPException(status_code=503, detail=f"Current price for {normalized_symbol} is unavailable")

    hourly_candles = await fetch_klines(request, normalized_symbol, "60min", 96)
    if len(hourly_candles) < 30:
        raise HTTPException(status_code=503, detail=f"Not enough hourly candles for {normalized_symbol}")

    rsi_points = compute_rsi_series(hourly_candles, 14)
    macd_payload = compute_macd_payload(hourly_candles)
    sma20 = compute_sma_series(hourly_candles, 20)
    sma50 = compute_sma_series(hourly_candles, 50)
    journal_entries = await fetch_recent_symbol_journal(request, normalized_symbol, limit=3)

    last_24 = hourly_candles[-24:] if len(hourly_candles) >= 24 else hourly_candles
    price_values = [item["close"] for item in last_24]
    latest_journal = journal_entries[0] if journal_entries else None
    latest_journal_symbol = latest_journal["symbols"][0] if latest_journal and latest_journal["symbols"] else {}

    return {
        "symbol": normalized_symbol.upper(),
        "horizon_hours": horizon_hours,
        "base_price": round(snapshot["price"], 8),
        "requested_at": serialize_dt(datetime.now(timezone.utc)),
        "spot_snapshot": {
            "price": round(snapshot["price"], 8),
            "change_pct_24h": round(snapshot["change_pct"], 4),
            "volume": round(snapshot["volume"], 4),
            "source": snapshot["source"],
        },
        "market_features": {
            "return_1h_pct": compute_return_pct(hourly_candles, 1),
            "return_4h_pct": compute_return_pct(hourly_candles, 4),
            "return_12h_pct": compute_return_pct(hourly_candles, 12),
            "return_24h_pct": compute_return_pct(hourly_candles, 24),
            "range_24h_low": round(min(price_values), 8) if price_values else None,
            "range_24h_high": round(max(price_values), 8) if price_values else None,
            "sma20": sma20[-1]["value"] if sma20 else None,
            "sma50": sma50[-1]["value"] if sma50 else None,
            "rsi14": rsi_points[-1]["value"] if rsi_points else None,
            "macd": macd_payload["macd"][-1]["value"] if macd_payload["macd"] else None,
            "macd_signal": macd_payload["signal"][-1]["value"] if macd_payload["signal"] else None,
            "macd_histogram": macd_payload["histogram"][-1]["value"] if macd_payload["histogram"] else None,
        },
        "recent_hourly_candles": compact_candle_context(hourly_candles, limit=36),
        "journal_context": {
            "latest_market_context": latest_journal["market_context"] if latest_journal else "",
            "latest_indicators": latest_journal_symbol.get("indicators", {}),
            "recent_notes": [
                {
                    "id": entry["id"],
                    "timestamp": entry["timestamp"],
                    "market_context": entry["market_context"],
                }
                for entry in journal_entries
            ],
        },
        "output_contract": {
            "hours": list(range(1, horizon_hours + 1)),
            "change_pct_basis": "relative to base_price",
            "neutrality": "do not force directional bias without evidence",
        },
    }


async def fetch_klines(request: Request, symbol: str, period: str, size: int) -> list[dict[str, Any]]:
    client: httpx.AsyncClient = request.app.state.http_client
    response = await client.get(
        f"{HTX_REST_URL}/market/history/kline",
        params={"symbol": symbol, "period": period, "size": size},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "ok":
        raise HTTPException(status_code=502, detail=f"HTX returned unexpected payload for {symbol}")
    return normalize_klines(payload.get("data", []))


async def fetch_market_tickers(request: Request) -> dict[str, dict[str, Any]]:
    client: httpx.AsyncClient = request.app.state.http_client
    response = await client.get(f"{HTX_REST_URL}/market/tickers")
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "ok":
        raise HTTPException(status_code=502, detail="HTX tickers endpoint returned unexpected payload")

    tickers: dict[str, dict[str, Any]] = {}
    for item in payload.get("data", []):
        symbol = str(item.get("symbol", "")).lower()
        if not symbol:
            continue
        open_price = float(item.get("open", 0.0) or 0.0)
        close_price = float(item.get("close", 0.0) or 0.0)
        change_pct = ((close_price - open_price) / open_price * 100.0) if open_price else 0.0
        tickers[symbol] = {
            "symbol": symbol,
            "price": close_price,
            "volume": float(item.get("vol", 0.0) or 0.0),
            "change_pct": change_pct,
            "source": "htx-rest",
        }
    return tickers


async def fetch_watchlist_snapshot(request: Request) -> list[dict[str, Any]]:
    redis_client = request.app.state.redis_client
    if redis_client is None:
        return []

    symbols = get_watchlist()
    pipeline = redis_client.pipeline()
    for symbol in symbols:
        pipeline.get(f"ticker:{symbol}")
    raw_items = await pipeline.execute()

    snapshot: list[dict[str, Any]] = []
    for symbol, raw in zip(symbols, raw_items):
        if raw:
            data = safe_json(raw)
            snapshot.append(
                {
                    "symbol": symbol,
                    "price": float(data.get("price", 0.0)),
                    "change_pct": float(data.get("change_pct", 0.0)),
                    "volume": float(data.get("volume", 0.0)),
                    "source": data.get("source", "redis-cache"),
                }
            )
        else:
            snapshot.append(
                {
                    "symbol": symbol,
                    "price": None,
                    "change_pct": None,
                    "volume": None,
                    "source": "unavailable",
                }
            )
    return snapshot


async def fetch_alert_rows(
    request: Request,
    limit: int = 12,
    symbol: str | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    pool = request.app.state.pg_pool
    if pool is None:
        return []

    base_query = """
    SELECT id, symbol, threshold, triggered, timestamp
    FROM alerts
    """
    params: list[Any] = []
    if symbol:
        base_query += " WHERE symbol = $1"
        params.append(symbol)
        base_query += " ORDER BY timestamp DESC LIMIT $2 OFFSET $3"
        params.extend([limit, offset])
    else:
        base_query += " ORDER BY timestamp DESC LIMIT $1 OFFSET $2"
        params.extend([limit, offset])

    async with pool.acquire() as connection:
        rows = await connection.fetch(base_query, *params)

    return [
        {
            "id": row["id"],
            "symbol": row["symbol"],
            "threshold": float(row["threshold"]),
            "triggered": bool(row["triggered"]),
            "timestamp": serialize_dt(row["timestamp"]),
        }
        for row in rows
    ]


async def fetch_alert_counts(request: Request, symbol: str | None = None) -> dict[str, int]:
    pool = request.app.state.pg_pool
    if pool is None:
        return {"total": 0, "acknowledged": 0, "open": 0}

    params: list[Any] = []
    where = ""
    if symbol:
        where = "WHERE symbol = $1"
        params.append(symbol)

    async with pool.acquire() as connection:
        total = await connection.fetchval(f"SELECT COUNT(*) FROM alerts {where}", *params)
        acknowledged = await connection.fetchval(f"SELECT COUNT(*) FROM alerts {where} {'AND' if where else 'WHERE'} triggered = TRUE", *params)

    return {"total": int(total or 0), "acknowledged": int(acknowledged or 0), "open": int((total or 0) - (acknowledged or 0))}


def format_journal_row(row: asyncpg.Record, symbol: str | None = None) -> dict[str, Any]:
    snapshot = safe_json(row["snapshot"])
    indicators = safe_json(row["indicators"])
    symbols = [symbol] if symbol else sorted(snapshot.keys())

    selected_symbols: list[dict[str, Any]] = []
    for selected in symbols:
        if selected not in snapshot:
            continue
        ticker = snapshot.get(selected, {})
        selected_symbols.append(
            {
                "symbol": selected,
                "price": float(ticker.get("price", 0.0)),
                "change_pct": float(ticker.get("change_pct", 0.0)),
                "volume": float(ticker.get("volume", 0.0)),
                "source": ticker.get("source", "snapshot"),
                "indicators": indicators.get(selected, {}),
            }
        )

    return {
        "id": row["id"],
        "timestamp": serialize_dt(row["timestamp"]),
        "market_context": row["market_context"] or "",
        "symbols": selected_symbols,
        "snapshot_raw": snapshot,
        "indicators_raw": indicators,
    }


async def fetch_journal_rows(
    request: Request,
    limit: int = 10,
    symbol: str | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    pool = request.app.state.pg_pool
    if pool is None:
        return []

    query = """
    SELECT id, snapshot, indicators, market_context, timestamp
    FROM ai_journal
    ORDER BY timestamp DESC
    LIMIT $1 OFFSET $2
    """
    async with pool.acquire() as connection:
        rows = await connection.fetch(query, limit, offset)

    items: list[dict[str, Any]] = []
    for row in rows:
        entry = format_journal_row(row, symbol=symbol)
        if symbol and not entry["symbols"]:
            continue
        items.append(entry)
    return items


async def fetch_journal_entry(request: Request, entry_id: int, symbol: str | None = None) -> dict[str, Any] | None:
    pool = request.app.state.pg_pool
    if pool is None:
        return None

    query = """
    SELECT id, snapshot, indicators, market_context, timestamp
    FROM ai_journal
    WHERE id = $1
    """
    async with pool.acquire() as connection:
        row = await connection.fetchrow(query, entry_id)
    if row is None:
        return None

    entry = format_journal_row(row, symbol=symbol)
    if symbol and not entry["symbols"]:
        return None
    return entry


def build_prediction_request_status(request_row: asyncpg.Record) -> dict[str, Any]:
    avg_accuracy = float(request_row["avg_accuracy"]) if request_row["avg_accuracy"] is not None else None
    return {
        "id": request_row["id"],
        "symbol": request_row["symbol"],
        "horizon_hours": request_row["horizon_hours"],
        "base_price": float(request_row["base_price"]),
        "status": request_row["status"],
        "source": request_row["source"] or "webui",
        "requested_by": request_row["requested_by"] or "webui",
        "created_at": serialize_dt(request_row["created_at"]),
        "created_at_display": format_ui_timestamp(request_row["created_at"]),
        "updated_at": serialize_dt(request_row["updated_at"]),
        "updated_at_display": format_ui_timestamp(request_row["updated_at"]),
        "completed_models": int(request_row["completed_models"] or 0),
        "failed_models": int(request_row["failed_models"] or 0),
        "total_points": int(request_row["total_points"] or 0),
        "evaluated_points": int(request_row["evaluated_points"] or 0),
        "avg_accuracy": avg_accuracy,
        "avg_failure": round(100.0 - avg_accuracy, 2) if avg_accuracy is not None else None,
    }


def split_model_display_name(model_name: str) -> tuple[str, str]:
    if " / " not in model_name:
        return "", model_name.strip()

    role_name, short_name = model_name.split(" / ", 1)
    return role_name.strip(), short_name.strip()


def build_prediction_comparison_payload(
    horizon_hours: int,
    models: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    comparison_models: list[dict[str, Any]] = []
    rows_by_hour: dict[int, dict[str, Any]] = {}

    for model in models:
        role_name, short_name = split_model_display_name(model["model_name"])
        model["role_name"] = role_name
        model["short_name"] = short_name
        comparison_models.append(
            {
                "id": model["id"],
                "model_key": model["model_key"],
                "model_name": model["model_name"],
                "model_id": model["model_id"],
                "status": model["status"],
                "role_name": role_name,
                "short_name": short_name,
                "avg_accuracy": model.get("avg_accuracy"),
                "avg_failure": (
                    model.get("avg_failure")
                    if model.get("avg_failure") is not None
                    else round(100.0 - model["avg_accuracy"], 2) if model.get("avg_accuracy") is not None else None
                ),
                "error_message": model.get("error_message", ""),
            }
        )

        for point in model["points"]:
            row = rows_by_hour.setdefault(
                point["forecast_hour"],
                {
                    "forecast_hour": point["forecast_hour"],
                    "target_time": point["target_time"],
                    "target_time_display": point["target_time_display"],
                    "actual_price": None,
                    "actual_change_pct": None,
                    "evaluated_at": None,
                    "evaluated_at_display": None,
                    "points_by_model": {},
                },
            )
            row["points_by_model"][model["id"]] = point

            if row["actual_price"] is None and point["actual_price"] is not None:
                row["actual_price"] = point["actual_price"]
            if row["actual_change_pct"] is None and point["actual_change_pct"] is not None:
                row["actual_change_pct"] = point["actual_change_pct"]
            if row["evaluated_at"] is None and point["evaluated_at"] is not None:
                row["evaluated_at"] = point["evaluated_at"]
                row["evaluated_at_display"] = point["evaluated_at_display"]

    comparison_rows: list[dict[str, Any]] = []
    for hour in range(1, horizon_hours + 1):
        row = rows_by_hour.get(
            hour,
            {
                "forecast_hour": hour,
                "target_time": None,
                "target_time_display": None,
                "actual_price": None,
                "actual_change_pct": None,
                "evaluated_at": None,
                "evaluated_at_display": None,
                "points_by_model": {},
            },
        )
        model_cells: list[dict[str, Any]] = []
        best_accuracy: float | None = None
        best_model_names: list[str] = []

        for model in comparison_models:
            point = row["points_by_model"].get(model["id"])
            accuracy_score = point["accuracy_score"] if point is not None else None
            if accuracy_score is not None:
                if best_accuracy is None or accuracy_score > best_accuracy:
                    best_accuracy = accuracy_score
                    best_model_names = [model["short_name"]]
                elif accuracy_score == best_accuracy:
                    best_model_names.append(model["short_name"])

            cell_state = "forecasted"
            if point is None:
                cell_state = "failed" if model["status"] != "completed" else "pending"

            model_cells.append(
                {
                    "model_id": model["id"],
                    "model_name": model["model_name"],
                    "short_name": model["short_name"],
                    "role_name": model["role_name"],
                    "status": model["status"],
                    "cell_state": cell_state,
                    "point": point,
                    "is_best": accuracy_score is not None and best_accuracy is not None and accuracy_score == best_accuracy,
                }
            )

        if best_accuracy is not None:
            for cell in model_cells:
                point = cell["point"]
                cell["is_best"] = point is not None and point["accuracy_score"] == best_accuracy

        comparison_rows.append(
            {
                "forecast_hour": hour,
                "target_time": row["target_time"],
                "target_time_display": row["target_time_display"],
                "actual_price": row["actual_price"],
                "actual_change_pct": row["actual_change_pct"],
                "evaluated_at": row["evaluated_at"],
                "evaluated_at_display": row["evaluated_at_display"],
                "model_cells": model_cells,
                "best_accuracy": best_accuracy,
                "best_models": best_model_names,
            }
        )

    ranked_models = [model for model in comparison_models if model["avg_accuracy"] is not None]
    top_model = max(ranked_models, key=lambda item: item["avg_accuracy"]) if ranked_models else None
    return comparison_models, comparison_rows, top_model


async def fetch_prediction_requests(request: Request, limit: int = 12, offset: int = 0) -> list[dict[str, Any]]:
    pool = request.app.state.pg_pool
    if pool is None:
        return []

    query = """
    SELECT
        fr.id,
        fr.symbol,
        fr.horizon_hours,
        fr.base_price,
        fr.status,
        fr.source,
        fr.requested_by,
        fr.created_at,
        fr.updated_at,
        COALESCE((
            SELECT COUNT(*)
            FROM forecast_model_runs fmr
            WHERE fmr.request_id = fr.id
              AND fmr.status = 'completed'
        ), 0) AS completed_models,
        COALESCE((
            SELECT COUNT(*)
            FROM forecast_model_runs fmr
            WHERE fmr.request_id = fr.id
              AND fmr.status <> 'completed'
        ), 0) AS failed_models,
        COALESCE((
            SELECT COUNT(*)
            FROM forecast_points fp
            WHERE fp.request_id = fr.id
        ), 0) AS total_points,
        COALESCE((
            SELECT COUNT(*)
            FROM forecast_points fp
            WHERE fp.request_id = fr.id
              AND fp.evaluated_at IS NOT NULL
        ), 0) AS evaluated_points,
        (
            SELECT ROUND(AVG(fp.accuracy_score)::numeric, 2)
            FROM forecast_points fp
            WHERE fp.request_id = fr.id
        ) AS avg_accuracy
    FROM forecast_requests fr
    ORDER BY fr.created_at DESC
    LIMIT $1 OFFSET $2
    """
    async with pool.acquire() as connection:
        rows = await connection.fetch(query, limit, offset)
    return [build_prediction_request_status(row) for row in rows]


async def fetch_prediction_request_detail(request: Request, request_id: int) -> dict[str, Any] | None:
    pool = request.app.state.pg_pool
    if pool is None:
        return None

    summary_query = """
    SELECT
        fr.id,
        fr.symbol,
        fr.horizon_hours,
        fr.base_price,
        fr.status,
        fr.source,
        fr.requested_by,
        fr.created_at,
        fr.updated_at,
        COALESCE((
            SELECT COUNT(*)
            FROM forecast_model_runs fmr
            WHERE fmr.request_id = fr.id
              AND fmr.status = 'completed'
        ), 0) AS completed_models,
        COALESCE((
            SELECT COUNT(*)
            FROM forecast_model_runs fmr
            WHERE fmr.request_id = fr.id
              AND fmr.status <> 'completed'
        ), 0) AS failed_models,
        COALESCE((
            SELECT COUNT(*)
            FROM forecast_points fp
            WHERE fp.request_id = fr.id
        ), 0) AS total_points,
        COALESCE((
            SELECT COUNT(*)
            FROM forecast_points fp
            WHERE fp.request_id = fr.id
              AND fp.evaluated_at IS NOT NULL
        ), 0) AS evaluated_points,
        (
            SELECT ROUND(AVG(fp.accuracy_score)::numeric, 2)
            FROM forecast_points fp
            WHERE fp.request_id = fr.id
        ) AS avg_accuracy
    FROM forecast_requests fr
    WHERE fr.id = $1
    """
    points_query = """
    SELECT
        fmr.id AS model_run_id,
        fmr.model_key,
        fmr.model_name,
        fmr.model_id,
        fmr.status AS model_status,
        fmr.summary,
        fmr.error_message,
        fp.id AS point_id,
        fp.forecast_hour,
        fp.target_time,
        fp.predicted_price,
        fp.predicted_change_pct,
        fp.confidence,
        fp.rationale,
        fp.actual_price,
        fp.actual_change_pct,
        fp.price_error_pct,
        fp.change_error_pct,
        fp.accuracy_score,
        fp.failure_score,
        fp.direction_match,
        fp.verdict,
        fp.evaluated_at
    FROM forecast_model_runs fmr
    LEFT JOIN forecast_points fp ON fp.model_run_id = fmr.id
    WHERE fmr.request_id = $1
    ORDER BY fmr.created_at ASC, fp.forecast_hour ASC
    """

    async with pool.acquire() as connection:
        summary_row = await connection.fetchrow(summary_query, request_id)
        if summary_row is None:
            return None
        point_rows = await connection.fetch(points_query, request_id)

    detail = build_prediction_request_status(summary_row)
    model_runs: dict[int, dict[str, Any]] = {}

    for row in point_rows:
        model_run_id = row["model_run_id"]
        if model_run_id not in model_runs:
            model_runs[model_run_id] = {
                "id": model_run_id,
                "model_key": row["model_key"],
                "model_name": row["model_name"],
                "model_id": row["model_id"],
                "status": row["model_status"],
                "summary": row["summary"] or "",
                "error_message": row["error_message"] or "",
                "points": [],
                "avg_accuracy": None,
                "evaluated_points": 0,
            }

        if row["point_id"] is None:
            continue

        point = {
            "id": row["point_id"],
            "forecast_hour": row["forecast_hour"],
            "target_time": serialize_dt(row["target_time"]),
            "target_time_display": format_ui_timestamp(row["target_time"]),
            "predicted_price": float(row["predicted_price"]),
            "predicted_change_pct": float(row["predicted_change_pct"]),
            "confidence": float(row["confidence"]) if row["confidence"] is not None else None,
            "rationale": row["rationale"] or "",
            "actual_price": float(row["actual_price"]) if row["actual_price"] is not None else None,
            "actual_change_pct": float(row["actual_change_pct"]) if row["actual_change_pct"] is not None else None,
            "price_error_pct": float(row["price_error_pct"]) if row["price_error_pct"] is not None else None,
            "change_error_pct": float(row["change_error_pct"]) if row["change_error_pct"] is not None else None,
            "accuracy_score": float(row["accuracy_score"]) if row["accuracy_score"] is not None else None,
            "failure_score": (
                float(row["failure_score"])
                if row["failure_score"] is not None
                else round(100.0 - float(row["accuracy_score"]), 2) if row["accuracy_score"] is not None else None
            ),
            "direction_match": row["direction_match"],
            "verdict": row["verdict"] or "",
            "evaluated_at": serialize_dt(row["evaluated_at"]),
            "evaluated_at_display": format_ui_timestamp(row["evaluated_at"]),
        }
        model_runs[model_run_id]["points"].append(point)

    for model in model_runs.values():
        scores = [point["accuracy_score"] for point in model["points"] if point["accuracy_score"] is not None]
        model["evaluated_points"] = len(scores)
        model["avg_accuracy"] = round(sum(scores) / len(scores), 2) if scores else None
        model["avg_failure"] = round(100.0 - model["avg_accuracy"], 2) if model["avg_accuracy"] is not None else None

    detail["models"] = list(model_runs.values())
    comparison_models, comparison_rows, top_model = build_prediction_comparison_payload(
        detail["horizon_hours"],
        detail["models"],
    )
    detail["comparison_models"] = comparison_models
    detail["comparison_rows"] = comparison_rows
    detail["top_model"] = top_model
    return detail


async def create_prediction_request_record(
    request: Request,
    symbol: str,
    horizon_hours: int,
    base_price: float,
    requested_by: str,
    source: str,
    model_results: list[Any],
) -> dict[str, Any]:
    pool = request.app.state.pg_pool
    if pool is None:
        raise HTTPException(status_code=503, detail="Postgres is unavailable")

    request_query = """
    INSERT INTO forecast_requests (symbol, horizon_hours, base_price, status, source, requested_by)
    VALUES ($1, $2, $3, $4, $5, $6)
    RETURNING id, created_at
    """
    model_query = """
    INSERT INTO forecast_model_runs (request_id, model_key, model_name, model_id, status, summary, error_message)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
    RETURNING id
    """
    point_query = """
    INSERT INTO forecast_points (
        request_id,
        model_run_id,
        forecast_hour,
        target_time,
        predicted_price,
        predicted_change_pct,
        confidence,
        rationale
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    """

    successful_models = [result for result in model_results if result.status == "completed" and result.points]
    request_status = "active" if successful_models else "failed"

    async with pool.acquire() as connection:
        async with connection.transaction():
            request_row = await connection.fetchrow(
                request_query,
                symbol,
                horizon_hours,
                base_price,
                request_status,
                source,
                requested_by,
            )
            request_id = request_row["id"]
            created_at = to_db_timestamp(request_row["created_at"])

            for result in model_results:
                model_row = await connection.fetchrow(
                    model_query,
                    request_id,
                    result.key,
                    result.name,
                    result.model_id,
                    result.status,
                    result.summary,
                    result.error_message,
                )
                if result.status != "completed" or not result.points:
                    continue

                model_run_id = model_row["id"]
                for point in result.points:
                    await connection.execute(
                        point_query,
                        request_id,
                        model_run_id,
                        point.hour,
                        to_db_timestamp(created_at + timedelta(hours=point.hour)),
                        point.predicted_price,
                        point.predicted_change_pct,
                        point.confidence,
                        point.rationale,
                    )

    detail = await fetch_prediction_request_detail(request, request_id=request_id)
    if detail is None:
        raise HTTPException(status_code=500, detail="Prediction request was created but could not be loaded")
    return detail


async def append_prediction_model_result(
    pool: asyncpg.Pool,
    request_id: int,
    result: Any,
) -> None:
    created_at_query = "SELECT created_at FROM forecast_requests WHERE id = $1"
    model_query = """
    INSERT INTO forecast_model_runs (request_id, model_key, model_name, model_id, status, summary, error_message)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
    RETURNING id
    """
    point_query = """
    INSERT INTO forecast_points (
        request_id,
        model_run_id,
        forecast_hour,
        target_time,
        predicted_price,
        predicted_change_pct,
        confidence,
        rationale
    )
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    """

    async with pool.acquire() as connection:
        async with connection.transaction():
            created_at = await connection.fetchval(created_at_query, request_id)
            if created_at is None:
                return

            created_at_ts = to_db_timestamp(created_at)
            model_row = await connection.fetchrow(
                model_query,
                request_id,
                result.key,
                result.name,
                result.model_id,
                result.status,
                result.summary,
                result.error_message,
            )
            if result.status != "completed" or not result.points:
                return

            model_run_id = model_row["id"]
            for point in result.points:
                await connection.execute(
                    point_query,
                    request_id,
                    model_run_id,
                    point.hour,
                    to_db_timestamp(created_at_ts + timedelta(hours=point.hour)),
                    point.predicted_price,
                    point.predicted_change_pct,
                    point.confidence,
                    point.rationale,
                )


async def finalize_oracle_prediction(app: FastAPI, request_id: int, context_payload: dict[str, Any]) -> None:
    pool = getattr(app.state, "pg_pool", None)
    if pool is None:
        return

    try:
        result = await generate_model_prediction(MODEL_CONFIGS["minimax"], context_payload)
        await append_prediction_model_result(pool, request_id, result)
    except Exception as exc:
        log.warning("Background Oracle prediction failed for request %s: %s", request_id, exc)


async def run_prediction_request(
    request: Request,
    background_tasks: BackgroundTasks,
    symbol: str,
    horizon_hours: int,
    requested_by: str,
    source: str,
) -> dict[str, Any]:
    normalized_symbol = ensure_prediction_symbol(symbol)
    normalized_horizon = normalize_prediction_horizon(horizon_hours)

    try:
        context_payload = await build_prediction_context(request, normalized_symbol, normalized_horizon)
        results = await generate_prediction_bundle(context_payload, model_keys=SYNC_PREDICTION_MODEL_KEYS)
        detail = await create_prediction_request_record(
            request,
            symbol=normalized_symbol,
            horizon_hours=normalized_horizon,
            base_price=float(context_payload["base_price"]),
            requested_by=requested_by,
            source=source,
            model_results=results,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch market context for {normalized_symbol}") from exc

    oracle_model = next((item for item in list_prediction_models() if item["key"] == "minimax"), None)
    if oracle_model and oracle_model["available"]:
        background_tasks.add_task(finalize_oracle_prediction, request.app, detail["id"], context_payload)

    return detail


async def fetch_health_snapshot(request: Request) -> dict[str, Any]:
    redis_client = request.app.state.redis_client
    pool = request.app.state.pg_pool

    redis_ok = False
    postgres_ok = False
    collector_ok = False
    last_journal_at: datetime | None = None

    if redis_client is not None:
        try:
            redis_ok = bool(await redis_client.ping())
        except Exception as exc:
            log.warning("Redis health probe failed: %s", exc)

    if pool is not None:
        try:
            async with pool.acquire() as connection:
                postgres_ok = (await connection.fetchval("SELECT 1")) == 1
                last_journal_at = await connection.fetchval("SELECT MAX(timestamp) FROM ai_journal")
        except Exception as exc:
            log.warning("Postgres health probe failed: %s", exc)

    journal_age_minutes: float | None = None
    if last_journal_at:
        if last_journal_at.tzinfo is None:
            last_journal_at = last_journal_at.replace(tzinfo=timezone.utc)
        journal_age_minutes = round((datetime.now(timezone.utc) - last_journal_at).total_seconds() / 60.0, 1)
        collector_ok = journal_age_minutes <= 20.0

    return {
        "generated_at": serialize_dt(datetime.now(timezone.utc)),
        "redis": redis_ok,
        "postgres": postgres_ok,
        "collector_feed": collector_ok,
        "last_journal_at": serialize_dt(last_journal_at),
        "last_journal_age_minutes": journal_age_minutes,
    }


async def refresh_watchlist_cache(request: Request) -> dict[str, Any]:
    redis_client = request.app.state.redis_client
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Redis is unavailable")

    market_tickers = await fetch_market_tickers(request)
    pipeline = redis_client.pipeline()
    updated_symbols: list[str] = []
    for symbol in get_watchlist():
        ticker = market_tickers.get(symbol)
        if not ticker:
            continue
        pipeline.setex(f"ticker:{symbol}", 300, json.dumps(ticker))
        updated_symbols.append(symbol)
    await pipeline.execute()

    return {"updated": len(updated_symbols), "symbols": updated_symbols}


async def build_manual_journal_snapshot(request: Request) -> dict[str, Any]:
    market_tickers = await fetch_market_tickers(request)
    watchlist = get_watchlist()
    snapshot: dict[str, Any] = {}
    indicators: dict[str, Any] = {}

    for symbol in watchlist:
        ticker = market_tickers.get(symbol)
        if ticker:
            snapshot[symbol] = ticker
        else:
            snapshot[symbol] = {"symbol": symbol, "price": 0.0, "change_pct": 0.0, "volume": 0.0, "source": "missing"}

        candles = await fetch_klines(request, symbol, "15min", 120)
        rsi_points = compute_rsi_series(candles, 14)
        macd_payload = compute_macd_payload(candles)
        indicators[symbol] = {
            "rsi_14": rsi_points[-1]["value"] if rsi_points else None,
            "macd": macd_payload["macd"][-1]["value"] if macd_payload["macd"] else None,
            "macd_signal": macd_payload["signal"][-1]["value"] if macd_payload["signal"] else None,
            "macd_hist": macd_payload["histogram"][-1]["value"] if macd_payload["histogram"] else None,
        }

    context = "Manual snapshot requested from webui admin actions."
    pool = request.app.state.pg_pool
    if pool is None:
        raise HTTPException(status_code=503, detail="Postgres is unavailable")

    query = """
    INSERT INTO ai_journal (snapshot, indicators, market_context)
    VALUES ($1, $2, $3)
    RETURNING id, timestamp
    """
    async with pool.acquire() as connection:
        row = await connection.fetchrow(query, json.dumps(snapshot), json.dumps(indicators), context)

    return {
        "id": row["id"],
        "timestamp": serialize_dt(row["timestamp"]),
        "symbols": watchlist,
    }


async def toggle_alert_triggered(request: Request, alert_id: int) -> dict[str, Any]:
    pool = request.app.state.pg_pool
    if pool is None:
        raise HTTPException(status_code=503, detail="Postgres is unavailable")

    query = """
    UPDATE alerts
    SET triggered = NOT triggered
    WHERE id = $1
    RETURNING id, symbol, triggered
    """
    async with pool.acquire() as connection:
        row = await connection.fetchrow(query, alert_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Alert not found")

    return {"id": row["id"], "symbol": row["symbol"], "triggered": bool(row["triggered"])}


async def clear_acknowledged_alerts(request: Request) -> int:
    pool = request.app.state.pg_pool
    if pool is None:
        raise HTTPException(status_code=503, detail="Postgres is unavailable")

    query = """
    DELETE FROM alerts
    WHERE triggered = TRUE
    """
    async with pool.acquire() as connection:
        result = await connection.execute(query)
    return int(result.split()[-1])


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_auth_config()

    app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
    app.state.redis_client = None
    app.state.pg_pool = None

    try:
        app.state.redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
    except Exception as exc:
        log.warning("Redis client bootstrap failed: %s", exc)

    db_url = normalize_db_url(os.getenv("DATABASE_URL"))
    if db_url:
        try:
            app.state.pg_pool = await asyncpg.create_pool(dsn=db_url)
            await ensure_prediction_schema(app.state.pg_pool)
        except Exception as exc:
            log.warning("Postgres pool bootstrap failed: %s", exc)

    try:
        yield
    finally:
        if app.state.pg_pool is not None:
            await app.state.pg_pool.close()
        if app.state.redis_client is not None:
            await app.state.redis_client.aclose()
        await app.state.http_client.aclose()


app = FastAPI(title="WORED Web UI", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=get_session_secret(),
    session_cookie="wored_webui_session",
    same_site="lax",
    https_only=False,
    max_age=60 * 60 * 12,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    if not get_auth_enabled():
        return RedirectResponse(url="/", status_code=303)
    if is_authenticated(request):
        return RedirectResponse(url=next or "/", status_code=303)
    return template_response(request, "login.html", page_title="Web UI Login", next_target=next or "/")


@app.post("/login")
async def login_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    if not get_auth_enabled():
        return RedirectResponse(url="/", status_code=303)

    if hmac.compare_digest(username, get_admin_username()) and hmac.compare_digest(password, get_admin_password()):
        request.session["authenticated"] = True
        request.session["username"] = username
        ensure_csrf_token(request)
        set_flash(request, "ok", "Web UI session opened.")
        return RedirectResponse(url=next or "/", status_code=303)

    set_flash(request, "bad", "Invalid username or password.")
    return RedirectResponse(url=f"/login?next={quote(next or '/', safe='/?=&')}", status_code=303)


@app.post("/logout")
async def logout_action(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
async def index(request: Request):
    auth_redirect = require_page_auth(request)
    if auth_redirect is not None:
        return auth_redirect
    return template_response(request, "index.html", page_title="WORED Control Room")


@app.get("/alerts", response_class=HTMLResponse)
async def alerts_page(
    request: Request,
    symbol: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
):
    auth_redirect = require_page_auth(request)
    if auth_redirect is not None:
        return auth_redirect

    selected_symbol = normalize_symbol(symbol) if symbol else None
    offset = (page - 1) * DEFAULT_PAGE_SIZE
    alerts = await fetch_alert_rows(request, limit=DEFAULT_PAGE_SIZE, symbol=selected_symbol, offset=offset)
    counts = await fetch_alert_counts(request, selected_symbol)
    has_next_page = counts["total"] > offset + len(alerts)

    return template_response(
        request,
        "alerts.html",
        page_title="Alert History",
        alerts=alerts,
        selected_symbol=selected_symbol or "",
        counts=counts,
        page=page,
        has_next_page=has_next_page,
    )


@app.get("/journal", response_class=HTMLResponse)
async def journal_page(
    request: Request,
    symbol: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
):
    auth_redirect = require_page_auth(request)
    if auth_redirect is not None:
        return auth_redirect

    selected_symbol = normalize_symbol(symbol) if symbol else None
    offset = (page - 1) * DEFAULT_PAGE_SIZE
    entries = await fetch_journal_rows(request, limit=DEFAULT_PAGE_SIZE, symbol=selected_symbol, offset=offset)
    selected_entry = entries[0] if entries else None

    return template_response(
        request,
        "journal.html",
        page_title="AI Journal",
        entries=entries,
        selected_entry=selected_entry,
        selected_symbol=selected_symbol or "",
        page=page,
        has_next_page=len(entries) == DEFAULT_PAGE_SIZE,
        snapshot_pretty=json_pretty(selected_entry["snapshot_raw"]) if selected_entry else "",
        indicators_pretty=json_pretty(selected_entry["indicators_raw"]) if selected_entry else "",
    )


@app.get("/journal/{entry_id}", response_class=HTMLResponse)
async def journal_detail_page(
    request: Request,
    entry_id: int,
    symbol: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
):
    auth_redirect = require_page_auth(request)
    if auth_redirect is not None:
        return auth_redirect

    selected_symbol = normalize_symbol(symbol) if symbol else None
    offset = (page - 1) * DEFAULT_PAGE_SIZE
    entries = await fetch_journal_rows(request, limit=DEFAULT_PAGE_SIZE, symbol=selected_symbol, offset=offset)
    selected_entry = await fetch_journal_entry(request, entry_id=entry_id, symbol=selected_symbol)
    if selected_entry is None:
        raise HTTPException(status_code=404, detail="Journal entry not found")

    return template_response(
        request,
        "journal.html",
        page_title=f"AI Journal Entry #{entry_id}",
        entries=entries,
        selected_entry=selected_entry,
        selected_symbol=selected_symbol or "",
        page=page,
        has_next_page=len(entries) == DEFAULT_PAGE_SIZE,
        snapshot_pretty=json_pretty(selected_entry["snapshot_raw"]),
        indicators_pretty=json_pretty(selected_entry["indicators_raw"]),
    )


@app.get("/predictions", response_class=HTMLResponse)
async def predictions_page(
    request: Request,
    request_id: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
):
    auth_redirect = require_page_auth(request)
    if auth_redirect is not None:
        return auth_redirect

    offset = (page - 1) * DEFAULT_PAGE_SIZE
    prediction_requests = await fetch_prediction_requests(request, limit=DEFAULT_PAGE_SIZE, offset=offset)
    selected_request = None

    if request_id is not None:
        selected_request = await fetch_prediction_request_detail(request, request_id=request_id)
        if selected_request is None:
            raise HTTPException(status_code=404, detail="Prediction request not found")
    elif prediction_requests:
        selected_request = await fetch_prediction_request_detail(request, request_id=prediction_requests[0]["id"])

    return template_response(
        request,
        "predictions.html",
        page_title="Prediction Lab",
        prediction_requests=prediction_requests,
        selected_request=selected_request,
        model_statuses=list_prediction_models(),
        prediction_horizons=sorted(ALLOWED_PREDICTION_HORIZONS),
        page=page,
        has_next_page=len(prediction_requests) == DEFAULT_PAGE_SIZE,
    )


@app.get("/predictions/{request_id}", response_class=HTMLResponse)
async def prediction_detail_page(request: Request, request_id: int, page: int = Query(default=1, ge=1)):
    auth_redirect = require_page_auth(request)
    if auth_redirect is not None:
        return auth_redirect

    offset = (page - 1) * DEFAULT_PAGE_SIZE
    prediction_requests = await fetch_prediction_requests(request, limit=DEFAULT_PAGE_SIZE, offset=offset)
    selected_request = await fetch_prediction_request_detail(request, request_id=request_id)
    if selected_request is None:
        raise HTTPException(status_code=404, detail="Prediction request not found")

    return template_response(
        request,
        "predictions.html",
        page_title=f"Prediction #{request_id}",
        prediction_requests=prediction_requests,
        selected_request=selected_request,
        model_statuses=list_prediction_models(),
        prediction_horizons=sorted(ALLOWED_PREDICTION_HORIZONS),
        page=page,
        has_next_page=len(prediction_requests) == DEFAULT_PAGE_SIZE,
    )


@app.post("/predictions")
async def create_prediction(
    background_tasks: BackgroundTasks,
    request: Request,
    symbol: str = Form(...),
    horizon_hours: int = Form(...),
    csrf_token: str = Form(...),
):
    require_api_auth(request)
    verify_csrf_token(request, csrf_token)
    detail = await run_prediction_request(
        request=request,
        background_tasks=background_tasks,
        symbol=symbol,
        horizon_hours=horizon_hours,
        requested_by=request.session.get("username", "local-webui"),
        source="webui",
    )

    successful_models = [model for model in detail["models"] if model["status"] == "completed" and model["points"]]
    failed_models = [model for model in detail["models"] if model["status"] != "completed"]
    oracle_model = next((item for item in list_prediction_models() if item["key"] == "minimax"), None)

    if successful_models:
        oracle_note = " Oracle continues in the background." if oracle_model and oracle_model["available"] else ""
        set_flash(
            request,
            "ok",
            f"Prediction #{detail['id']} created for {normalized_symbol.upper()} / {normalized_horizon}h. "
            f"Completed models: {len(successful_models)}. Failed models: {len(failed_models)}.{oracle_note}",
        )
    else:
        set_flash(
            request,
            "bad",
            f"Prediction #{detail['id']} was recorded, but all models failed. Check provider keys and model status.",
        )

    response = RedirectResponse(url=f"/predictions/{detail['id']}", status_code=303)
    response.background = background_tasks
    return response


@app.post("/api/internal/predictions")
async def api_internal_create_prediction(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: dict[str, Any] = Body(...),
    x_internal_token: str | None = Header(default=None),
):
    verify_internal_api_token(x_internal_token)
    detail = await run_prediction_request(
        request=request,
        background_tasks=background_tasks,
        symbol=str(payload.get("symbol") or ""),
        horizon_hours=int(payload.get("horizon_hours") or 0),
        requested_by=str(payload.get("requested_by") or "telegram-bot"),
        source=str(payload.get("source") or "telegram"),
    )
    return detail


@app.post("/admin/actions/refresh-cache")
async def admin_refresh_cache(request: Request, csrf_token: str = Form(...), next: str = Form("/")):
    require_api_auth(request)
    verify_csrf_token(request, csrf_token)
    result = await refresh_watchlist_cache(request)
    set_flash(request, "ok", f"Refreshed Redis cache for {result['updated']} symbol(s).")
    return RedirectResponse(url=next or "/", status_code=303)


@app.post("/admin/actions/journal-snapshot")
async def admin_journal_snapshot(request: Request, csrf_token: str = Form(...), next: str = Form("/")):
    require_api_auth(request)
    verify_csrf_token(request, csrf_token)
    entry = await build_manual_journal_snapshot(request)
    set_flash(request, "ok", f"Created AI journal entry #{entry['id']} for {len(entry['symbols'])} symbol(s).")
    return RedirectResponse(url=next or "/journal", status_code=303)


@app.post("/admin/actions/clear-acknowledged-alerts")
async def admin_clear_acknowledged_alerts(request: Request, csrf_token: str = Form(...), next: str = Form("/alerts")):
    require_api_auth(request)
    verify_csrf_token(request, csrf_token)
    deleted_count = await clear_acknowledged_alerts(request)
    set_flash(request, "ok", f"Deleted {deleted_count} acknowledged alert(s).")
    return RedirectResponse(url=next or "/alerts", status_code=303)


@app.post("/admin/alerts/{alert_id}/toggle")
async def admin_toggle_alert(
    request: Request,
    alert_id: int,
    csrf_token: str = Form(...),
    next: str = Form("/alerts"),
):
    require_api_auth(request)
    verify_csrf_token(request, csrf_token)
    result = await toggle_alert_triggered(request, alert_id)
    state_label = "acknowledged" if result["triggered"] else "reopened"
    set_flash(request, "ok", f"Alert #{result['id']} marked as {state_label}.")
    return RedirectResponse(url=next or "/alerts", status_code=303)


@app.get("/api/health")
async def api_health(request: Request):
    return await fetch_health_snapshot(request)


@app.get("/api/overview")
async def api_overview(request: Request):
    require_api_auth(request)
    watchlist = await fetch_watchlist_snapshot(request)
    alerts = await fetch_alert_rows(request, limit=8)
    health = await fetch_health_snapshot(request)

    return {
        "watchlist": watchlist,
        "health": health,
        "alerts": alerts,
        "available_symbols": get_watchlist(),
        "default_symbol": get_watchlist()[0] if get_watchlist() else "btcusdt",
    }


@app.get("/api/alerts")
async def api_alerts(
    request: Request,
    symbol: str | None = Query(default=None),
    limit: int = Query(default=12, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
):
    require_api_auth(request)
    selected_symbol = normalize_symbol(symbol) if symbol else None
    counts = await fetch_alert_counts(request, selected_symbol)
    return {
        "items": await fetch_alert_rows(request, limit=limit, symbol=selected_symbol, offset=offset),
        "symbol": selected_symbol,
        "counts": counts,
    }


@app.get("/api/journal")
async def api_journal(
    request: Request,
    symbol: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=64),
    offset: int = Query(default=0, ge=0, le=10000),
):
    require_api_auth(request)
    selected_symbol = normalize_symbol(symbol) if symbol else None
    return {
        "items": await fetch_journal_rows(request, limit=limit, symbol=selected_symbol, offset=offset),
        "symbol": selected_symbol,
    }


@app.get("/api/journal/{entry_id}")
async def api_journal_entry(request: Request, entry_id: int, symbol: str | None = Query(default=None)):
    require_api_auth(request)
    selected_symbol = normalize_symbol(symbol) if symbol else None
    entry = await fetch_journal_entry(request, entry_id=entry_id, symbol=selected_symbol)
    if entry is None:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    return entry


@app.get("/api/predictions")
async def api_predictions(
    request: Request,
    limit: int = Query(default=12, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
):
    require_api_auth(request)
    return {"items": await fetch_prediction_requests(request, limit=limit, offset=offset)}


@app.get("/api/predictions/{request_id}")
async def api_prediction_detail(request: Request, request_id: int):
    require_api_auth(request)
    detail = await fetch_prediction_request_detail(request, request_id=request_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Prediction request not found")
    return detail


@app.get("/api/candles")
async def api_candles(
    request: Request,
    symbol: str = Query(default="btcusdt"),
    period: str = Query(default=DEFAULT_PERIOD),
    size: int = Query(default=DEFAULT_SIZE, ge=30, le=MAX_KLINE_SIZE),
):
    require_api_auth(request)
    normalized_symbol = normalize_symbol(symbol)
    normalized_period = normalize_period(period)
    normalized_size = clamp_size(size)

    try:
        candles = await fetch_klines(request, normalized_symbol, normalized_period, normalized_size)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"HTX request failed for {normalized_symbol}") from exc

    return {
        "symbol": normalized_symbol,
        "period": normalized_period,
        "size": normalized_size,
        "candles": candles,
        "volume": build_volume_series(candles),
        "sma20": compute_sma_series(candles, 20),
        "sma50": compute_sma_series(candles, 50),
        "rsi14": compute_rsi_series(candles, 14),
        "macd": compute_macd_payload(candles),
        "fetched_at": serialize_dt(datetime.now(timezone.utc)),
    }




# ─── ТЗ: Futures Lab, Strategy Performance, Model Management ─────────

@app.get("/api/sim-positions")
async def api_sim_positions(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List sim positions for Futures Lab dashboard."""
    require_api_auth(request)
    pool = getattr(request.app.state, "pg_pool", None)
    if not pool:
        return JSONResponse(status_code=503, content={"detail": "Postgres unavailable"})
    async with pool.acquire() as conn:
        if status and status in ("open", "closed", "liquidated"):
            rows = await conn.fetch(
                "SELECT * FROM sim_positions WHERE status = $1 ORDER BY opened_at DESC LIMIT $2",
                status, limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM sim_positions ORDER BY opened_at DESC LIMIT $1", limit
            )
    items = []
    for r in rows:
        items.append({
            "id": r["id"], "user_id": r["user_id"], "symbol": r["symbol"],
            "direction": r["direction"], "leverage": r["leverage"],
            "margin_type": r.get("margin_mode", r.get("margin_type")),
            "order_type": r["order_type"],
            "entry_price": float(r["entry_price"]) if r["entry_price"] else None,
            "size": float(r["size"]) if r["size"] else None,
            "margin": float(r["margin"]) if r["margin"] else None,
            "status": r["status"],
            "realized_pnl": float(r["realized_pnl"]) if r["realized_pnl"] else None,
            "close_reason": r.get("close_reason"),
            "created_at": serialize_dt(r["opened_at"]) if r.get("opened_at") else (serialize_dt(r["created_at"]) if r.get("created_at") else None),
            "closed_at": serialize_dt(r["closed_at"]) if r.get("closed_at") else None,
        })
    open_count = len([i for i in items if i["status"] == "open"])
    closed_count = len([i for i in items if i["status"] in ("closed", "liquidated")])
    return {"items": items, "open_count": open_count, "closed_count": closed_count, "total": len(items)}


@app.get("/api/strategy")
async def api_strategy(request: Request):
    """Get latest strategy rules for Strategy Performance page."""
    require_api_auth(request)
    pool = getattr(request.app.state, "pg_pool", None)
    if not pool:
        return JSONResponse(status_code=503, content={"detail": "Postgres unavailable"})
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM strategy_rules ORDER BY version DESC LIMIT 1"
        )
        evals = await conn.fetch(
            "SELECT * FROM sim_evaluations ORDER BY created_at DESC LIMIT 10"
        )
    if not row:
        return {"strategy": None, "evaluations": [], "message": "No strategy rules yet"}
    strategy = {
        "version": row["version"],
        "rules": safe_json(row["rules"]) if isinstance(row["rules"], str) else row["rules"],
        "source": row.get("source", "unknown"),
        "created_at": serialize_dt(row["created_at"]) if row["created_at"] else None,
    }
    eval_list = []
    for e in evals:
        eval_list.append({
            "run_id": e["evaluation_run_id"], "total": e["total_positions"],
            "winrate": float(e["winrate"]) if e["winrate"] else 0,
            "avg_pnl": float(e["avg_pnl"]) if e["avg_pnl"] else 0,
            "max_drawdown": float(e["max_drawdown"]) if e["max_drawdown"] else 0,
            "liquidation_rate": float(e["liquidation_rate"]) if e["liquidation_rate"] else 0,
            "details": safe_json(e["details"]) if isinstance(e["details"], str) else (e["details"] or {}),
            "created_at": serialize_dt(e["created_at"]) if e["created_at"] else None,
        })
    return {"strategy": strategy, "evaluations": eval_list}


@app.get("/api/models/probe")
async def api_models_probe(request: Request):
    """Probe all configured AI models for Model Management page."""
    require_api_auth(request)
    import asyncio as _asyncio
    import os as _os
    import httpx as _httpx

    from prediction_engine import MODEL_CONFIGS

    results = []
    for key, cfg in MODEL_CONFIGS.items():
        model_id = getattr(cfg, "model_id", "unknown")
        name = getattr(cfg, "name", key)
        base_url = getattr(cfg, "base_url", "") or getattr(cfg, "endpoint", "")
        api_key_env = getattr(cfg, "api_key_env", "")
        api_key = _os.getenv(api_key_env, "")
        timeout = getattr(cfg, "timeout", 15.0)
        status = "no_key" if not api_key else "unknown"
        latency_ms = None
        if api_key and base_url:
            try:
                start = _asyncio.get_event_loop().time()
                async with _httpx.AsyncClient(timeout=min(timeout, 15)) as client:
                    resp = await client.post(
                        f"{base_url.rstrip('/')}/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        json={"model": model_id, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 5},
                    )
                    latency_ms = int((_asyncio.get_event_loop().time() - start) * 1000)
                    if resp.status_code == 200:
                        content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                        status = "ok" if content.strip() else "empty"
                    else:
                        status = f"http_{resp.status_code}"
            except _httpx.TimeoutException:
                status = "timeout"
            except Exception as exc:
                err_msg = str(exc)[:100]
                status = "error"

        provider = "ollama" if "ollama" in base_url else "dashscope" if "dashscope" in base_url else "minimax" if "minimax" in base_url else "unknown"

        results.append({
            "key": key,
            "name": name,
            "provider": provider,
            "model_id": model_id,
            "base_url": base_url[:60],
            "status": status,
            "latency_ms": latency_ms,
            "available": bool(api_key),
        })
    return {"models": results}


# ─── Strategy API: metrics, positions, evaluate ──────────────────────

@app.get("/api/strategy/metrics")
async def api_strategy_metrics(request: Request):
    """Compute KPI metrics from real sim_positions."""
    require_api_auth(request)
    pool = getattr(request.app.state, "pg_pool", None)
    if not pool:
        return JSONResponse(status_code=503, content={"detail": "Postgres unavailable"})
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, realized_pnl, close_reason, leverage, entry_price, close_price, "
            "opened_at, closed_at, symbol, direction FROM sim_positions "
            "ORDER BY COALESCE(closed_at, opened_at) DESC LIMIT 100"
        )
    total = len(rows)
    if total == 0:
        return {"total": 0, "winrate": 0, "avg_pnl": 0, "total_pnl": 0,
                "max_drawdown": 0, "liquidation_rate": 0, "open_count": 0,
                "closed_count": 0, "wins": 0, "losses": 0, "liquidations": 0,
                "avg_leverage": 0, "pnl_series": [], "best_pnl": 0, "worst_pnl": 0}

    wins = losses = liquidations = 0
    open_count = 0
    closed_count = 0
    pnl_list = []
    pnl_series = []
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    leverage_sum = 0.0

    for r in rows:
        status = r["status"] or ""
        pnl = float(r["realized_pnl"] or 0)
        leverage_sum += float(r["leverage"] or 0)
        if status == "open":
            open_count += 1
            continue
        closed_count += 1
        cumulative += pnl
        pnl_series.append({
            "x": (r["closed_at"].isoformat() if r["closed_at"] else ""),
            "y": round(cumulative, 4),
            "pnl": round(pnl, 4),
            "symbol": r["symbol"],
            "status": status,
        })
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
        pnl_list.append(pnl)
        if pnl > 0:
            wins += 1
        else:
            losses += 1
        if status == "liquidated" or r["close_reason"] == "liquidation":
            liquidations += 1

    winrate = (wins / closed_count * 100) if closed_count > 0 else 0
    avg_pnl = (sum(pnl_list) / len(pnl_list)) if pnl_list else 0
    liq_rate = (liquidations / closed_count * 100) if closed_count > 0 else 0
    avg_lev = (leverage_sum / total) if total > 0 else 0

    return {
        "total": total,
        "open_count": open_count,
        "closed_count": closed_count,
        "wins": wins,
        "losses": losses,
        "liquidations": liquidations,
        "winrate": round(winrate, 1),
        "avg_pnl": round(avg_pnl, 2),
        "total_pnl": round(sum(pnl_list), 2),
        "max_drawdown": round(max_dd, 2),
        "liquidation_rate": round(liq_rate, 1),
        "avg_leverage": round(avg_lev, 1),
        "best_pnl": round(max(pnl_list), 2) if pnl_list else 0,
        "worst_pnl": round(min(pnl_list), 2) if pnl_list else 0,
        "pnl_series": pnl_series,
    }


@app.get("/api/strategy/positions")
async def api_strategy_positions(request: Request, limit: int = 20):
    """Get recent sim positions for Strategy page table."""
    require_api_auth(request)
    pool = getattr(request.app.state, "pg_pool", None)
    if not pool:
        return JSONResponse(status_code=503, content={"detail": "Postgres unavailable"})
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, symbol, direction, leverage, margin_mode, entry_price, close_price, "
            "realized_pnl, status, close_reason, opened_at, closed_at, ai_managed "
            "FROM sim_positions ORDER BY COALESCE(closed_at, opened_at) DESC LIMIT $1",
            min(limit, 50),
        )
    positions = []
    for r in rows:
        positions.append({
            "id": r["id"],
            "symbol": r["symbol"],
            "direction": r["direction"],
            "leverage": int(r["leverage"]),
            "margin_mode": r["margin_mode"],
            "entry_price": float(r["entry_price"]) if r["entry_price"] else None,
            "close_price": float(r["close_price"]) if r["close_price"] else None,
            "realized_pnl": float(r["realized_pnl"] or 0),
            "status": r["status"],
            "close_reason": r["close_reason"] or "",
            "opened_at": serialize_dt(r["opened_at"]) if r["opened_at"] else None,
            "closed_at": serialize_dt(r["closed_at"]) if r["closed_at"] else None,
            "ai_managed": bool(r["ai_managed"]),
        })
    return {"positions": positions}


@app.post("/api/strategy/evaluate")
async def api_strategy_evaluate(request: Request):
    """Run evaluation on sim_positions and generate new strategy rules."""
    require_api_auth(request)
    pool = getattr(request.app.state, "pg_pool", None)
    if not pool:
        return JSONResponse(status_code=503, content={"detail": "Postgres unavailable"})

    # Compute metrics from real sim_positions
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, realized_pnl, close_reason, leverage FROM sim_positions "
            "WHERE status IN ('closed','liquidated') ORDER BY closed_at DESC LIMIT 100"
        )
    if len(rows) < 3:
        return JSONResponse(status_code=400, content={
            "ok": False, "message": f"Need 3+ closed positions, have {len(rows)}"
        })

    total = len(rows)
    wins = liquidations = 0
    pnl_list = []
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0

    for r in rows:
        pnl = float(r["realized_pnl"] or 0)
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
        pnl_list.append(pnl)
        if pnl > 0:
            wins += 1
        if r["status"] == "liquidated" or r["close_reason"] == "liquidation":
            liquidations += 1

    winrate = (wins / total * 100) if total > 0 else 0
    avg_pnl = (sum(pnl_list) / total) if total > 0 else 0
    liq_rate = (liquidations / total * 100) if total > 0 else 0
    details = {
        "wins": wins, "losses": total - wins, "liquidations": liquidations,
        "best_pnl": max(pnl_list) if pnl_list else 0,
        "worst_pnl": min(pnl_list) if pnl_list else 0,
    }

    import uuid as _uuid
    run_id = _uuid.uuid4().hex[:16]

    # Save evaluation
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO sim_evaluations (evaluation_run_id, total_positions, winrate, avg_pnl, max_drawdown, liquidation_rate, details) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            run_id, total, round(winrate, 2), round(avg_pnl, 4),
            round(max_dd, 2), round(liq_rate, 2), json.dumps(details),
        )
        # Get current version
        current = await conn.fetchrow(
            "SELECT version FROM strategy_rules ORDER BY id DESC LIMIT 1"
        )
        version = (current["version"] + 1) if current else 1

    # Heuristic rules generation (no AI dependency in webui)
    adjustments = []
    if liq_rate > 20:
        adjustments.append({"parameter": "max_leverage", "old": 200, "new": 150,
                           "reason": f"Ликвидации {liq_rate:.0f}% > 20%, снизить плечо"})
    if winrate < 40:
        adjustments.append({"parameter": "rsi_entry_filter", "old": 50, "new": 60,
                           "reason": f"Winrate {winrate:.0f}% < 40%, ужесточить вход"})
    if avg_pnl < 0:
        adjustments.append({"parameter": "min_confirmation_signals", "old": 1, "new": 2,
                           "reason": f"Avg PnL {avg_pnl:.2f} < 0, больше подтверждений"})
    if max_dd > 50:
        adjustments.append({"parameter": "stop_loss_pct", "old": 0, "new": 5,
                           "reason": f"Max DD {max_dd:.0f}% > 50%, добавить стоп-лосс"})
    if winrate > 60 and liq_rate < 10:
        adjustments.append({"parameter": "status", "old": "active", "new": "active",
                           "reason": "Метрики в норме, без изменений"})

    summary = f"Winrate {winrate:.0f}%, ликвидации {liq_rate:.0f}%, avg PnL {avg_pnl:.2f}"
    confidence = "high" if total >= 20 else "medium" if total >= 5 else "low"
    new_rules = {"adjustments": adjustments, "summary": summary, "confidence": confidence}

    # Save new strategy rules
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO strategy_rules (version, rules, source) VALUES ($1, $2, $3)",
            version, json.dumps(new_rules), "heuristic_webui",
        )

    return {
        "ok": True,
        "run_id": run_id,
        "version": version,
        "metrics": {
            "total": total, "winrate": round(winrate, 1), "avg_pnl": round(avg_pnl, 2),
            "max_drawdown": round(max_dd, 2), "liquidation_rate": round(liq_rate, 1),
            "wins": wins, "losses": total - wins, "liquidations": liquidations,
        },
        "rules": new_rules,
    }


@app.get("/futures-lab", response_class=HTMLResponse)
async def futures_lab_page(request: Request):
    """Futures Lab page — sim positions dashboard."""
    auth_redirect = require_page_auth(request)
    if auth_redirect is not None:
        return auth_redirect
    return template_response(request, "futures_lab.html", page_title="Futures Lab")


@app.get("/strategy", response_class=HTMLResponse)
async def strategy_page(request: Request):
    """Strategy Performance page — rules and evaluations."""
    auth_redirect = require_page_auth(request)
    if auth_redirect is not None:
        return auth_redirect
    return template_response(request, "strategy.html", page_title="Strategy")


@app.get("/model-management", response_class=HTMLResponse)
async def model_management_page(request: Request):
    """Model Management page — probe and rotate models."""
    auth_redirect = require_page_auth(request)
    if auth_redirect is not None:
        return auth_redirect
    return template_response(request, "models.html", page_title="Model Management")


# ─── Daily Pipeline v2 — Telegram Mini App API (ТЗ backend_contract v1) ─

VALID_REVISION_COMMANDS = {"continue", "tighten", "reduce", "pause", "close_all"}

RISK_PARAMS = {
    "defensive": {"maxsessiondrawdownpct": 5.0, "maxfailedentries": 2, "cooldownminutesafterstop": 30},
    "balanced": {"maxsessiondrawdownpct": 10.0, "maxfailedentries": 3, "cooldownminutesafterstop": 20},
    "aggressive": {"maxsessiondrawdownpct": 20.0, "maxfailedentries": 4, "cooldownminutesafterstop": 10},
}


def _get_session_risk_for_snapshot(risk_mode: str) -> dict:
    """ТЗ 5.2 — plan.sessionrisk object for Mini App."""
    rp = RISK_PARAMS.get(risk_mode, RISK_PARAMS["balanced"])
    return {
        "maxsessiondrawdownpct": rp["maxsessiondrawdownpct"],
        "maxfailedentries": rp["maxfailedentries"],
        "maxsimultaneouspositions": 1,
        "cooldownminutesafterstop": rp["cooldownminutesafterstop"],
        "stoptradingafterliquidation": True,
    }
TELEGRAM_WEBAPP_AUTH_DISABLED = os.getenv("TELEGRAM_WEBAPP_AUTH_DISABLED", "true").lower() in {"1", "true", "yes"}


def _verify_telegram_init_data(init_data: str) -> dict | None:
    """
    ТЗ 6.2 — валидация Telegram WebApp initData.
    Возвращает {"user_id": int, "username": str} или None.
    """
    if TELEGRAM_WEBAPP_AUTH_DISABLED:
        return {"user_id": 5249526259, "username": "admin"}

    if not init_data:
        return None

    try:
        import urllib.parse
        import hmac
        import hashlib

        parsed = urllib.parse.parse_qs(init_data)
        hash_val = parsed.get("hash", [None])[0]
        if not hash_val:
            return None

        # Build data-check string
        parsed.pop("hash", None)
        data_check_string = "\n".join(f"{k}={v[0]}" for k, v in sorted(parsed.items()))

        bot_token = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or ""
        if not bot_token:
            return None

        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_hash, hash_val):
            return None

        user_json = parsed.get("user", [None])[0]
        if user_json:
            user_data = json.loads(user_json)
            return {"user_id": user_data.get("id"), "username": user_data.get("username", "")}
        return None
    except Exception:
        return None


def _get_telegram_user(request: Request) -> dict | None:
    """Extract and verify Telegram user from request headers."""
    init_data = request.headers.get("X-Telegram-Init-Data", "")
    return _verify_telegram_init_data(init_data)


@app.get("/daily-session", response_class=HTMLResponse)
async def daily_session_page(request: Request):
    """Daily Session page — WebUI."""
    auth_redirect = require_page_auth(request)
    if auth_redirect is not None:
        return auth_redirect
    return template_response(request, "daily_session.html", page_title="Daily Session")


@app.get("/api/daily-session/active")
async def api_daily_session_active(request: Request):
    """ТЗ 5.2 — unified snapshot for Mini App. Direct SQL (no cross-container imports)."""
    require_api_auth(request)

    pool = request.app.state.pg_pool
    if pool is None:
        return JSONResponse({"session": None, "plan": None, "metrics": None, "trades": [], "events": [], "revision": None}, status_code=503)

    async with pool.acquire() as conn:
        session = await conn.fetchrow(
            "SELECT * FROM trading_sessions ORDER BY created_at DESC LIMIT 1",
        )
        if not session:
            return {"session": None, "plan": None, "metrics": None, "trades": [], "events": [], "revision": None}

        session_id = str(session["id"])
        plan = await conn.fetchrow(
            "SELECT * FROM session_plans WHERE session_id = $1 ORDER BY version DESC LIMIT 1",
            session_id,
        )
        entries = await conn.fetch(
            "SELECT * FROM planned_entries WHERE session_id = $1 AND plan_version = $2 ORDER BY created_at",
            session_id, session["active_plan_version"],
        )
        trades = await conn.fetch(
            "SELECT * FROM executed_trades WHERE session_id = $1 ORDER BY opened_at DESC",
            session_id,
        )
        revisions = await conn.fetch(
            "SELECT * FROM session_revisions WHERE session_id = $1 ORDER BY created_at DESC LIMIT 1",
            session_id,
        )
        metrics = await conn.fetchrow(
            "SELECT * FROM session_metrics WHERE session_id = $1",
            session_id,
        )
        events = await conn.fetch(
            "SELECT * FROM execution_events WHERE session_id = $1 ORDER BY created_at DESC LIMIT 50",
            session_id,
        )

    def safe_jsonb(val):
        if val is None:
            return None
        if isinstance(val, str):
            return json.loads(val)
        return dict(val)

    plan_json = safe_jsonb(plan["plan_json"]) if plan else None
    failed_entries = sum(1 for e in entries if e["status"] == "expired")
    last_rev = revisions[0] if revisions else None

    return {
        "session": {
            "id": str(session["id"]),
            "user_id": session["user_id"],
            "symbol": session["symbol"],
            "exchange": session["exchange"],
            "status": session["status"].upper(),
            "riskmode": session["risk_mode"],
            "sessionstart": serialize_dt(session["session_start"]),
            "sessionend": serialize_dt(session["session_end"]),
            "failedentries": failed_entries,
            "lastcommand": last_rev["execution_command"] if last_rev else None,
            "activeplanversion": session["active_plan_version"],
            # §6 — trade profile fields
            "tradedirection": session.get("trade_direction", "auto"),
            "tradehorizon": session.get("trade_horizon", "fast"),
            "targetnetprofitusdt": float(session.get("target_net_profit_usdt") or 1.5),
            "maxtradedurationminutes": int(session.get("max_trade_duration_minutes") or 15),
            "costfilterenabled": bool(session.get("cost_filter_enabled", True)),
            "sessiongoalprofile": session.get("session_goal_profile", "fast_profit"),
        },
        "plan": {
            "id": str(plan["id"]),
            "version": session["active_plan_version"],
            "thesis": plan_json.get("thesis") if plan_json else None,
            "marketregime": plan_json.get("market_regime") if plan_json else None,
            "primaryscenario": plan_json.get("primary_scenario") if plan_json else None,
            "alternativescenario": plan_json.get("alternative_scenario") if plan_json else None,
            "notradecondition": plan_json.get("no_trade_condition") if plan_json else None,
            "riskmode": session["risk_mode"],
            "sessionrisk": _get_session_risk_for_snapshot(session["risk_mode"]),
            "entries": [
                {
                    "id": str(e["id"]),
                    "side": e["side"],
                    "status": e["status"],
                    "entryzonefrom": float(e["entry_zone_from"]),
                    "entryzoneto": float(e["entry_zone_to"]),
                    "stoploss": float(e["stop_loss"]),
                    "takeprofit": safe_jsonb(e["take_profit_json"]),
                    "leverage": e["recommended_leverage"],
                    "budgetsharepct": float(e["budget_share_pct"]),
                    "reasoncode": e["reason_code"],
                }
                for e in entries
            ],
        } if plan else None,
        "metrics": {
            "tradecount": int(metrics["trade_count"]),
            "wincount": int(metrics["win_count"]),
            "losscount": int(metrics["loss_count"]),
            "liquidationcount": int(metrics["liquidation_count"]),
            "totalpnlusdt": float(metrics["total_pnl_usdt"]),
            "totalpnlpct": float(metrics["total_pnl_pct"]),
            "maxdrawdownpct": float(metrics["max_drawdown_pct"]),
            "profitfactor": float(metrics["profit_factor"]) if metrics["profit_factor"] else None,
            "timeinmarketpct": float(metrics["time_in_market_pct"]) if metrics["time_in_market_pct"] else None,
            # §6 — fast-mode metrics
            "grosspnlusdt": float(metrics.get("gross_pnl_usdt") or 0),
            "feesusdt": float(metrics.get("fees_usdt") or 0),
            "slippageusdt": float(metrics.get("slippage_usdt") or 0),
            "netpnaftercostsusdt": float(metrics.get("net_pnl_after_costs_usdt") or 0),
            "avgtradedurationminutes": float(metrics["avg_trade_duration_minutes"]) if metrics.get("avg_trade_duration_minutes") else None,
            "rejectedbycostfiltercount": int(metrics.get("rejected_by_cost_filter_count") or 0),
            "targethitscount": int(metrics.get("target_hits_count") or 0),
        } if metrics else {
            "tradecount": 0,
            "wincount": 0,
            "losscount": 0,
            "liquidationcount": 0,
            "totalpnlusdt": 0.0,
            "totalpnlpct": 0.0,
            "maxdrawdownpct": 0.0,
            "profitfactor": None,
            "timeinmarketpct": 0.0,
            "grosspnlusdt": 0.0,
            "feesusdt": 0.0,
            "slippageusdt": 0.0,
            "netpnaftercostsusdt": 0.0,
            "avgtradedurationminutes": None,
            "rejectedbycostfiltercount": 0,
            "targethitscount": 0,
        },
        "trades": [
            {
                "id": str(t["id"]),
                "side": t["side"],
                "leverage": t["leverage"],
                "entryprice": float(t["entry_price"]),
                "exitprice": float(t["exit_price"]) if t["exit_price"] else None,
                "pnl": float(t["realised_pnl_usdt"]) if t["realised_pnl_usdt"] else None,
                "close_reason": t["close_reason"],
                "status": t["status"],
                "openedat": serialize_dt(t["opened_at"]),
                "closedat": serialize_dt(t["closed_at"]),
            }
            for t in trades
        ],
        "events": [
            {
                "id": str(e["id"]),
                "timestamp": serialize_dt(e["created_at"]),
                "eventtype": e["event_type"],
                "statebefore": (e["state_before"] or "").upper(),
                "stateafter": (e["state_after"] or "").upper(),
                "eventpayload": safe_jsonb(e["event_payload"]),
            }
            for e in events
        ],
        "revision": {
            "id": str(last_rev["id"]),
            "executioncommand": last_rev["execution_command"],
            "createdat": serialize_dt(last_rev["created_at"]),
        } if last_rev else None,
    }


@app.post("/api/daily-session/start")
async def api_daily_session_start(request: Request, body: dict = Body(...)):
    """ТЗ 5.1 + §6.1 — start new daily trading session with atomic bootstrap.

    Calls create_session_with_bootstrap() which:
    1. Creates session in DB
    2. Creates initial session_metrics
    3. Generates initial plan via Analyst AI
    4. Bootstraps: validates market + transitions to ARMED or BLOCKED
    """
    require_api_auth(request)

    pool = request.app.state.pg_pool
    if pool is None:
        return JSONResponse({"ok": False, "error": "database_unavailable"}, status_code=503)

    budget = float(body.get("budget_usdt", 100.0))
    risk_mode = body.get("risk_mode", "balanced")
    duration = int(body.get("duration_hours", 8))
    user_id = int(body.get("user_id", 5249526259))

    # §6 — Trade profile from body
    trade_direction = body.get("trade_direction", "auto")
    trade_horizon = body.get("trade_horizon", "fast")
    target_net_profit = float(body.get("target_net_profit_usdt", 1.5))
    max_trade_duration = int(body.get("max_trade_duration_minutes", 15))
    cost_filter_enabled = bool(body.get("cost_filter_enabled", True))

    if risk_mode not in ("defensive", "balanced", "aggressive"):
        return JSONResponse({"ok": False, "error": "invalid_risk_mode"}, status_code=400)

    if trade_direction not in ("long", "short", "both", "auto"):
        return JSONResponse({"ok": False, "error": "invalid_trade_direction"}, status_code=400)
    if trade_horizon not in ("fast", "medium", "long"):
        return JSONResponse({"ok": False, "error": "invalid_trade_horizon"}, status_code=400)

    # Check for existing active session (ТЗ 11.2)
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id, status FROM trading_sessions WHERE user_id = $1 AND status NOT IN ('completed', 'stopped', 'failed') ORDER BY created_at DESC LIMIT 1",
            user_id,
        )

    if existing:
        return {"ok": True, "session_id": str(existing["id"]), "status": existing["status"],
                "message": "session already active"}

    # Build trade profile
    trade_profile = {
        "trade_direction": trade_direction,
        "trade_horizon": trade_horizon,
        "target_net_profit_usdt": target_net_profit,
        "max_trade_duration_minutes": max_trade_duration,
        "cost_filter_enabled": cost_filter_enabled,
    }

    # Atomic create + plan + bootstrap
    import sys
    sys.path.insert(0, "/chatbot")
    from services.session_manager import create_session_with_bootstrap

    result = await create_session_with_bootstrap(
        user_id=user_id,
        budget_usdt=budget,
        duration_hours=duration,
        risk_mode=risk_mode,
        source="webui",
        trade_profile=trade_profile,
    )

    if not result.get("ok"):
        return JSONResponse({
            "ok": False,
            "error": result.get("error", "unknown"),
            "session_id": result.get("session_id"),
            "status": result.get("status", "failed"),
            "reason": result.get("reason", result.get("bootstrap_reason")),
        }, status_code=500)

    return JSONResponse({
        "ok": True,
        "session_id": result["session_id"],
        "status": result.get("status", "idle"),
        "plan_version": result.get("plan_version"),
        "entries": result.get("entries", 0),
        "bootstrap_reason": result.get("bootstrap_reason"),
        "trade_profile": trade_profile,
    }, status_code=201)


@app.post("/api/daily-session/revision")
async def api_daily_session_revision(request: Request, body: dict = Body(...)):
    """ТЗ 5.3 — send execution control command. Direct SQL."""
    require_api_auth(request)

    session_id = body.get("session_id", "")
    command = body.get("command", "")
    source = body.get("source", "telegram_miniapp")

    if not session_id:
        return JSONResponse({"ok": False, "error": "missing_session_id", "message": "session_id is required"}, status_code=400)

    if command not in VALID_REVISION_COMMANDS:
        return JSONResponse({"ok": False, "error": "invalid_revision_command", "message": f"command {command} is not supported"}, status_code=422)

    pool = request.app.state.pg_pool
    if pool is None:
        return JSONResponse({"ok": False, "error": "database_unavailable"}, status_code=503)

    import uuid as uuid_mod

    async with pool.acquire() as conn:
        session = await conn.fetchrow(
            "SELECT id, status, active_plan_version FROM trading_sessions WHERE id = $1",
            session_id,
        )
        if not session:
            return JSONResponse({"ok": False, "error": "session_not_found", "message": "session not found"}, status_code=404)

        current_status = session["status"]
        if current_status in ("stopped", "completed"):
            return JSONResponse({"ok": False, "error": "session_ended", "message": f"session is {current_status}"}, status_code=409)

        base_version = session["active_plan_version"]
        new_version = base_version + 1
        revision_id = str(uuid_mod.uuid4())
        event_id = str(uuid_mod.uuid4())
        now = datetime.now(timezone.utc)

        revision_payload = {
            "command": command,
            "source": source,
            "previous_status": current_status,
            "applied_at": now.isoformat(),
        }

        # Write revision record
        await conn.execute(
            """
            INSERT INTO session_revisions
                (id, session_id, base_version, new_version, execution_command, revision_json, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, NOW())
            """,
            revision_id, session_id, base_version, new_version,
            command, json.dumps(revision_payload),
        )

        # Update active plan version
        await conn.execute(
            "UPDATE trading_sessions SET active_plan_version = $2, updated_at = NOW() WHERE id = $1",
            session_id, new_version,
        )

        # Apply state transitions
        new_status = current_status
        if command == "pause":
            new_status = "paused"
            await conn.execute(
                "UPDATE trading_sessions SET status = 'paused', updated_at = NOW() WHERE id = $1",
                session_id,
            )
        elif command == "close_all":
            new_status = "stopped"
            await conn.execute(
                "UPDATE trading_sessions SET status = 'stopped', final_status_reason = 'close_all_command', updated_at = NOW() WHERE id = $1",
                session_id,
            )
        elif command == "continue" and current_status == "paused":
            new_status = "armed"
            await conn.execute(
                "UPDATE trading_sessions SET status = 'armed', updated_at = NOW() WHERE id = $1",
                session_id,
            )

        # Write execution event
        await conn.execute(
            """
            INSERT INTO execution_events
                (id, session_id, event_type, state_before, state_after, event_payload, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, NOW())
            """,
            event_id, session_id,
            f"revision_{command}",
            current_status, new_status,
            json.dumps(revision_payload),
        )

    # Publish to Redis for live stream
    try:
        import redis.asyncio as redis_async
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        r = redis_async.from_url(redis_url, decode_responses=True)
        event_msg = json.dumps({
            "id": event_id,
            "session_id": session_id,
            "event_type": f"revision_{command}",
            "state_before": current_status,
            "state_after": new_status,
            "event_payload": revision_payload,
            "created_at": now.isoformat(),
        })
        await r.publish("daily_session_events", event_msg)
        await r.aclose()
    except Exception as exc:
        log.warning("Redis publish failed: %s", exc)

    log.info("Revision applied: session=%s cmd=%s %s→%s",
             session_id, command, current_status, new_status)

    return {
        "ok": True,
        "session_id": session_id,
        "executioncommand": command,
        "accepted": True,
        "applied_at": now.isoformat(),
        "new_status": new_status,
    }


@app.get("/api/daily-session/events")
async def api_daily_session_events(
    request: Request,
    session_id: str = Query(...),
    limit: int = Query(50, ge=1, le=200),
    after: str | None = Query(None),
    event_type: str | None = Query(None),
):
    """ТЗ §5.2.3 — polling fallback for execution events with type filter."""
    require_api_auth(request)

    pool = request.app.state.pg_pool
    if pool is None:
        return JSONResponse({"session_id": session_id, "events": []}, status_code=503)

    async with pool.acquire() as conn:
        if after and event_type:
            events = await conn.fetch(
                """
                SELECT id, event_type, state_before, state_after, event_payload, created_at
                FROM execution_events
                WHERE session_id = $1 AND created_at > $2 AND event_type = $3
                ORDER BY created_at DESC LIMIT $4
                """,
                session_id, after, event_type, limit,
            )
        elif event_type:
            events = await conn.fetch(
                """
                SELECT id, event_type, state_before, state_after, event_payload, created_at
                FROM execution_events
                WHERE session_id = $1 AND event_type = $2
                ORDER BY created_at DESC LIMIT $3
                """,
                session_id, event_type, limit,
            )
        elif after:
            events = await conn.fetch(
                """
                SELECT id, event_type, state_before, state_after, event_payload, created_at
                FROM execution_events
                WHERE session_id = $1 AND created_at > $2
                ORDER BY created_at DESC LIMIT $3
                """,
                session_id, after, limit,
            )
        else:
            events = await conn.fetch(
                """
                SELECT id, event_type, state_before, state_after, event_payload, created_at
                FROM execution_events
                WHERE session_id = $1
                ORDER BY created_at DESC LIMIT $2
                """,
                session_id, limit,
            )

    def safe_jsonb(val):
        if val is None:
            return None
        if isinstance(val, str):
            return json.loads(val)
        return dict(val)

    def payload_summary(payload):
        """Extract human-readable summary from event payload."""
        if not payload:
            return ""
        p = safe_jsonb(payload)
        if not p or not isinstance(p, dict):
            return ""
        parts = []
        for k in ("side", "leverage", "entry_price", "reason_code", "close_reason",
                     "expected_net_profit", "new_status", "command"):
            if k in p:
                parts.append(f"{k}={p[k]}")
        return " | ".join(parts)

    return {
        "session_id": session_id,
        "events": [
            {
                "id": str(e["id"]),
                "timestamp": serialize_dt(e["created_at"]),
                "eventtype": e["event_type"],
                "statebefore": (e["state_before"] or "").upper(),
                "stateafter": (e["state_after"] or "").upper(),
                "eventpayload": safe_jsonb(e["event_payload"]),
                "payloadsummary": payload_summary(e["event_payload"]),
            }
            for e in events
        ],
    }


@app.get("/api/daily-session/diagnostics")
async def api_daily_session_diagnostics(request: Request, session_id: str = Query(...)):
    """§6.4 + §7.3 — execution guardrails + diagnostic messages.

    Checks: market snapshot freshness, plan validity, risk limits, execution readiness.
    Returns blocked_reason if any check fails.
    """
    require_api_auth(request)
    pool = request.app.state.pg_pool

    checks = {
        "session_id": session_id,
        "ready": True,
        "blocked_reason": None,
        "checks": {},
    }

    # 1. Session exists + status
    async with pool.acquire() as conn:
        session = await conn.fetchrow(
            "SELECT status, risk_mode, initial_budget_usdt, active_plan_version, session_end FROM trading_sessions WHERE id=$1",
            session_id,
        )
    if not session:
        return JSONResponse({"ok": False, "error": "session_not_found"}, status_code=404)

    checks["checks"]["session_status"] = {"ok": True, "value": session["status"]}
    if session["status"] in ("completed", "stopped", "failed"):
        checks["ready"] = False
        checks["blocked_reason"] = f"session_{session['status']}"

    # 2. Market snapshot freshness (Redis)
    import redis.asyncio as aioredis
    redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    try:
        redis = aioredis.from_url(redis_url, decode_responses=True)
        ticker_raw = await redis.get("ticker:btcusdt")
        if ticker_raw:
            ticker = json.loads(ticker_raw)
            checks["checks"]["market_snapshot"] = {"ok": True, "price": ticker.get("price")}
        else:
            checks["checks"]["market_snapshot"] = {"ok": False, "reason": "stale"}
            checks["ready"] = False
            checks["blocked_reason"] = "market_snapshot_stale"
        await redis.aclose()
    except Exception as exc:
        checks["checks"]["market_snapshot"] = {"ok": False, "reason": str(exc)}
        checks["ready"] = False
        checks["blocked_reason"] = "redis_unavailable"

    # 3. Active plan exists
    async with pool.acquire() as conn:
        plan = await conn.fetchrow(
            "SELECT version, plan_json FROM session_plans WHERE session_id=$1 ORDER BY version DESC LIMIT 1",
            session_id,
        )
        entries_count = await conn.fetchval(
            "SELECT count(*) FROM planned_entries WHERE session_id=$1 AND status='planned'",
            session_id,
        )

    if not plan:
        checks["checks"]["active_plan"] = {"ok": False, "reason": "no_plan"}
        checks["ready"] = False
        checks["blocked_reason"] = "no_active_plan"
    else:
        plan_data = json.loads(plan["plan_json"]) if plan["plan_json"] else {}
        checks["checks"]["active_plan"] = {
            "ok": True, "version": plan["version"],
            "entries": entries_count,
            "market_regime": plan_data.get("market_regime"),
            "primary_scenario": plan_data.get("primary_scenario"),
        }
        if entries_count == 0:
            checks["ready"] = False
            checks["blocked_reason"] = "no_planned_entries"

    # 4. Risk gate — check if max drawdown exceeded
    async with pool.acquire() as conn:
        metrics = await conn.fetchrow(
            "SELECT max_drawdown_pct, total_pnl_usdt FROM session_metrics WHERE session_id=$1",
            session_id,
        )
    if metrics:
        max_dd = float(metrics["max_drawdown_pct"] or 0)
        risk_limits = {"defensive": 3.0, "balanced": 5.0, "aggressive": 8.0}
        limit = risk_limits.get(session["risk_mode"], 5.0)
        if max_dd >= limit:
            checks["checks"]["risk_gate"] = {"ok": False, "max_dd": max_dd, "limit": limit}
            checks["ready"] = False
            checks["blocked_reason"] = "max_drawdown_exceeded"
        else:
            checks["checks"]["risk_gate"] = {"ok": True, "max_dd": max_dd, "limit": limit}

    # 5. Session window
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    session_end = session["session_end"]
    if session_end.tzinfo is None:
        session_end = session_end.replace(tzinfo=timezone.utc)
    if now >= session_end:
        checks["checks"]["session_window"] = {"ok": False, "expired": True}
        checks["ready"] = False
        checks["blocked_reason"] = "session_window_expired"
    else:
        checks["checks"]["session_window"] = {"ok": True, "expires_in_min": int((session_end - now).total_seconds() / 60)}

    return {"ok": True, "diagnostics": checks}


@app.get("/api/daily-session/events/stream")
async def api_daily_session_events_stream(
    request: Request,
    session_id: str = Query(...),
):
    """ТЗ 5.5 — SSE stream for live execution events."""
    require_api_auth(request)

    from sse_starlette.sse import EventSourceResponse

    async def event_generator():
        # Send heartbeat + initial events
        pool = request.app.state.pg_pool
        last_ts = None

        if pool:
            async with pool.acquire() as conn:
                initial = await conn.fetch(
                    "SELECT id, event_type, state_before, state_after, event_payload, created_at FROM execution_events WHERE session_id = $1 ORDER BY created_at DESC LIMIT 20",
                    session_id,
                )
            for e in reversed(initial):
                payload = e["event_payload"]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                yield {
                    "event": "execution_event",
                    "id": str(e["id"]),
                    "data": json.dumps({
                        "id": str(e["id"]),
                        "session_id": session_id,
                        "timestamp": serialize_dt(e["created_at"]),
                        "eventtype": e["event_type"],
                        "statebefore": e["state_before"],
                        "stateafter": e["state_after"],
                        "eventpayload": payload,
                    }),
                }
                last_ts = e["created_at"]

        # Subscribe to Redis pub/sub for live events
        try:
            import redis.asyncio as redis_async
            redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
            r = redis_async.from_url(redis_url, decode_responses=True)
            pubsub = r.pubsub()
            await pubsub.subscribe("daily_session_events")
        except Exception:
            r = None
            pubsub = None

        import asyncio
        heartbeat_counter = 0
        while True:
            if await request.is_disconnected():
                break

            # Check Redis pub/sub
            if pubsub:
                try:
                    msg = await asyncio.wait_for(pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0), timeout=2.0)
                    if msg and msg["type"] == "message":
                        data = json.loads(msg["data"])
                        if data.get("session_id") == session_id:
                            yield {
                                "event": "execution_event",
                                "id": data.get("id", ""),
                                "data": json.dumps(data),
                            }
                except asyncio.TimeoutError:
                    pass
                except Exception:
                    pass
            else:
                # Fallback: poll DB for new events
                if pool:
                    try:
                        async with pool.acquire() as conn:
                            new_events = await conn.fetch(
                                "SELECT id, event_type, state_before, state_after, event_payload, created_at FROM execution_events WHERE session_id = $1 AND created_at > $2 ORDER BY created_at ASC LIMIT 10",
                                session_id, last_ts or datetime.min.replace(tzinfo=timezone.utc),
                            )
                        for e in new_events:
                            payload = e["event_payload"]
                            if isinstance(payload, str):
                                payload = json.loads(payload)
                            yield {
                                "event": "execution_event",
                                "id": str(e["id"]),
                                "data": json.dumps({
                                    "id": str(e["id"]),
                                    "session_id": session_id,
                                    "timestamp": serialize_dt(e["created_at"]),
                                    "eventtype": e["event_type"],
                                    "statebefore": e["state_before"],
                                    "stateafter": e["state_after"],
                                    "eventpayload": payload,
                                }),
                            }
                            last_ts = e["created_at"]
                    except Exception:
                        pass
                await asyncio.sleep(2)

            # Heartbeat every ~20 seconds
            heartbeat_counter += 1
            if heartbeat_counter >= 10:
                heartbeat_counter = 0
                yield {"event": "heartbeat", "data": ""}

        if pubsub:
            await pubsub.unsubscribe("daily_session_events")
            await r.aclose()

    return EventSourceResponse(event_generator())


@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=401, content={"detail": exc.detail})
    return RedirectResponse(url="/login", status_code=303)


# ─── ТЗ Dashboard v2 — New API endpoints ─────────────────────────────

@app.get("/api/daily-session/list")
async def api_daily_session_list(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """ТЗ §6.2.3 — list sessions for selector."""
    require_api_auth(request)

    pool = request.app.state.pg_pool
    if pool is None:
        return JSONResponse({"sessions": [], "total": 0}, status_code=503)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, status, risk_mode, symbol, session_start, session_end,
                   active_plan_version, initial_budget_usdt, created_at,
                   trade_direction, trade_horizon, target_net_profit_usdt,
                   session_goal_profile
            FROM trading_sessions
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset,
        )
        total_row = await conn.fetchrow("SELECT count(*) as cnt FROM trading_sessions")

    return {
        "sessions": [
            {
                "id": str(r["id"]),
                "status": r["status"].upper(),
                "riskmode": r["risk_mode"],
                "symbol": r["symbol"],
                "sessionstart": serialize_dt(r["session_start"]),
                "sessionend": serialize_dt(r["session_end"]),
                "activeplanversion": r["active_plan_version"],
                "budget": float(r["initial_budget_usdt"]),
                "createdat": serialize_dt(r["created_at"]),
                "tradedirection": r.get("trade_direction", "auto"),
                "tradehorizon": r.get("trade_horizon", "fast"),
                "targetnetprofitusdt": float(r.get("target_net_profit_usdt") or 1.5),
                "sessiongoalprofile": r.get("session_goal_profile", "fast_profit"),
            }
            for r in rows
        ],
        "total": int(total_row["cnt"]) if total_row else 0,
    }


@app.get("/api/daily-session/positions")
async def api_daily_session_positions(
    request: Request,
    session_id: str = Query(...),
):
    """ТЗ §5.2.1 — positions tab data."""
    require_api_auth(request)

    pool = request.app.state.pg_pool
    if pool is None:
        return JSONResponse({"positions": []}, status_code=503)

    async with pool.acquire() as conn:
        trades = await conn.fetch(
            """
            SELECT id, side, margin_mode, leverage, entry_price, exit_price,
                   position_qty, position_notional_usdt, margin_used_usdt,
                   open_fee_usdt, close_fee_usdt, realised_pnl_usdt,
                   status, opened_at, closed_at, close_reason,
                   trade_horizon, trade_direction, target_net_profit_usdt,
                   expected_total_fees_usdt, expected_slippage_usdt,
                   expected_net_profit_usdt, actual_trade_duration_minutes
            FROM executed_trades
            WHERE session_id = $1
            ORDER BY opened_at DESC
            """,
            session_id,
        )

    def safe_float(v):
        return float(v) if v is not None else None

    return {
        "session_id": session_id,
        "positions": [
            {
                "id": str(t["id"]),
                "symbol": "BTCUSDT",
                "side": t["side"].upper(),
                "marginmode": t["margin_mode"],
                "leverage": t["leverage"],
                "entryprice": safe_float(t["entry_price"]),
                "exitprice": safe_float(t["exit_price"]),
                "markprice": safe_float(t["exit_price"]) if t["status"] == "closed" else None,
                "sizeusdt": safe_float(t["position_notional_usdt"]),
                "marginused": safe_float(t["margin_used_usdt"]),
                "unrealizedpnl": None if t["status"] == "closed" else safe_float(t["realised_pnl_usdt"]),
                "realizedpnl": safe_float(t["realised_pnl_usdt"]) if t["status"] == "closed" else None,
                "openfee": safe_float(t["open_fee_usdt"]),
                "closefee": safe_float(t["close_fee_usdt"]),
                "feesusdt": (safe_float(t["open_fee_usdt"]) or 0) + (safe_float(t["close_fee_usdt"]) or 0),
                "slippageusdt": safe_float(t.get("expected_slippage_usdt")),
                "netpnl": safe_float(t.get("expected_net_profit_usdt")),
                "liquidationprice": None,
                "status": t["status"].upper(),
                "opentime": serialize_dt(t["opened_at"]),
                "closetime": serialize_dt(t["closed_at"]),
                "tradehorizon": t.get("trade_horizon"),
                "targetnetprofit": safe_float(t.get("target_net_profit_usdt")),
                "closereason": t["close_reason"],
            }
            for t in trades
        ],
    }


@app.get("/api/daily-session/orders")
async def api_daily_session_orders(
    request: Request,
    session_id: str = Query(...),
):
    """ТЗ §5.2.2 — orders tab data (from planned_entries)."""
    require_api_auth(request)

    pool = request.app.state.pg_pool
    if pool is None:
        return JSONResponse({"orders": []}, status_code=503)

    async with pool.acquire() as conn:
        entries = await conn.fetch(
            """
            SELECT id, side, status, entry_zone_from, entry_zone_to,
                   stop_loss, take_profit_json, recommended_leverage,
                   budget_share_pct, margin_mode, confirmation_rule,
                   reason_code, created_at
            FROM planned_entries
            WHERE session_id = $1
            ORDER BY created_at DESC
            """,
            session_id,
        )

    def safe_jsonb(val):
        if val is None:
            return None
        if isinstance(val, str):
            return json.loads(val)
        return dict(val)

    return {
        "session_id": session_id,
        "orders": [
            {
                "id": str(e["id"]),
                "type": "conditional",
                "side": e["side"].upper(),
                "pricefrom": float(e["entry_zone_from"]),
                "priceto": float(e["entry_zone_to"]),
                "stoploss": float(e["stop_loss"]),
                "takeprofit": safe_jsonb(e["take_profit_json"]),
                "leverage": e["recommended_leverage"],
                "budgetsharepct": float(e["budget_share_pct"]),
                "marginmode": e["margin_mode"],
                "status": e["status"].upper(),
                "reason": e.get("reason_code"),
                "confirmationrule": e["confirmation_rule"],
                "createdat": serialize_dt(e["created_at"]),
            }
            for e in entries
        ],
    }

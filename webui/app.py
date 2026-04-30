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

    material = f"{get_admin_username()}::{get_admin_password()}::{os.getenv('TELEGRAM_ADMIN_ID', 'local')}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"wored-webui-{digest}"


def get_internal_api_token() -> str:
    explicit = os.getenv("WEBUI_INTERNAL_TOKEN", "").strip()
    if explicit:
        return explicit

    material = f"wored-internal::{get_session_secret()}::{os.getenv('TELEGRAM_TOKEN', 'local')}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


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


@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=401, content={"detail": exc.detail})
    return RedirectResponse(url="/login", status_code=303)

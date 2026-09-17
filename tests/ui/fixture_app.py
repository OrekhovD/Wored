"""UI-11 FastAPI ASGI test fixture app.

Uses real Jinja2 templates from webui/templates/ and static from webui/static/.
Overrides lifespan to skip PG/Redis. Provides routes for all 13 pages,
API endpoints, and /__qa__/health with nonce.
"""
from __future__ import annotations

import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

# Make webui importable for ui_presenters
PROJECT_WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
WEBUI_DIR = Path(os.environ.get("WORED_WEBUI_DIR", str(PROJECT_WEBUI_DIR)))
if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

from ui_presenters import present_deck_ui, present_preview_ui  # noqa: E402

from tests.ui.fixture_data import (  # noqa: E402
    get_clock,
    get_fixture,
    market_to_deck,
    forecast_to_deck,
    session_to_api,
    preview_to_api,
    metrics_to_api,
)

BASE_DIR = WEBUI_DIR
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
TEMPLATES.env.autoescape = True

CHART_NOTICE = "TradingView Lightweight Charts. Copyright (c) 2025 TradingView, Inc."
DEFAULT_WATCHLIST = ["btcusdt", "ethusdt"]
SESSION_SECRET = "test-session-secret-at-least-32-characters-long!!"

# 13 page routes (path → template name)
PAGE_ROUTES: list[tuple[str, str]] = [
    ("/", "index.html"),
    ("/dashboard", "index.html"),
    ("/alerts", "alerts.html"),
    ("/journal", "journal.html"),
    ("/predictions", "predictions.html"),
    ("/futures-lab", "futures_lab.html"),
    ("/strategy", "strategy.html"),
    ("/model-management", "models.html"),
    ("/system", "system.html"),
    ("/daily-session", "daily_session.html"),
    ("/trader", "trading_day.html"),
    ("/command-deck", "command_deck.html"),
    ("/login", "login.html"),
    ("/journal/0", "journal.html"),  # journal detail (uses entry_id=0)
]


# ─── Lifespan (no PG/Redis) ───────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = None
    app.state.redis_client = None
    app.state.pg_pool = None
    app.state.forecast_worker = None
    yield


# ─── App factory ─────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(title="WORED UI Test Fixture", version="11.0.0", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=SESSION_SECRET,
        session_cookie="wored_webui_session",
        same_site="lax",
        https_only=False,
    )
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    _register_routes(app)
    return app


# ─── Template helpers ─────────────────────────────────────────────────────────

def _is_authenticated(request: Request) -> bool:
    return bool(request.session.get("authenticated"))


def _ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(24)
        request.session["csrf_token"] = token
    return token


def _build_context(request: Request, **extra: Any) -> dict[str, Any]:
    context = {
        "request": request,
        "watchlist": DEFAULT_WATCHLIST,
        "default_symbol": DEFAULT_WATCHLIST[0],
        "chart_notice": CHART_NOTICE,
        "auth_enabled": True,
        "authenticated": _is_authenticated(request),
        "admin_username": "fixture-admin",
        "csrf_token": _ensure_csrf_token(request),
        "flash": None,
        "current_path": request.url.path,
        "can_admin": _is_authenticated(request),
    }
    context.update(extra)
    return context


def _template_response(request: Request, name: str, **extra: Any) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request=request,
        name=name,
        context=_build_context(request, **extra),
    )


def _require_page_auth(request: Request) -> RedirectResponse | None:
    if not _is_authenticated(request):
        return RedirectResponse(
            url=f"/login?next={request.url.path}", status_code=303
        )
    return None


def _require_api_auth(request: Request) -> JSONResponse | None:
    if not _is_authenticated(request):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return None


# ─── Route registration ───────────────────────────────────────────────────────

def _register_routes(app: FastAPI) -> None:

    # ── QA health endpoint ─────────────────────────────────────────────────────
    @app.get("/__qa__/health")
    async def qa_health(request: Request):
        nonce = secrets.token_urlsafe(8)
        return {
            "status": "ok",
            "clock": get_clock(),
            "nonce": nonce,
            "authenticated": _is_authenticated(request),
        }

    @app.get("/healthz")
    async def healthz():
        return {"alive": True}

    @app.get("/readyz")
    async def readyz():
        return {"ready": True, "clock": get_clock()}

    # ── Login ──────────────────────────────────────────────────────────────────
    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, next: str = "/"):
        return _template_response(
            request, "login.html",
            page_title="Web UI Login",
            next_target=next or "/",
        )

    @app.post("/login")
    async def login_action(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        next: str = Form("/"),
        csrf_token: str = Form(""),
    ):
        expected = request.session.get("csrf_token")
        if not expected or csrf_token != expected:
            return RedirectResponse(url="/login", status_code=303)
        # Test fixture: accept any non-empty username/password
        if username and password:
            request.session.clear()
            request.session["authenticated"] = True
            request.session["auth_type"] = "password"
            request.session["username"] = username
            _ensure_csrf_token(request)
            return RedirectResponse(url=next or "/", status_code=303)
        return RedirectResponse(url="/login", status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse(url="/login", status_code=303)

    # ── Page routes ────────────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    @app.get("/dashboard", response_class=HTMLResponse)
    async def index(request: Request):
        r = _require_page_auth(request)
        if r is not None:
            return r
        return _template_response(request, "index.html", page_title="WORED Control Room")

    @app.get("/alerts", response_class=HTMLResponse)
    async def alerts_page(request: Request):
        r = _require_page_auth(request)
        if r is not None:
            return r
        return _template_response(
            request, "alerts.html",
            page_title="Alert History",
            alerts=[],
            selected_symbol="",
            counts={"open": 0, "acknowledged": 0, "total": 0},
            page=1,
            has_next_page=False,
        )

    @app.get("/journal", response_class=HTMLResponse)
    async def journal_page(request: Request):
        r = _require_page_auth(request)
        if r is not None:
            return r
        return _template_response(
            request, "journal.html",
            page_title="AI Journal",
            entries=[],
            selected_entry=None,
            selected_symbol="",
            page=1,
            has_next_page=False,
            snapshot_pretty="—",
            indicators_pretty="—",
        )

    @app.get("/journal/{entry_id}", response_class=HTMLResponse)
    async def journal_detail(request: Request, entry_id: int):
        r = _require_page_auth(request)
        if r is not None:
            return r
        return _template_response(
            request, "journal.html",
            page_title=f"AI Journal Entry #{entry_id}",
            entries=[],
            selected_entry=None,
            selected_symbol="",
            page=1,
            has_next_page=False,
            snapshot_pretty="—",
            indicators_pretty="—",
        )

    @app.get("/predictions", response_class=HTMLResponse)
    async def predictions_page(request: Request):
        r = _require_page_auth(request)
        if r is not None:
            return r
        fx = get_fixture("steps_48")
        fc = fx.get("forecast", {})
        return _template_response(
            request, "predictions.html",
            page_title="Prediction Lab",
            prediction_requests=[
                {"id": fc.get("request_id", 9001), "symbol": "btcusdt",
                 "horizon_hours": 48, "base_timeframe": "15min",
                 "status": "completed", "created_at": fc.get("as_of"),
                 "base_price": 64250.5, "completed_models": 3, "failed_models": 0,
                 "avg_accuracy": None, "avg_failure": None,
                 "evaluated_points": 0, "total_points": 48},
            ],
            selected_request={
                "id": fc.get("request_id", 9001), "symbol": "btcusdt",
                "horizon_hours": 48, "base_timeframe": "15min",
                "status": "completed", "created_at": fc.get("as_of"),
                "base_price": 64250.5, "comparison_rows": [],
                "points": fc.get("points", []),
                "completed_models": 3, "failed_models": 0,
                "models": [], "comparison_models": [], "top_model": None,
                "avg_accuracy": None, "avg_failure": None,
                "evaluated_points": 0, "total_points": 48,
            },
            model_statuses=[
                {"key": "bull", "name": "Bull Model",
                 "model_id": "fixture-bull-v1", "tier": "standard",
                 "available": True, "reason": ""},
                {"key": "bear", "name": "Bear Model",
                 "model_id": "fixture-bear-v1", "tier": "standard",
                 "available": True, "reason": ""},
                {"key": "arbiter", "name": "Arbiter Model",
                 "model_id": "fixture-arbiter-v1", "tier": "premium",
                 "available": False, "reason": "OLLAMA_API_KEY is not set"},
            ],
            prediction_horizons=list(range(1, 49)),
            historical_candles=[],
            live_candles=fc.get("points", []),
            base_ts=0,
            page=1,
            has_next_page=False,
        )

    @app.get("/futures-lab", response_class=HTMLResponse)
    async def futures_lab_page(request: Request):
        r = _require_page_auth(request)
        if r is not None:
            return r
        return _template_response(request, "futures_lab.html", page_title="Futures Lab")

    @app.get("/strategy", response_class=HTMLResponse)
    async def strategy_page(request: Request):
        r = _require_page_auth(request)
        if r is not None:
            return r
        return _template_response(request, "strategy.html", page_title="Strategy")

    @app.get("/model-management", response_class=HTMLResponse)
    async def model_management_page(request: Request):
        r = _require_page_auth(request)
        if r is not None:
            return r
        return _template_response(request, "models.html", page_title="Model Management")

    @app.get("/system", response_class=HTMLResponse)
    async def system_page(request: Request):
        r = _require_page_auth(request)
        if r is not None:
            return r
        return _template_response(request, "system.html", page_title="Состояние системы")

    @app.get("/daily-session", response_class=HTMLResponse)
    async def daily_session_page(request: Request):
        r = _require_page_auth(request)
        if r is not None:
            return r
        return _template_response(request, "daily_session.html", page_title="Daily Session")

    @app.get("/command-deck", response_class=HTMLResponse)
    async def command_deck_page(request: Request):
        r = _require_page_auth(request)
        if r is not None:
            return r
        return _template_response(request, "command_deck.html", page_title="Панель управления")

    # ── API endpoints ───────────────────────────────────────────────────────────
    @app.get("/api/health")
    async def api_health(request: Request):
        return {
            "postgres": False,
            "redis": False,
            "collector_feed": False,
            "forecast_worker": False,
            "all_ok": False,
            "as_of": get_clock(),
        }

    @app.get("/api/command-deck")
    async def api_command_deck(request: Request):
        auth = _require_api_auth(request)
        if auth:
            return auth
        fx = get_fixture("ready")
        market = market_to_deck(fx)
        consensus = forecast_to_deck(fx)
        session = session_to_api(fx)
        deck = present_deck_ui(
            market, consensus, [], session.get("session") or {},
            metrics_to_api(fx), {"postgres": False, "redis": False, "collector_feed": False},
        )
        return {"market": market, "consensus": consensus,
                "positions": [], "session": session.get("session"),
                "ui": deck}

    @app.get("/api/trade/preview")
    async def api_trade_preview(request: Request, direction: str = "long",
                                leverage: int = 100, margin: float = 10.0,
                                symbol: str = "btcusdt"):
        auth = _require_api_auth(request)
        if auth:
            return auth
        fx = get_fixture("preview_valid")
        preview = preview_to_api(fx)
        ui = present_preview_ui({
            "allowed": preview["allowed"],
            "reasons": preview.get("reasons", []),
            "expires_at": fx.get("preview", {}).get("expires_at"),
            "calculation_version": fx.get("preview", {}).get("calculation_version", 3),
            "taker_fee": preview["taker_fee"],
            "estimated_exit_fee": preview.get("estimated_exit_fee", 0),
            "funding_assumption": preview.get("funding_assumption", ""),
            "scenarios": {k: {"net_pnl": v, "liquidation_crossed": False}
                           for k, v in preview.get("scenarios", {}).items()},
        }, price_as_of=get_clock())
        return {**preview, "ui": ui.get("ui", {})}

    @app.post("/api/positions/open")
    async def api_open_position(request: Request):
        auth = _require_api_auth(request)
        if auth:
            return auth
        body = await request.json()
        return {"ok": True, "id": 9999, "direction": body.get("direction", "long"),
                "entry_price": 64250.5, "size": 0.01556, "notional": 1000}

    @app.post("/api/positions/{position_id}/close")
    async def api_close_position(request: Request, position_id: int):
        auth = _require_api_auth(request)
        if auth:
            return auth
        return {"ok": True, "id": position_id, "realized_pnl": 0.75}

    @app.get("/api/daily-session/active")
    async def api_daily_session_active(request: Request):
        auth = _require_api_auth(request)
        if auth:
            return auth
        return session_to_api(get_fixture("ready"))

    @app.post("/api/daily-session/revision")
    async def api_daily_session_revision(request: Request):
        auth = _require_api_auth(request)
        if auth:
            return auth
        body = await request.json()
        return {"ok": True, "new_status": "UPDATED",
                "command": body.get("command", "pause")}

    @app.get("/api/forecast/{request_id}/status")
    async def api_forecast_status(request: Request, request_id: int):
        auth = _require_api_auth(request)
        if auth:
            return auth
        fx = get_fixture("ready")
        fc = fx.get("forecast", {})
        return {
            "id": request_id,
            "status": "completed",
            "execution_state": fc.get("execution_state", "completed"),
            "evaluation_state": fc.get("evaluation_state", "pending"),
            "failure_code": fc.get("failure_code"),
            "as_of": fc.get("as_of"),
            "valid_until": fc.get("valid_until"),
            "deadline_at": fc.get("deadline_at"),
        }

    @app.get("/api/alerts")
    async def api_alerts(request: Request):
        auth = _require_api_auth(request)
        if auth:
            return auth
        return {"alerts": [], "counts": {"open": 0, "acknowledged": 0, "total": 0}}

    @app.get("/api/journal")
    async def api_journal(request: Request):
        auth = _require_api_auth(request)
        if auth:
            return auth
        return {"entries": [], "page": 1, "has_next_page": False}

    @app.get("/api/overview")
    async def api_overview(request: Request):
        auth = _require_api_auth(request)
        if auth:
            return auth
        return {"watchlist": [], "health": {"postgres": False, "redis": False},
                "alerts": [], "available_symbols": DEFAULT_WATCHLIST,
                "default_symbol": DEFAULT_WATCHLIST[0]}

    @app.get("/api/strategy/metrics")
    async def api_strategy_metrics(request: Request):
        auth = _require_api_auth(request)
        if auth:
            return auth
        return metrics_to_api(get_fixture("ready"))

    @app.get("/api/candles")
    async def api_candles(request: Request, symbol: str = "btcusdt",
                          period: str = "15min", size: int = 100):
        auth = _require_api_auth(request)
        if auth:
            return auth
        fx = get_fixture("ready")
        return {"candles": fx.get("history", [])}

    @app.get("/api/tickers")
    async def api_tickers(request: Request):
        return {"data": {}}

    @app.post("/api/predictions")
    async def api_create_prediction(request: Request):
        auth = _require_api_auth(request)
        if auth:
            return auth
        await request.json()
        return JSONResponse(
            {"request_id": 9001, "status": "queued",
             "execution_state": "queued"},
            status_code=202,
        )

    @app.get("/api/predictions/{request_id}")
    async def api_prediction_detail(request: Request, request_id: int):
        auth = _require_api_auth(request)
        if auth:
            return auth
        fx = get_fixture("ready")
        fc = fx.get("forecast", {})
        return {"id": request_id, "symbol": "btcusdt",
                "execution_state": fc.get("execution_state", "completed"),
                "points": fc.get("points", [])}

    @app.post("/admin/actions/journal-snapshot")
    async def admin_journal_snapshot(request: Request):
        r = _require_page_auth(request)
        if r is not None:
            return r
        return RedirectResponse(url="/system", status_code=303)

    @app.post("/admin/actions/clear-acknowledged-alerts")
    async def admin_clear_alerts(request: Request):
        r = _require_page_auth(request)
        if r is not None:
            return r
        return RedirectResponse(url="/system", status_code=303)


# Singleton for import
app = create_app()

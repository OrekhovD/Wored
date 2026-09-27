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
    ("/trading-day", "trading_day.html"),
    ("/workspace", "workspace.html"),
    ("/command-deck", "command_deck.html"),
    ("/results", "results.html"),
    ("/learning", "learning.html"),
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

    @app.get("/trading-day", response_class=HTMLResponse)
    async def trading_day_page(request: Request):
        r = _require_page_auth(request)
        if r is not None:
            return r
        return _template_response(request, "trading_day.html", page_title="Сегодня")

    @app.get("/workspace", response_class=HTMLResponse)
    async def workspace_page(request: Request):
        r = _require_page_auth(request)
        if r is not None:
            return r
        return _template_response(request, "workspace.html", page_title="Рабочая область")

    @app.get("/api/workspace/state")
    async def api_workspace_state(request: Request):
        auth = _require_api_auth(request)
        if auth:
            return auth
        return _workspace_state_fixture()

    @app.get("/api/results")
    async def api_results_list(request: Request):
        auth = _require_api_auth(request)
        if auth:
            return auth
        return {"ok": True, "days": _RESULTS_DAYS_FIXTURE, "source": "paper_trading"}

    @app.get("/api/results/{day_id}")
    async def api_result_detail(request: Request, day_id: str):
        auth = _require_api_auth(request)
        if auth:
            return auth
        return {"ok": True, "report": {"day_id": day_id, "state": "closed",
                "accounts": [{"account_label": "manual",
                             "summary": {"net_pnl": "10.70", "trades_count": 3},
                             "reconciliation": {"balanced": True, "mismatches": []},
                             "closed_positions": []}]}}

    @app.get("/trader", include_in_schema=False)
    async def trader_page():
        # Mirror production: /trader redirects to the V2 workspace (F08 Phase 4a).
        return RedirectResponse(url="/workspace", status_code=307)

    # ── Итоги / Обучение: read-only pages (mirror webui/app.py) ────────────────
    _RESULTS_DAYS_FIXTURE = [
        {"day_id": "day-aaa-001", "date": "2026-09-20", "state": "closed",
         "strategy_version": "baseline_v1", "trades": 3, "wins": 2, "losses": 1,
         "net_pnl": "12.50", "fees": "1.80"},
        {"day_id": "day-bbb-002", "date": "2026-09-19", "state": "closed",
         "strategy_version": "baseline_v1", "trades": 0, "wins": 0, "losses": 0,
         "net_pnl": "0", "fees": "0"},
    ]

    @app.get("/results", response_class=HTMLResponse)
    async def results_page(request: Request):
        r = _require_page_auth(request)
        if r is not None:
            return r
        return _template_response(
            request, "results.html", page_title="Итоги",
            days=_RESULTS_DAYS_FIXTURE, source_error=None,
        )

    @app.get("/results/{day_id}", response_class=HTMLResponse)
    async def result_detail_page(request: Request, day_id: str):
        r = _require_page_auth(request)
        if r is not None:
            return r
        day_data = {
            "day_id": day_id, "state": "closed",
            "start_utc": "2026-09-20T00:00:00+00:00",
            "end_utc": "2026-09-20T12:00:00+00:00",
            "strategy_version": "baseline_v1", "settings_snapshot": {},
            "accounts": [{
                "day_id": day_id, "account_id": "acc-manual-1",
                "account_label": "manual",
                "summary": {"opening_capital": "1000", "ending_equity": "1010.70",
                           "realized_pnl": "12.50", "total_fees": "1.80",
                           "net_pnl": "10.70", "unrealized_pnl": "0", "return_pct": "1.07"},
                "stats": {"trades_count": 3, "wins": 2, "losses": 1, "win_rate_pct": "66.67"},
                "open_positions": [], "open_position_count": 0,
                "reconciliation": {"balanced": True, "mismatches": []},
                "closed_positions": [
                    {"id": "pos-1", "side": "long", "instrument": "BTC-USDT",
                     "qty": "1", "entry": "60000", "exit": "61000",
                     "net_pnl": "950", "fees": "72", "closed_at": "2026-09-20T08:00:00Z"},
                ],
            }],
        }
        return _template_response(
            request, "results_detail.html", page_title="Итоги",
            day=day_data, day_id=day_id, report_unavailable=False, source_error=None,
        )

    @app.get("/learning", response_class=HTMLResponse)
    async def learning_page(request: Request):
        r = _require_page_auth(request)
        if r is not None:
            return r
        return _template_response(
            request, "learning.html", page_title="Обучение",
            candidates=[], source_error=None,
        )

    @app.get("/learning/{candidate_id}", response_class=HTMLResponse)
    async def learning_detail_page(request: Request, candidate_id: str):
        r = _require_page_auth(request)
        if r is not None:
            return r
        return _template_response(
            request, "learning_detail.html", page_title="Обучение",
            candidate=None, candidate_id=candidate_id, source_error=None,
        )

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

    # ── Trading-day: the single action centre API (mirrors webui/paper_api) ────
    def _trading_day_payload() -> dict[str, Any]:
        account = lambda kind: {  # noqa: E731
            "id": f"proto-{kind}", "kind": kind, "currency": "USDT",
            "cash": "1000", "opening_equity": "1000", "available_margin": "1000",
            "equity": "1000", "realized_net": "0", "unrealized": "0",
            "total_costs": "0", "loss_budget": "50", "open_positions": 0,
            "closed_trades": 0,
        }
        return {
            "ui_schema_version": 1, "mode": "prototype", "market_mode": "demo",
            "storage_mode": "memory", "server_time": get_clock(), "day": None,
            "accounts": [account("manual"), account("auto")],
            "risk_policy": {"max_daily_loss_usdt": "50", "max_risk_per_order_usdt": "10",
                            "max_total_exposure": "500", "max_leverage": 10},
            "fresh": False,
            "capabilities": {"can_start": True, "reason_code": None},
            "reason_code": "Тестовая цена; реальные perpetual-данные не подключены",
            "next_action": "start", "end_time_local": "21:00",
            "timezone": "Asia/Bangkok", "auto_state": "observing",
            "auto_state_label": "Ожидает запуска дня",
            "positions": [], "closed_positions": [], "orders": [], "events": [],
        }

    def _workspace_state_fixture() -> dict[str, Any]:
        """Deterministic /workspace BFF response for UI tests (read-only, no day)."""
        return {
            "schema_version": 2,
            "as_of": get_clock(),
            "stage": "prepare",
            "day": None,
            "accounts": [
                {"kind": "manual", "currency": "USDT", "cash": "1000",
                 "equity": "1000", "open_positions": 0, "realized_net": "0",
                 "object_ref": {"kind": "account", "id": "ws-manual", "source": "paper_trading"}},
                {"kind": "auto", "currency": "USDT", "cash": "1000",
                 "equity": "1000", "open_positions": 0, "realized_net": "0",
                 "object_ref": {"kind": "account", "id": "ws-auto", "source": "paper_trading"}},
            ],
            "market": {"quality": "demo", "bid": "64250", "ask": "64260", "mark": "64255"},
            "objects": [],
            "attention": [
                {"severity": "info", "priority": 5, "reason_code": "ready_to_start",
                 "message": "День не начат. Готов к запуску.",
                 "source": "paper_trading", "primary_action": "start_day",
                 "object_ref": None, "next_check_at": None},
            ],
            "capabilities": {"can_start": True, "can_trade": False,
                            "can_finish": False, "commands_enabled": True,
                            "can_enter": False},
            "sources": [{"source_name": "paper_trading", "status": "ok"}],
            "status_bar": {
                "stage": "prepare", "day_date": "—",
                "market_quality": "demo", "accounts": [
                    {"kind": "manual", "equity": "1000", "open_positions": 0},
                    {"kind": "auto", "equity": "1000", "open_positions": 0},
                ],
                "pending_commands": 0,
            },
            "automation_state": None,
            "trader_mode": "trade",
            "next_action": "start",
            "commands_enabled": True,
        }

    @app.get("/api/trading-day/current")
    async def api_trading_day_current(request: Request):
        auth = _require_api_auth(request)
        if auth:
            return auth
        return _trading_day_payload()

    @app.post("/api/trading-day/settings")
    async def api_trading_day_settings(request: Request):
        auth = _require_api_auth(request)
        if auth:
            return auth
        await request.json()
        return {"ok": True, "settings": {"end_time_local": "21:00", "timezone": "Asia/Bangkok"}}

    @app.post("/api/trading-day/start")
    async def api_trading_day_start(request: Request):
        auth = _require_api_auth(request)
        if auth:
            return auth
        await request.json()
        return JSONResponse({"command_id": "fixture-start", "status": "accepted"}, status_code=202)

    @app.post("/api/trading-day/{day_id}/finish")
    async def api_trading_day_finish(request: Request, day_id: str):
        auth = _require_api_auth(request)
        if auth:
            return auth
        await request.json()
        return JSONResponse({"command_id": "fixture-finish", "status": "accepted"}, status_code=202)

    @app.post("/api/trading-day/{day_id}/automation")
    async def api_trading_day_automation(request: Request, day_id: str):
        auth = _require_api_auth(request)
        if auth:
            return auth
        body = await request.json()
        return JSONResponse({"command_id": "fixture-auto", "status": "accepted",
                             "action": body.get("action", "pause")}, status_code=202)

    @app.post("/api/paper/accounts/{account_id}/orders/preview")
    async def api_paper_preview(request: Request, account_id: str):
        auth = _require_api_auth(request)
        if auth:
            return auth
        await request.json()
        return {
            "allowed": True, "reasons": [], "account_label": "Ручной счёт",
            "quantity": "0.02", "notional": "1285", "reserved_margin": "128.5",
            "entry_fee": "0.771", "estimated_exit_fee": "0.771", "funding_status": "unknown",
            "funding_rate": None, "break_even": "64251.5", "estimated_net_at_tp": "23.2",
            "estimated_net_at_sl": "-10", "liquidation_price": "57825",
            "liquidation_quality": "simplified", "price_as_of": get_clock(),
            "entry_price": "64250.5", "side": "buy", "order_type": "market",
            "leverage": 10, "market": {"mode": "demo", "contract_code": "BTC-USDT"},
        }

    @app.post("/api/paper/accounts/{account_id}/orders")
    async def api_paper_order(request: Request, account_id: str):
        auth = _require_api_auth(request)
        if auth:
            return auth
        await request.json()
        return JSONResponse({"command_id": "fixture-order", "status": "accepted"}, status_code=202)

    @app.post("/api/paper/positions/{position_id}/actions")
    async def api_paper_position_action(request: Request, position_id: str):
        auth = _require_api_auth(request)
        if auth:
            return auth
        await request.json()
        return JSONResponse({"command_id": "fixture-close", "status": "accepted",
                             "action": "close"}, status_code=202)

    @app.get("/api/paper/commands/{command_id}")
    async def api_paper_command_status(request: Request, command_id: str):
        auth = _require_api_auth(request)
        if auth:
            return auth
        return {"command_id": command_id, "status": "completed", "result": {}}

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
        return {"symbol": symbol, "period": period, "source": "fixture",
                "candles": fx.get("history", [])[:size]}

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

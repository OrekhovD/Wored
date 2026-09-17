"""API contract tests for the Trader API — WORED Trader V0.1 Phase 5.

Tests cover:
  - GET /api/trader/candles returns 200 with candle array
  - GET /api/trader/state returns 200 with mode field
  - GET /api/trader/positions returns 200 with array
  - POST /api/trader/mode returns 200 with idempotency_key
  - POST /api/trader/mode duplicate key returns 200 with applied=false
  - Auth required (401 without session)
  - /trader route returns 200 (or 303 redirect to login)

Uses FastAPI TestClient with mock data (no Redis/Postgres needed).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

# Ensure webui is importable
WEBUI_DIR = Path(__file__).resolve().parents[2] / "webui"
if str(WEBUI_DIR) not in sys.path:
    sys.path.insert(0, str(WEBUI_DIR))

from trader_api import router as trader_router  # noqa: E402

BASE_DIR = WEBUI_DIR
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))
TEMPLATES.env.autoescape = True

SESSION_SECRET = "trader-test-session-secret-at-least-32-chars!!"


@pytest.fixture
def app() -> FastAPI:
    """Test app with trader router, session middleware, login + /trader page route."""
    application = FastAPI(title="Trader API Test")
    application.state.redis_client = None
    application.state.pg_pool = None
    application.state.http_client = None

    application.add_middleware(
        SessionMiddleware,
        secret_key=SESSION_SECRET,
        session_cookie="wored_webui_session",
        same_site="lax",
        https_only=False,
    )

    application.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
    application.include_router(trader_router)

    @application.get("/test-login")
    async def test_login(request: Request):
        """Test-only route: sets authenticated=True in the session."""
        request.session["authenticated"] = True
        request.session["auth_type"] = "password"
        request.session["username"] = "test-admin"
        return {"ok": True}

    @application.get("/trader", response_class=HTMLResponse)
    async def trader_page(request: Request):
        if not request.session.get("authenticated"):
            return RedirectResponse(url="/login?next=/trader", status_code=303)
        return TEMPLATES.TemplateResponse(
            request=request,
            name="trader.html",
            context={
                "request": request,
                "page_title": "Trader Deck",
                "watchlist": ["btcusdt", "ethusdt"],
                "default_symbol": "btcusdt",
                "chart_notice": "TradingView Lightweight Charts",
                "auth_enabled": True,
                "authenticated": True,
                "admin_username": "test-admin",
                "csrf_token": "test-csrf",
                "flash": None,
                "current_path": "/trader",
                "can_admin": True,
            },
        )

    return application


@pytest.fixture
def client(app: FastAPI):
    """Synchronous TestClient (unauthenticated by default)."""
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture
def auth_client(client):
    """TestClient with an authenticated session (via /test-login)."""
    client.get("/test-login")
    return client


class TestTraderCandles:
    """GET /api/trader/candles"""

    def test_returns_200_with_candle_array(self, auth_client):
        resp = auth_client.get("/api/trader/candles")
        assert resp.status_code == 200
        data = resp.json()
        assert "candles" in data
        assert isinstance(data["candles"], list)
        assert len(data["candles"]) > 0
        candle = data["candles"][0]
        assert "time" in candle
        assert "open" in candle
        assert "high" in candle
        assert "low" in candle
        assert "close" in candle
        assert "volume" in candle

    def test_unauthenticated_returns_401(self, client):
        resp = client.get("/api/trader/candles")
        assert resp.status_code == 401


class TestTraderState:
    """GET /api/trader/state"""

    def test_returns_200_with_mode_field(self, auth_client):
        resp = auth_client.get("/api/trader/state")
        assert resp.status_code == 200
        data = resp.json()
        assert "mode" in data
        assert data["mode"] in ("trade", "reduce_only", "pause")

    def test_unauthenticated_returns_401(self, client):
        resp = client.get("/api/trader/state")
        assert resp.status_code == 401


class TestTraderPositions:
    """GET /api/trader/positions"""

    def test_returns_200_with_array(self, auth_client):
        resp = auth_client.get("/api/trader/positions")
        assert resp.status_code == 200
        data = resp.json()
        assert "positions" in data
        assert isinstance(data["positions"], list)

    def test_unauthenticated_returns_401(self, client):
        resp = client.get("/api/trader/positions")
        assert resp.status_code == 401


class TestTraderMode:
    """POST /api/trader/mode"""

    def test_returns_200_with_idempotency_key(self, auth_client):
        resp = auth_client.post(
            "/api/trader/mode",
            json={"mode": "reduce_only", "idempotency_key": "test-key-1", "reason": "test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["applied"] is True
        assert data["idempotency_key"] == "test-key-1"
        assert data["mode"] == "reduce_only"

    def test_duplicate_key_returns_applied_false(self, auth_client):
        # First request
        resp1 = auth_client.post(
            "/api/trader/mode",
            json={"mode": "pause", "idempotency_key": "dup-key-1", "reason": "test pause"},
        )
        assert resp1.status_code == 200
        assert resp1.json()["applied"] is True

        # Duplicate key — same request
        resp2 = auth_client.post(
            "/api/trader/mode",
            json={"mode": "pause", "idempotency_key": "dup-key-1", "reason": "test pause"},
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["applied"] is False
        assert data2["idempotency_key"] == "dup-key-1"

    def test_invalid_mode_returns_422(self, auth_client):
        resp = auth_client.post(
            "/api/trader/mode",
            json={"mode": "invalid_mode", "idempotency_key": "test-key-2"},
        )
        assert resp.status_code == 422

    def test_unauthenticated_returns_401(self, client):
        resp = client.post(
            "/api/trader/mode",
            json={"mode": "trade", "idempotency_key": "test-key-3"},
        )
        assert resp.status_code == 401


class TestTraderForecast:
    """GET /api/trader/forecast"""

    def test_returns_200_with_forecast(self, auth_client):
        resp = auth_client.get("/api/trader/forecast")
        assert resp.status_code == 200
        data = resp.json()
        assert "steps" in data
        assert isinstance(data["steps"], list)

    def test_unauthenticated_returns_401(self, client):
        resp = client.get("/api/trader/forecast")
        assert resp.status_code == 401


class TestTraderActivity:
    """GET /api/trader/activity"""

    def test_returns_200_with_items(self, auth_client):
        resp = auth_client.get("/api/trader/activity")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert isinstance(data["items"], list)


class TestTraderStream:
    """GET /api/trader/stream — SSE placeholder"""

    def test_returns_501(self, auth_client):
        resp = auth_client.get("/api/trader/stream")
        assert resp.status_code == 501


class TestTraderPage:
    """GET /trader — page route"""

    def test_unauthenticated_redirects_to_login(self, client):
        resp = client.get("/trader", follow_redirects=False)
        assert resp.status_code in (303, 307)
        assert "/login" in resp.headers.get("location", "")

    def test_authenticated_returns_200(self, auth_client):
        resp = auth_client.get("/trader", follow_redirects=False)
        assert resp.status_code == 200
        assert "Trader" in resp.text or "trader" in resp.text
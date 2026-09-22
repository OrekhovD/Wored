"""API contract tests for the Trader API — WORED Trader V0.1 Phase 5.

Contract under test (self-learn review, item A): every Trader endpoint serves
real upstream data only.  When the upstream is missing or fails, the response is
an honest empty with ``source="unavailable"`` — never a fabricated candle series,
price path or position table.

Tests cover:
  - GET /api/trader/candles returns HTX candles, and empty on upstream failure
  - GET /api/trader/state returns 200 with mode field, labelled "skeleton"
  - GET /api/trader/positions returns 200 with array (empty without DB)
  - GET /api/trader/forecast + /activity return empty without data
  - forecast band aggregation is role-aware and reports bundle coverage (review M7)
  - POST /api/trader/mode returns 200 with idempotency_key
  - POST /api/trader/mode duplicate key returns 200 with applied=false
  - Auth required (401 without session)
  - /trader route returns 200 (or 303 redirect to login)

Uses FastAPI TestClient with stub state (no Redis/Postgres needed).
"""
from __future__ import annotations

import sys
from datetime import datetime
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
import trader_api  # noqa: E402

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


class _StubResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _StubHttpClient:
    """Minimal stand-in for app.state.http_client (HTX spot REST)."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    async def get(self, url, params=None):
        self.calls.append((url, params))
        return _StubResponse(self._payload)


def _htx_klines(closes):
    """HTX returns newest bar first."""
    return {
        "status": "ok",
        "data": [
            {"id": 1760000000 + i, "open": c, "high": c + 1, "low": c - 1,
             "close": c, "vol": 10.0, "amount": 10.0}
            for i, c in enumerate(reversed(closes))
        ],
    }


class TestTraderCandles:
    """GET /api/trader/candles — real upstream only, never fabricated."""

    def test_returns_real_htx_candles(self, auth_client, app):
        closes = [111.0, 222.0, 333.0, 444.0]
        app.state.http_client = _StubHttpClient(_htx_klines(closes))
        resp = auth_client.get("/api/trader/candles")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "htx-rest"
        assert [c["close"] for c in data["candles"]] == closes  # oldest → newest
        candle = data["candles"][0]
        for key in ("time", "open", "high", "low", "close", "volume"):
            assert key in candle

    def test_no_upstream_returns_empty_not_mock(self, auth_client):
        resp = auth_client.get("/api/trader/candles")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "unavailable"
        assert data["candles"] == []

    def test_upstream_error_returns_empty_not_mock(self, auth_client, app):
        class _Failing:
            async def get(self, url, params=None):
                raise RuntimeError("HTX down")

        app.state.http_client = _Failing()
        resp = auth_client.get("/api/trader/candles")
        assert resp.status_code == 200
        assert resp.json()["source"] == "unavailable"
        assert resp.json()["candles"] == []

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

    def test_no_db_marks_state_as_skeleton(self, auth_client):
        """Roster/budget layout placeholders must not claim to be live state."""
        data = auth_client.get("/api/trader/state").json()
        assert data["source"] == "skeleton"

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

    def test_no_db_returns_empty_not_mock(self, auth_client):
        """Trading state is never synthesized — an empty table is honest."""
        resp = auth_client.get("/api/trader/positions")
        data = resp.json()
        assert data["source"] == "unavailable"
        assert data["positions"] == []
        assert data["count"] == 0

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

    def test_no_forecast_in_db_returns_empty_not_mock(self, auth_client):
        data = auth_client.get("/api/trader/forecast").json()
        assert data["source"] == "unavailable"
        assert data["steps"] == []
        assert data["horizon_steps"] == 0

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

    def test_no_events_returns_empty_not_mock(self, auth_client):
        data = auth_client.get("/api/trader/activity").json()
        assert data["source"] == "unavailable"
        assert data["items"] == []


def _point(step, run_id, role, model, price, high, low, change_pct, confidence=70.0):
    """One row of the joined forecast_points/forecast_model_runs query."""
    return {
        "step_index": step,
        "target_time": datetime(2026, 9, 22, 12, 0),
        "predicted_price": price,
        "predicted_high": high,
        "predicted_low": low,
        "confidence": confidence,
        "predicted_change_pct": change_pct,
        "run_id": run_id,
        "agent_role": role,
        "model_id": model,
    }


class _StubConnection:
    def __init__(self, request_row, points):
        self._request_row = request_row
        self._points = points

    async def fetchrow(self, query, *args):
        return self._request_row

    async def fetch(self, query, *args):
        return self._points

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _StubPool:
    def __init__(self, request_row, points):
        self._connection = _StubConnection(request_row, points)

    def acquire(self):
        return self._connection


class TestForecastRoleAggregation:
    """Role-aware band aggregation — review M7.

    The q10/q50/q90 band is drawn from whatever points exist, so the API has to
    say how many independent voices are behind it.
    """

    def test_missing_arbiter_is_reported_not_hidden(self):
        votes = trader_api._role_votes([
            _point(1, 10, "bull", "deepseek-v4-pro", 111000, 112000, 110000, 1.2),
            _point(1, 20, "bear", "glm-5.2", 110000, 111500, 109500, -0.4),
        ])
        steps, coverage = trader_api.aggregate_forecast_steps(votes)
        assert coverage["band_basis"] == "two-roles"
        assert coverage["roles_missing"] == ["arbiter"]
        assert coverage["roles"]["arbiter"]["present"] is False
        assert steps[0]["samples"] == 2

    def test_retry_of_a_role_counts_as_one_voice(self):
        votes = trader_api._role_votes([
            _point(1, 10, "bull", "deepseek-v4-pro", 111000, 112000, 110000, 1.2),
            _point(1, 14, "bull", "deepseek-v4-pro", 120000, 121000, 119000, 9.0),
            _point(1, 20, "bear", "glm-5.2", 110000, 111500, 109500, -0.4),
        ])
        steps, coverage = trader_api.aggregate_forecast_steps(votes)
        # The newest bull run wins, and one voice replayed is still one voice:
        # the band sits between 110000 (bear) and 120000 (bull retry), not between
        # three samples where the bull would be quoted twice.
        assert steps[0]["samples"] == 2
        assert coverage["max_samples_per_step"] == 2
        assert steps[0]["close"] == 115000.0
        assert steps[0]["c10"] == 110000.0
        assert steps[0]["c90"] == 120000.0

    def test_single_voice_gets_no_invented_certainty(self):
        votes = trader_api._role_votes([
            _point(1, 10, "bull", "deepseek-v4-pro", 111000, 112000, 110000, 1.2),
        ])
        steps, coverage = trader_api.aggregate_forecast_steps(votes)
        assert coverage["band_basis"] == "single-role"
        # One opinion has no spread: sigma is absent rather than fabricated.
        assert steps[0]["sigma"] is None
        assert steps[0]["c10"] == steps[0]["c90"]

    def test_three_roles_on_two_models_are_not_three_opinions(self):
        # Production request #139: bull and arbiter both answered glm-5.1.
        votes = trader_api._role_votes([
            _point(1, 30, "bull", "glm-5.1", 111000, 112000, 110000, 1.2),
            _point(1, 40, "bear", "glm-5.2", 110000, 111500, 109500, -0.4),
            _point(1, 50, "arbiter", "glm-5.1", 110500, 111800, 109800, 0.3),
        ])
        steps, coverage = trader_api.aggregate_forecast_steps(votes)
        assert coverage["band_basis"] == "three-roles"
        assert coverage["min_models_per_step"] == 2

    def test_legacy_points_without_roles_stay_separate_samples(self):
        votes = trader_api._role_votes([
            _point(1, 60, None, "kimi-k2.6", 111000, 112000, 110000, 1.2),
            _point(1, 61, None, "glm-5.2", 110000, 111500, 109500, -0.4),
        ])
        steps, coverage = trader_api.aggregate_forecast_steps(votes)
        assert coverage["band_basis"] == "legacy-no-roles"
        assert coverage["neutral_steps"] == 1
        assert steps[0]["samples"] == 2

    def test_null_confidence_does_not_erase_the_forecast(self):
        # forecast_points.confidence is nullable (14 of 855 production rows); a
        # bare float(None) used to blow up the whole endpoint into "unavailable".
        votes = trader_api._role_votes([
            _point(1, 70, "bull", "glm-5.2", 111000, 112000, 110000, 1.2, confidence=None),
            _point(1, 71, "bear", "glm-5.2", 110000, 111500, 109500, -0.4, confidence=None),
        ])
        steps, _coverage = trader_api.aggregate_forecast_steps(votes)
        assert steps[0]["confidence"] is None
        assert steps[0]["close"] == 110500.0


class TestTraderForecastEndpointCoverage:
    """GET /api/trader/forecast must carry provenance through to the deck."""

    def test_response_exposes_execution_state_and_coverage(self, auth_client, app):
        app.state.pg_pool = _StubPool(
            {
                "id": 137,
                "symbol": "btcusdt",
                "base_price": 110000,
                "horizon_hours": 4,
                "base_timeframe": "60min",
                "created_at": datetime(2026, 9, 21, 10, 0),
                "execution_state": "partial",
            },
            [
                _point(1, 10, "bull", "deepseek-v4-pro", 111000, 112000, 110000, 1.2),
                _point(1, 20, "bear", "glm-5.2", 110000, 111500, 109500, -0.4),
            ],
        )
        data = auth_client.get("/api/trader/forecast").json()
        assert data["source"] == "postgres"
        assert data["execution_state"] == "partial"
        assert data["coverage"]["roles_missing"] == ["arbiter"]
        step = data["steps"][0]
        for key in ("step", "time", "open", "close", "high", "low",
                    "c10", "c90", "h90", "l10", "vol", "p_up", "sigma", "samples"):
            assert key in step


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

    def test_page_exposes_provenance_badge(self, auth_client):
        """Item A: the deck must show where the candles come from (self-learn review)."""
        resp = auth_client.get("/trader")
        assert resp.status_code == 200
        assert 'id="trSourceBadge"' in resp.text
        assert "ui/trader-chart.js" in resp.text

    def test_page_exposes_bundle_coverage_badge(self, auth_client):
        """Review M7: the forecast card has to show how many roles answered it."""
        resp = auth_client.get("/trader")
        assert resp.status_code == 200
        assert 'id="trFcCoverage"' in resp.text
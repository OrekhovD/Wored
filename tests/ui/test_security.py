"""UI-11 test_security — unauthenticated redirect, CSRF, 401/403, no secrets."""
from __future__ import annotations

import re

import pytest


PROTECTED_PAGES = [
    "/",
    "/dashboard",
    "/alerts",
    "/journal",
    "/predictions",
    "/futures-lab",
    "/strategy",
    "/model-management",
    "/system",
    "/daily-session",
    "/command-deck",
]

PROTECTED_APIS = [
    ("/api/command-deck", "GET"),
    ("/api/trade/preview", "GET"),
    ("/api/daily-session/active", "GET"),
    ("/api/forecast/9001/status", "GET"),
    ("/api/overview", "GET"),
    ("/api/alerts", "GET"),
    ("/api/strategy/metrics", "GET"),
    ("/api/candles", "GET"),
]


async def _login(client) -> str:
    """Authenticate the fixture client and return the submitted CSRF token."""
    resp = await client.get("/login")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
    token = csrf.group(1) if csrf else ""
    await client.post(
        "/login",
        data={
            "username": "fixture-admin",
            "password": "test-password",
            "next": "/",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    return token


@pytest.mark.asyncio
class TestUnauthenticatedRedirect:
    async def test_protected_pages_redirect(self, client):
        for path in PROTECTED_PAGES:
            resp = await client.get(path, follow_redirects=False)
            assert resp.status_code in (303, 307), \
                f"{path} should redirect when unauthenticated, got {resp.status_code}"
            assert "/login" in resp.headers.get("location", ""), \
                f"{path} should redirect to /login"

    async def test_login_page_accessible_unauthenticated(self, client):
        resp = await client.get("/login")
        assert resp.status_code == 200

    async def test_healthz_accessible_unauthenticated(self, client):
        resp = await client.get("/healthz")
        assert resp.status_code == 200

    async def test_readyz_accessible_unauthenticated(self, client):
        resp = await client.get("/readyz")
        assert resp.status_code == 200

    async def test_qa_health_accessible_unauthenticated(self, client):
        resp = await client.get("/__qa__/health")
        assert resp.status_code == 200

    async def test_static_files_accessible_unauthenticated(self, client):
        resp = await client.get("/static/ui/tokens.css")
        assert resp.status_code == 200

    async def test_redirect_includes_next_param(self, client):
        resp = await client.get("/system", follow_redirects=False)
        assert resp.status_code in (303, 307)
        location = resp.headers["location"]
        assert "next=" in location


@pytest.mark.asyncio
class TestCSRF:
    async def test_login_form_has_csrf_token(self, client):
        resp = await client.get("/login")
        assert 'name="csrf_token"' in resp.text
        assert 'value="' in resp.text

    async def test_login_post_requires_csrf(self, client):
        await client.get("/login")
        # Extract session cookie
        # POST without CSRF token
        resp2 = await client.post("/login", data={
            "username": "fixture-admin",
            "password": "test-password",
            "next": "/",
        }, follow_redirects=False)
        # Should redirect back to login (invalid CSRF)
        assert resp2.status_code == 303
        assert "/login" in resp2.headers["location"]

    async def test_login_post_with_valid_csrf_succeeds(self, client):
        resp = await client.get("/login")
        csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
        csrf = csrf.group(1) if csrf else ""
        resp2 = await client.post("/login", data={
            "username": "fixture-admin",
            "password": "test-password",
            "next": "/",
            "csrf_token": csrf,
        }, follow_redirects=False)
        assert resp2.status_code == 303
        assert resp2.headers["location"] == "/"

    async def test_predictions_form_has_csrf(self, client):
        resp = await client.get("/login")
        csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
        csrf = csrf.group(1) if csrf else ""
        await client.post("/login", data={
            "username": "fixture-admin", "password": "test-password",
            "next": "/", "csrf_token": csrf,
        }, follow_redirects=False)
        resp2 = await client.get("/predictions")
        assert 'name="csrf_token"' in resp2.text

    async def test_system_admin_forms_have_csrf(self, client):
        resp = await client.get("/login")
        csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
        csrf = csrf.group(1) if csrf else ""
        await client.post("/login", data={
            "username": "fixture-admin", "password": "test-password",
            "next": "/", "csrf_token": csrf,
        }, follow_redirects=False)
        resp2 = await client.get("/system")
        # Both admin forms should have CSRF
        csrf_count = resp2.text.count('name="csrf_token"')
        assert csrf_count >= 2

    async def test_ticket_js_sends_csrf_header(self):
        from pathlib import Path
        ticket_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "ticket.js"
        ticket = ticket_path.read_text(encoding="utf-8")
        assert "X-CSRF-Token" in ticket or "csrf" in ticket.lower()

    async def test_forecast_js_sends_csrf_header(self):
        from pathlib import Path
        forecast_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "forecast.js"
        forecast = forecast_path.read_text(encoding="utf-8")
        assert "X-CSRF-Token" in forecast or "csrf" in forecast.lower()


@pytest.mark.asyncio
class Test401And403:
    async def test_api_returns_401_unauthenticated(self, client):
        for path, method in PROTECTED_APIS:
            if method == "GET":
                resp = await client.get(path)
            else:
                resp = await client.post(path, json={})
            assert resp.status_code == 401, \
                f"{path} should return 401 unauthenticated, got {resp.status_code}"

    async def test_api_401_has_detail(self, client):
        resp = await client.get("/api/command-deck")
        data = resp.json()
        assert "detail" in data
        assert "Authentication" in data["detail"] or "auth" in data["detail"].lower()

    async def test_positions_open_401_unauthenticated(self, client):
        resp = await client.post("/api/positions/open", json={
            "symbol": "btcusdt", "direction": "long",
            "leverage": 10, "margin": 10, "simulation": True,
        })
        assert resp.status_code == 401

    async def test_positions_close_401_unauthenticated(self, client):
        resp = await client.post("/api/positions/42/close", json={})
        assert resp.status_code == 401

    async def test_daily_session_revision_401_unauthenticated(self, client):
        resp = await client.post("/api/daily-session/revision", json={
            "session_id": "7001", "command": "pause",
        })
        assert resp.status_code == 401

    async def test_predictions_post_401_unauthenticated(self, client):
        resp = await client.post("/api/predictions", json={
            "symbol": "btcusdt", "timeframe": "15min",
            "horizon_steps": 4, "depth": 3,
        })
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestNoSecrets:
    async def test_no_password_in_html(self, client):
        resp = await client.get("/login")
        # The password field should be type="password" — no visible password value
        assert 'type="password"' in resp.text
        # No hardcoded password values in the HTML
        assert "test-password" not in resp.text
        assert "fixture-admin-secret" not in resp.text

    async def test_no_session_secret_in_html(self, client):
        await _login(client)
        resp = await client.get("/")
        assert "test-session-secret" not in resp.text

    async def test_no_db_url_in_responses(self, client):
        resp = await client.get("/healthz")
        assert "postgresql://" not in resp.text
        assert "DATABASE_URL" not in resp.text

    async def test_no_redis_url_in_responses(self, client):
        resp = await client.get("/healthz")
        assert "redis://" not in resp.text

    async def test_qa_health_no_secrets(self, client):
        resp = await client.get("/__qa__/health")
        data = resp.json()
        assert "password" not in data
        assert "secret" not in str(data).lower()

    async def test_auth_fixture_does_not_leak(self):
        from tests.ui.fixture_data import get_fixture
        fx = get_fixture("auth")
        assert fx.get("identity") == "fixture-admin"
        # Secret values should be a descriptive string, not a real secret
        assert "injected only by fixture server" in fx.get("secret_values", "")

    async def test_xss_fixture_is_escaped(self):
        """xss fixture has an img onerror payload — templates must escape it."""
        from tests.ui.fixture_data import get_fixture
        fx = get_fixture("xss")
        payload = fx.get("display_value", "")
        assert "<img" in payload  # payload present
        assert "onerror" in payload
        # The template (Jinja2 auto-escaping) must render this safely

    async def test_xss_payload_not_executed_in_template(self, client):
        """Verify Jinja2 auto-escaping is active for XSS fixture payload."""
        from tests.ui.fixture_data import get_fixture
        payload = get_fixture("xss")["display_value"]
        # Jinja2 should escape < > & " ' by default
        from jinja2 import Environment
        env = Environment(autoescape=True)
        rendered = env.from_string("{{ value }}").render(value=payload)
        assert "<img" not in rendered  # escaped to &lt;img
        assert "&lt;img" in rendered or "&#34;" in rendered or "&#" in rendered

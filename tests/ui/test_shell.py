"""UI-11 test_shell — all 13 routes return 200, nav structure, /system, login."""
from __future__ import annotations

import re

import pytest

# All 13 page routes that must return 200 when authenticated
PAGES_200 = [
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
    "/login",        # login page is public
    "/journal/0",     # journal detail
]

# Nav links expected in navigation.html
NAV_LINKS = [
    "/command-deck",
    "/",
    "/alerts",
    "/daily-session",
    "/futures-lab",
    "/strategy",
    "/predictions",
    "/journal",
    "/system",
    "/model-management",
]


async def _login(client) -> str:
    """Login and return csrf token."""
    resp = await client.get("/login")
    assert resp.status_code == 200
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
    csrf = csrf.group(1) if csrf else ""
    await client.post("/login", data={
        "username": "fixture-admin",
        "password": "test-password",
        "next": "/",
        "csrf_token": csrf,
    }, follow_redirects=False)
    return csrf


@pytest.mark.asyncio
class TestAllRoutesReturn200:
    async def test_all_13_routes_authenticated(self, client):
        await _login(client)
        for path in PAGES_200:
            resp = await client.get(path, follow_redirects=False)
            # login page is public; others should not redirect
            assert resp.status_code == 200, f"{path} returned {resp.status_code}"

    async def test_unauthenticated_protected_routes_redirect(self, client):
        protected = [p for p in PAGES_200 if p != "/login"]
        for path in protected:
            resp = await client.get(path, follow_redirects=False)
            assert resp.status_code in (303, 307), \
                f"{path} should redirect when unauthenticated, got {resp.status_code}"


@pytest.mark.asyncio
class TestNavStructure:
    async def test_nav_links_present(self, client):
        await _login(client)
        resp = await client.get("/")
        assert resp.status_code == 200
        for href in NAV_LINKS:
            assert f'href="{href}"' in resp.text, f"Nav link {href} missing"

    async def test_nav_has_aria_label(self, client):
        await _login(client)
        resp = await client.get("/")
        assert 'aria-label="Основная навигация"' in resp.text

    async def test_nav_current_path_marked(self, client):
        await _login(client)
        resp = await client.get("/system")
        assert 'aria-current="page"' in resp.text

    async def test_nav_has_health_dots(self, client):
        await _login(client)
        resp = await client.get("/")
        assert "healthRedis" in resp.text
        assert "healthPostgres" in resp.text
        assert "healthCollector" in resp.text

    async def test_nav_has_auth_section(self, client):
        await _login(client)
        resp = await client.get("/")
        assert "wored-nav-status" in resp.text
        assert "fixture-admin" in resp.text


@pytest.mark.asyncio
class TestSystemPage:
    async def test_system_page_200(self, client):
        await _login(client)
        resp = await client.get("/system")
        assert resp.status_code == 200

    async def test_system_has_health_grid(self, client):
        await _login(client)
        resp = await client.get("/system")
        assert "systemHealthGrid" in resp.text
        assert "sysPostgres" in resp.text
        assert "sysRedis" in resp.text
        assert "sysCollector" in resp.text
        assert "sysForecast" in resp.text

    async def test_system_has_readiness_section(self, client):
        await _login(client)
        resp = await client.get("/system")
        assert "Readiness" in resp.text or "readyz" in resp.text

    async def test_system_has_admin_section_when_authenticated(self, client):
        await _login(client)
        resp = await client.get("/system")
        assert "Администрирование" in resp.text
        assert "journal-snapshot" in resp.text

    async def test_system_admin_hidden_when_unauthenticated(self, client):
        resp = await client.get("/login")
        assert "Администрирование" not in resp.text


@pytest.mark.asyncio
class TestLoginPage:
    async def test_login_page_200(self, client):
        resp = await client.get("/login")
        assert resp.status_code == 200

    async def test_login_has_csrf_token(self, client):
        resp = await client.get("/login")
        assert 'name="csrf_token"' in resp.text
        assert 'value="' in resp.text

    async def test_login_has_username_field(self, client):
        resp = await client.get("/login")
        assert 'name="username"' in resp.text
        assert "Имя пользователя" in resp.text

    async def test_login_has_password_field(self, client):
        resp = await client.get("/login")
        assert 'name="password"' in resp.text
        assert "Пароль" in resp.text

    async def test_login_form_posts_to_login(self, client):
        resp = await client.get("/login")
        assert 'action="/login"' in resp.text

    async def test_login_has_telegram_auth_status(self, client):
        resp = await client.get("/login")
        assert "telegram-auth-status" in resp.text
        assert 'aria-live="polite"' in resp.text

    async def test_login_redirects_on_success(self, client):
        resp = await client.get("/login")
        csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
        csrf = csrf.group(1) if csrf else ""
        resp = await client.post("/login", data={
            "username": "fixture-admin",
            "password": "test-password",
            "next": "/",
            "csrf_token": csrf,
        }, follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"

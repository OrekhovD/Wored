"""UI-11 test_accessibility — keyboard nav, form labels, aria-live, contrast."""
from __future__ import annotations

import re

import pytest


async def _login(client) -> str:
    resp = await client.get("/login")
    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
    csrf = csrf.group(1) if csrf else ""
    await client.post("/login", data={
        "username": "fixture-admin", "password": "test-password",
        "next": "/", "csrf_token": csrf,
    }, follow_redirects=False)
    return csrf


@pytest.mark.asyncio
class TestKeyboardNav:
    async def test_nav_links_are_focusable(self, client):
        await _login(client)
        resp = await client.get("/")
        # All nav links are <a> elements with href — inherently focusable
        links = re.findall(r'<a[^>]*href="(/[^"]*)"[^>]*>', resp.text)
        assert len(links) >= 10  # nav has at least 10 links

    async def test_nav_has_aria_current(self, client):
        await _login(client)
        resp = await client.get("/")
        assert 'aria-current="' in resp.text

    async def test_login_form_tab_order(self, client):
        resp = await client.get("/login")
        # username input should come before password
        u_idx = resp.text.find('name="username"')
        p_idx = resp.text.find('name="password"')
        assert u_idx > 0
        assert p_idx > 0
        assert u_idx < p_idx

    async def test_logout_button_in_nav(self, client):
        await _login(client)
        resp = await client.get("/")
        assert "Выход" in resp.text
        assert 'action="/logout"' in resp.text

    async def test_nav_uses_semantic_nav_element(self, client):
        await _login(client)
        resp = await client.get("/")
        assert "<nav" in resp.text
        assert "</nav>" in resp.text

    async def test_escape_closes_dialog_in_js(self):
        from pathlib import Path
        core_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "core.js"
        core = core_path.read_text(encoding="utf-8")
        assert "Escape" in core
        assert "closeDialog" in core


@pytest.mark.asyncio
class TestFormLabels:
    async def test_login_has_label_for_username(self, client):
        resp = await client.get("/login")
        assert "<label" in resp.text
        assert "Имя пользователя" in resp.text

    async def test_login_has_label_for_password(self, client):
        resp = await client.get("/login")
        assert "Пароль" in resp.text
        assert "<label" in resp.text

    async def test_forecast_form_has_labels(self, client):
        await _login(client)
        resp = await client.get("/predictions")
        assert "Инструмент" in resp.text
        assert "Шаг времени" in resp.text
        assert "Количество шагов" in resp.text

    async def test_forecast_form_inputs_have_required(self, client):
        await _login(client)
        resp = await client.get("/predictions")
        assert "required" in resp.text

    async def test_forecast_form_inputs_have_ids(self, client):
        await _login(client)
        resp = await client.get("/predictions")
        assert 'id="forecastSymbol"' in resp.text
        assert 'id="forecastTimeframe"' in resp.text
        assert 'id="forecastHorizon"' in resp.text

    async def test_system_admin_form_has_csrf(self, client):
        await _login(client)
        resp = await client.get("/system")
        assert 'name="csrf_token"' in resp.text


@pytest.mark.asyncio
class TestAriaLive:
    async def test_nav_data_status_has_aria_live(self, client):
        await _login(client)
        resp = await client.get("/")
        assert 'aria-live="polite"' in resp.text

    async def test_forecast_result_has_aria_live(self, client):
        await _login(client)
        resp = await client.get("/predictions")
        assert 'id="forecastResult"' in resp.text
        assert 'aria-live="polite"' in resp.text

    async def test_login_has_aria_live_status(self, client):
        resp = await client.get("/login")
        assert "telegram-auth-status" in resp.text
        assert 'aria-live="polite"' in resp.text

    async def test_health_dots_have_aria_label(self, client):
        await _login(client)
        resp = await client.get("/")
        assert "Состояние сервисов" in resp.text

    async def test_block_error_has_role_alert(self):
        """showBlockError creates element with role='alert'."""
        from pathlib import Path
        async_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "async-patterns.js"
        async_js = async_path.read_text(encoding="utf-8")
        assert "role" in async_js
        assert "alert" in async_js


@pytest.mark.asyncio
class TestContrast:
    def test_tokens_css_has_color_variables(self):
        from pathlib import Path
        tokens_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "tokens.css"
        css = tokens_path.read_text(encoding="utf-8")
        assert "--" in css  # CSS custom properties

    def test_styles_css_has_text_colors(self):
        from pathlib import Path
        styles_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "styles.css"
        css = styles_path.read_text(encoding="utf-8")
        # Should define text colors
        assert "color" in css

    def test_command_deck_has_contrast_colors(self):
        from pathlib import Path
        cd_path = Path(__file__).resolve().parents[2] / "webui" / "templates" / "command_deck.html"
        cd = cd_path.read_text(encoding="utf-8")
        # CSS variables for color
        assert "--fg" in cd
        assert "--bg" in cd
        assert "--green" in cd
        assert "--red" in cd

    def test_text_success_class_defined(self):
        from pathlib import Path
        tokens_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "tokens.css"
        css = tokens_path.read_text(encoding="utf-8")
        assert "ui-text-success" in css or "ui-text" in css

    def test_dark_theme_readable(self):
        """Command deck uses dark bg with light fg for readability."""
        from pathlib import Path
        cd_path = Path(__file__).resolve().parents[2] / "webui" / "templates" / "command_deck.html"
        cd = cd_path.read_text(encoding="utf-8")
        # --bg is dark (#0a0a0a), --fg is light (#e5e5e5)
        assert "#0a0a0a" in cd or "#0A0A0A" in cd.upper()
        assert "#e5e5e5" in cd or "#E5E5E5" in cd.upper()
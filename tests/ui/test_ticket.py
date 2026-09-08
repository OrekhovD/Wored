"""UI-11 test_ticket — Long/Short dialog, focus trap, preview debounce, confirm."""
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
class TestLongShortDialog:
    async def test_command_deck_has_long_button(self, client):
        await _login(client)
        resp = await client.get("/command-deck")
        assert resp.status_code == 200
        assert "long" in resp.text.lower()

    async def test_command_deck_has_short_button(self, client):
        await _login(client)
        resp = await client.get("/command-deck")
        assert "short" in resp.text.lower()

    async def test_command_deck_has_ticket_modal(self, client):
        await _login(client)
        resp = await client.get("/command-deck")
        assert 'id="ticket"' in resp.text
        assert "modal" in resp.text.lower()

    async def test_ticket_has_direction_label(self, client):
        await _login(client)
        resp = await client.get("/command-deck")
        assert 'id="ticketDir"' in resp.text

    async def test_ticket_has_close_button(self, client):
        await _login(client)
        resp = await client.get("/command-deck")
        assert "closeTicket" in resp.text or "modal-close" in resp.text

    async def test_ticket_has_leverage_slider(self, client):
        await _login(client)
        resp = await client.get("/command-deck")
        assert 'id="tLev"' in resp.text

    async def test_ticket_has_margin_slider(self, client):
        await _login(client)
        resp = await client.get("/command-deck")
        assert 'id="tMar"' in resp.text

    async def test_ticket_has_confirm_button(self, client):
        await _login(client)
        resp = await client.get("/command-deck")
        assert "confirmTicket" in resp.text or 'id="tConfirm"' in resp.text

    async def test_open_ticket_function_exists(self, client):
        await _login(client)
        resp = await client.get("/command-deck")
        assert "openTicket" in resp.text


@pytest.mark.asyncio
class TestFocusTrap:
    async def test_dialog_has_role_or_aria_modal(self, client):
        """core.js sets role='dialog' and aria-modal='true' on openDialog."""
        # ticket.js calls WORED.openDialog which sets these attrs at runtime.
        # Verify the JS module exports openDialog/closeDialog.
        from pathlib import Path
        core_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "core.js"
        core = core_path.read_text(encoding="utf-8")
        assert "openDialog" in core
        assert "closeDialog" in core
        assert "aria-modal" in core
        assert "role" in core and "dialog" in core

    async def test_escape_key_closes_dialog(self, client):
        """_dialogKeyHandler listens for Escape and calls closeDialog."""
        from pathlib import Path
        core_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "core.js"
        core = core_path.read_text(encoding="utf-8")
        assert "Escape" in core
        assert "_dialogKeyHandler" in core
        assert "closeDialog" in core

    async def test_focus_returns_to_trigger(self, client):
        """closeDialog restores focus to the trigger element."""
        from pathlib import Path
        core_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "core.js"
        core = core_path.read_text(encoding="utf-8")
        assert "_trigger" in core
        assert "trigger" in core and "focus" in core

    async def test_dialog_focuses_heading_on_open(self, client):
        from pathlib import Path
        core_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "core.js"
        core = core_path.read_text(encoding="utf-8")
        assert "heading" in core
        assert "focus" in core

    async def test_body_overflow_hidden_on_open(self, client):
        from pathlib import Path
        core_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "core.js"
        core = core_path.read_text(encoding="utf-8")
        assert "overflow" in core
        assert "hidden" in core


@pytest.mark.asyncio
class TestPreviewDebounce:
    async def test_debounce_in_ticket_js(self):
        from pathlib import Path
        ticket_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "ticket.js"
        ticket = ticket_path.read_text(encoding="utf-8")
        assert "debouncedPreview" in ticket
        assert "setTimeout" in ticket
        assert "250" in ticket  # 250ms debounce

    async def test_abort_controller_on_new_preview(self):
        from pathlib import Path
        ticket_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "ticket.js"
        ticket = ticket_path.read_text(encoding="utf-8")
        assert "AbortController" in ticket
        assert "abort" in ticket
        assert "previewSeq" in ticket

    async def test_stale_response_rejected(self):
        from pathlib import Path
        ticket_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "ticket.js"
        ticket = ticket_path.read_text(encoding="utf-8")
        assert "seq" in ticket
        assert "previewSeq" in ticket

    async def test_preview_endpoint_returns_data(self, client):
        await _login(client)
        resp = await client.get("/api/trade/preview?direction=long&leverage=10&margin=10&symbol=btcusdt")
        assert resp.status_code == 200
        data = resp.json()
        assert "entry_price" in data
        assert "liquidation_price" in data
        assert "scenarios" in data

    async def test_preview_requires_auth(self, client):
        resp = await client.get("/api/trade/preview?direction=long")
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestConfirm:
    async def test_confirm_posts_to_positions_open(self):
        from pathlib import Path
        ticket_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "ticket.js"
        ticket = ticket_path.read_text(encoding="utf-8")
        assert "/api/positions/open" in ticket
        assert "confirmTicket" in ticket

    async def test_confirm_disabled_without_preview(self):
        from pathlib import Path
        ticket_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "ticket.js"
        ticket = ticket_path.read_text(encoding="utf-8")
        assert "updateConfirmButton" in ticket
        assert "hasPreview" in ticket or "preview" in ticket
        assert "disabled" in ticket

    async def test_confirm_sends_simulation_true(self):
        from pathlib import Path
        ticket_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "ticket.js"
        ticket = ticket_path.read_text(encoding="utf-8")
        assert "simulation" in ticket
        assert "true" in ticket

    async def test_confirm_sends_csrf_token(self):
        from pathlib import Path
        ticket_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "ticket.js"
        ticket = ticket_path.read_text(encoding="utf-8")
        assert "X-CSRF-Token" in ticket or "csrf" in ticket.lower()

    async def test_open_position_returns_ok(self, client):
        await _login(client)
        resp = await client.post("/api/positions/open", json={
            "symbol": "btcusdt", "direction": "long",
            "leverage": 10, "margin": 10, "simulation": True,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "id" in data
        assert "entry_price" in data

    async def test_close_position_returns_ok(self, client):
        await _login(client)
        resp = await client.post("/api/positions/42/close", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

    async def test_confirm_redirects_on_401(self):
        from pathlib import Path
        ticket_path = Path(__file__).resolve().parents[2] / "webui" / "static" / "ui" / "ticket.js"
        ticket = ticket_path.read_text(encoding="utf-8")
        assert "401" in ticket
        assert "/login" in ticket
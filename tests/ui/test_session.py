"""UI-11 test_session — readiness states, execution controls, close_all."""
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
class TestReadinessStates:
    async def test_daily_session_page_200(self, client):
        await _login(client)
        resp = await client.get("/daily-session")
        assert resp.status_code == 200

    async def test_session_page_has_readiness_badge(self, client):
        await _login(client)
        resp = await client.get("/daily-session")
        assert "dsReadinessBadge" in resp.text

    async def test_session_page_has_diag_checks(self, client):
        await _login(client)
        resp = await client.get("/daily-session")
        assert "dsDiagChecks" in resp.text

    async def test_session_page_has_blocked_reason(self, client):
        await _login(client)
        resp = await client.get("/daily-session")
        assert "dsBlockedReason" in resp.text

    def test_ready_fixture_preserves_base_session(self):
        from tests.ui.fixture_data import get_fixture
        fx = get_fixture("ready")
        sess = fx.get("session")
        assert sess is not None
        assert sess["technical_status"] == "ARMED"
        assert sess["readiness"] == "waiting_trigger"

    def test_no_trade_fixture(self):
        from tests.ui.fixture_data import get_fixture
        fx = get_fixture("no_trade")
        sess = fx["session"]
        assert sess["readiness"] == "no_trade_plan"
        assert sess["reason_code"] == "no_valid_entries"
        assert sess["entries"] == []

    def test_conditional_no_trade_fixture(self):
        from tests.ui.fixture_data import get_fixture
        fx = get_fixture("conditional_no_trade")
        sess = fx["session"]
        assert sess["readiness"] == "waiting_trigger"
        assert "notradecondition" in sess
        assert len(sess["entries"]) > 0

    def test_paused_fixture(self):
        from tests.ui.fixture_data import get_fixture
        fx = get_fixture("paused")
        sess = fx["session"]
        assert sess["technical_status"] == "PAUSED"
        assert "continue" in sess["allowed_commands"]
        assert "close_all" in sess["allowed_commands"]

    def test_empty_fixture_nulls_session(self):
        from tests.ui.fixture_data import get_fixture
        fx = get_fixture("empty")
        assert fx["session"] is None
        assert fx["forecast"] is None

    async def test_api_session_returns_ready_state(self, client):
        await _login(client)
        resp = await client.get("/api/daily-session/active")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session"] is not None
        assert data["session"]["technical_status"] == "ARMED"

    async def test_api_session_returns_null_when_empty(self, client):
        from tests.ui.fixture_data import get_fixture, session_to_api
        result = session_to_api(get_fixture("empty"))
        assert result["session"] is None


@pytest.mark.asyncio
class TestExecutionControls:
    async def test_session_page_has_control_buttons(self, client):
        await _login(client)
        resp = await client.get("/daily-session")
        assert "sendCommand" in resp.text
        assert "dsControls" in resp.text

    async def test_session_has_continue_button(self, client):
        await _login(client)
        resp = await client.get("/daily-session")
        assert "continue" in resp.text.lower()

    async def test_session_has_tighten_button(self, client):
        await _login(client)
        resp = await client.get("/daily-session")
        assert "tighten" in resp.text.lower() or "Tighten" in resp.text

    async def test_session_has_reduce_button(self, client):
        await _login(client)
        resp = await client.get("/daily-session")
        assert "reduce" in resp.text.lower() or "Reduce" in resp.text

    async def test_session_has_pause_button(self, client):
        await _login(client)
        resp = await client.get("/daily-session")
        assert "pause" in resp.text.lower() or "Pause" in resp.text

    async def test_session_has_cmd_result_area(self, client):
        await _login(client)
        resp = await client.get("/daily-session")
        assert "dsCmdResult" in resp.text

    async def test_revision_api_returns_ok(self, client):
        await _login(client)
        resp = await client.post("/api/daily-session/revision", json={
            "session_id": "7001", "command": "pause",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "new_status" in data

    async def test_revision_requires_auth(self, client):
        resp = await client.post("/api/daily-session/revision", json={
            "session_id": "7001", "command": "pause",
        })
        assert resp.status_code == 401

    def test_paused_allowed_commands(self):
        from tests.ui.fixture_data import get_fixture
        fx = get_fixture("paused")
        commands = fx["session"]["allowed_commands"]
        assert "continue" in commands
        assert "close_all" in commands
        # paused session should not have pause (already paused)
        assert "pause" not in commands

    def test_armed_allowed_commands(self):
        from tests.ui.fixture_data import get_fixture
        fx = get_fixture("ready")
        commands = fx["session"]["allowed_commands"]
        assert "pause" in commands
        assert "tighten" in commands
        assert "reduce" in commands


@pytest.mark.asyncio
class TestCloseAll:
    async def test_session_page_has_close_all_button(self, client):
        await _login(client)
        resp = await client.get("/daily-session")
        assert "close_all" in resp.text

    async def test_close_all_in_paused_commands(self):
        from tests.ui.fixture_data import get_fixture
        fx = get_fixture("paused")
        assert "close_all" in fx["session"]["allowed_commands"]

    async def test_close_all_not_in_armed_commands(self):
        from tests.ui.fixture_data import get_fixture
        fx = get_fixture("ready")
        assert "close_all" not in fx["session"]["allowed_commands"]

    async def test_revision_close_all_command(self, client):
        await _login(client)
        resp = await client.post("/api/daily-session/revision", json={
            "session_id": "7001", "command": "close_all",
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_close_position_endpoint(self, client):
        await _login(client)
        resp = await client.post("/api/positions/42/close", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "realized_pnl" in data
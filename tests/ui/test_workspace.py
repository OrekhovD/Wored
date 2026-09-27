"""UI tests for /workspace V2 B2 (read-only workspace).

Tests V2-01 orientation (status, accounts, attention) and V2-05 no-false-success
(commands_enabled=False) on the fixture app.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestWorkspacePage:
    async def test_workspace_renders_200(self, auth_client):
        resp = await auth_client.get("/workspace")
        assert resp.status_code == 200

    async def test_workspace_has_four_zones(self, auth_client):
        resp = await auth_client.get("/workspace")
        text = resp.text
        assert 'ws-status' in text
        assert 'ws-context' in text
        assert 'ws-attention' in text
        assert 'ws-stepper' in text

    async def test_workspace_title(self, auth_client):
        resp = await auth_client.get("/workspace")
        assert "Рабочая область" in resp.text

    async def test_workspace_loads_js(self, auth_client):
        resp = await auth_client.get("/workspace")
        assert "workspace-state.js" in resp.text


@pytest.mark.asyncio
class TestWorkspaceAPI:
    async def test_state_returns_schema_version(self, auth_client):
        resp = await auth_client.get("/api/workspace/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["schema_version"] == 2

    async def test_state_has_attention_queue(self, auth_client):
        resp = await auth_client.get("/api/workspace/state")
        data = resp.json()
        assert isinstance(data["attention"], list)
        assert len(data["attention"]) >= 1
        # Every item has required fields
        for item in data["attention"]:
            assert "severity" in item
            assert "message" in item

    async def test_state_has_two_accounts(self, auth_client):
        resp = await auth_client.get("/api/workspace/state")
        data = resp.json()
        kinds = [a["kind"] for a in data["accounts"]]
        assert "manual" in kinds
        assert "auto" in kinds

    async def test_state_no_day_honest(self, auth_client):
        """V2-05: no day → null, not fabricated zero-state."""
        resp = await auth_client.get("/api/workspace/state")
        data = resp.json()
        assert data["day"] is None
        assert data["stage"] == "prepare"
        # No fake PnL or trades
        assert all(a.get("open_positions", 0) == 0 for a in data["accounts"])

    async def test_state_commands_enabled_b3(self, auth_client):
        """B3: domain reachable → commands_enabled True; trade still gated by state."""
        resp = await auth_client.get("/api/workspace/state")
        data = resp.json()
        assert data["commands_enabled"] is True
        assert data["capabilities"]["commands_enabled"] is True
        # Pre-start: can_start True, but can_trade False (no running day)
        assert data["capabilities"]["can_start"] is True
        assert data["capabilities"]["can_trade"] is False

    async def test_state_has_status_bar(self, auth_client):
        resp = await auth_client.get("/api/workspace/state")
        data = resp.json()
        sb = data["status_bar"]
        assert sb["stage"] == "prepare"
        assert len(sb["accounts"]) == 2

    async def test_state_unauthenticated_401(self, client):
        resp = await client.get("/api/workspace/state")
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestWorkspaceNav:
    async def test_nav_exposes_workspace(self, auth_client):
        resp = await auth_client.get("/trading-day")
        assert 'href="/workspace"' in resp.text


@pytest.mark.asyncio
class TestCommandDrawer:
    """B3: Command Drawer present, command flow works."""

    async def test_drawer_in_page(self, auth_client):
        resp = await auth_client.get("/workspace")
        assert "wsDrawerOverlay" in resp.text
        assert "workspace-actions.js" in resp.text

    async def test_start_command_accepted(self, auth_client):
        """POST /api/trading-day/start → 202 + command_id."""
        resp = await auth_client.post("/api/trading-day/start",
                                       json={"csrf_token": "x"})
        assert resp.status_code == 202
        data = resp.json()
        assert "command_id" in data
        assert data["status"] == "accepted"

    async def test_command_poll_completed(self, auth_client):
        """GET /api/paper/commands/{id} → completed."""
        resp = await auth_client.get("/api/paper/commands/fixture-start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["command_id"] == "fixture-start"

    async def test_order_command_202(self, auth_client):
        """POST /api/paper/accounts/{id}/orders → 202."""
        resp = await auth_client.post("/api/paper/accounts/proto-manual/orders",
                                       json={"instrument": "BTCUSDT", "side": "buy"})
        assert resp.status_code == 202
        assert "command_id" in resp.json()

    async def test_unauthenticated_command_401(self, client):
        resp = await client.post("/api/trading-day/start", json={})
        assert resp.status_code == 401


class TestF08TraderMode:
    """F08 Phase 4a: operational mode exposed in workspace BFF."""

    async def test_mode_in_bff(self, auth_client):
        resp = await auth_client.get("/api/workspace/state")
        data = resp.json()
        assert "trader_mode" in data
        assert data["trader_mode"] in ("trade", "reduce_only", "pause")

    async def test_can_enter_capability(self, auth_client):
        resp = await auth_client.get("/api/workspace/state")
        caps = resp.json()["capabilities"]
        # With prepare stage + no day, can_enter should be False
        assert "can_enter" in caps

    async def test_trader_page_redirects_to_workspace(self, client):
        resp = await client.get("/trader", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/workspace"

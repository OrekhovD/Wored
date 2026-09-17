"""Phase 6: Hermes MCP server tests — policy, authorization, audit."""
from __future__ import annotations

import pytest

from agents.mcp_server import TraderMCPServer, MCPResponse
from agents.role_runner import RoleRunner


class TestMCPPolicy:
    def test_no_forbidden_tools_exposed(self):
        server = TraderMCPServer()
        assert server.verify_no_forbidden_tools() is True

    def test_no_create_order_tool(self):
        server = TraderMCPServer()
        tools = server.list_tools()
        names = [t["name"] for t in tools]
        assert "create_order" not in names
        assert "close_position" not in names
        assert "change_limit" not in names

    def test_run_role_disabled_by_default(self):
        server = TraderMCPServer()
        tools = server.list_tools()
        run_role = next((t for t in tools if t["name"] == "run_role"), None)
        assert run_role is None  # Disabled tools are not listed


class TestMCPReadOnly:
    def test_get_hourly_digest(self):
        server = TraderMCPServer()
        r = server.call_tool("get_hourly_digest", {})
        assert r.success is True
        assert r.data["mode"] is not None
        assert r.data["contract"] == "BTC-USDT"

    def test_get_positions(self):
        server = TraderMCPServer()
        r = server.call_tool("get_positions", {"status": "open"})
        assert r.success is True
        assert "positions" in r.data

    def test_get_forecast(self):
        server = TraderMCPServer()
        r = server.call_tool("get_forecast", {})
        assert r.success is True

    def test_get_agent_runs(self):
        server = TraderMCPServer()
        r = server.call_tool("get_agent_runs", {"limit": 10})
        assert r.success is True
        assert "budget" in r.data


class TestMCPWriteTools:
    def test_set_mode_requires_idempotency_key(self):
        server = TraderMCPServer()
        r = server.call_tool("set_mode", {"mode": "pause"})
        assert r.success is False
        assert "idempotency_key" in r.error

    def test_set_mode_success(self):
        server = TraderMCPServer()
        r = server.call_tool("set_mode", {"mode": "pause", "idempotency_key": "key-001"})
        assert r.success is True
        assert r.data["mode"] == "pause"

    def test_set_mode_duplicate_key_no_op(self):
        server = TraderMCPServer()
        server.call_tool("set_mode", {"mode": "pause", "idempotency_key": "key-002"})
        r = server.call_tool("set_mode", {"mode": "trade", "idempotency_key": "key-002"})
        assert r.success is True
        assert r.data["applied"] is False

    def test_set_mode_invalid(self):
        server = TraderMCPServer()
        r = server.call_tool("set_mode", {"mode": "invalid", "idempotency_key": "key-003"})
        assert r.success is False

    def test_submit_trade_plan(self):
        server = TraderMCPServer()
        plan = {"plan_version": "v1", "mode": "trade", "max_entries": 6}
        r = server.call_tool("submit_trade_plan", {"plan": plan, "idempotency_key": "key-004"})
        assert r.success is True
        assert r.data["plan_version"] == "v1"


class TestAuditLog:
    def test_audit_log_records_writes(self):
        server = TraderMCPServer()
        server.call_tool("set_mode", {"mode": "pause", "idempotency_key": "key-a1"})
        log = server.audit_log()
        assert len(log) == 1
        assert log[0]["tool"] == "set_mode"
        assert log[0]["write"] is True

    def test_audit_log_records_reads(self):
        server = TraderMCPServer()
        server.call_tool("get_hourly_digest", {})
        log = server.audit_log()
        assert len(log) == 1
        assert log[0]["write"] is False

    def test_audit_log_excludes_idempotency_keys(self):
        server = TraderMCPServer()
        server.call_tool("set_mode", {"mode": "pause", "idempotency_key": "secret-key"})
        log = server.audit_log()
        assert "idempotency_key" not in log[0]["arguments"]


class TestMCPRunnerIntegration:
    def test_missing_runner_does_not_crash(self):
        """Paper runner and protection plane must be unaffected by missing Hermes."""
        server = TraderMCPServer(role_runner=None)
        # Read tools still work
        r = server.call_tool("get_hourly_digest", {})
        assert r.success is True
        # Write tools still work
        r = server.call_tool("set_mode", {"mode": "trade", "idempotency_key": "key-b1"})
        assert r.success is True
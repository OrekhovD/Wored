"""Hermes MCP server for WORED Trader V0.1.

Provides read-only supervision and limited control:
- get_hourly_digest: summary of current trading state
- get_positions: open/closed positions
- get_forecast: latest forecast run
- get_agent_runs: LLM role execution history
- set_mode: change trading mode (trade/reduce_only/pause)
- submit_trade_plan: submit a new trade plan
- run_role: rate-limited manual role trigger (disabled by default)

No create-order, close-position, or change-limit tools are exposed.
Every write requires an idempotency key and is audited.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

TOOL_DEFINITIONS = [
    {
        "name": "get_hourly_digest",
        "description": "Get hourly trading digest: mode, positions, PnL, reason",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "write": False,
        "enabled": True,
    },
    {
        "name": "get_positions",
        "description": "Get all positions (open and closed)",
        "input_schema": {"type": "object", "properties": {"status": {"type": "string", "enum": ["open", "closed", "all"]}}, "required": []},
        "write": False,
        "enabled": True,
    },
    {
        "name": "get_forecast",
        "description": "Get latest forecast run with candles and evaluation",
        "input_schema": {"type": "object", "properties": {}, "required": []},
        "write": False,
        "enabled": True,
    },
    {
        "name": "get_agent_runs",
        "description": "Get recent LLM agent runs with budget status",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}}, "required": []},
        "write": False,
        "enabled": True,
    },
    {
        "name": "set_mode",
        "description": "Set trading mode. Requires idempotency key.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["trade", "reduce_only", "pause"]},
                "idempotency_key": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["mode", "idempotency_key"],
        },
        "write": True,
        "enabled": True,
    },
    {
        "name": "submit_trade_plan",
        "description": "Submit a validated trade plan. Requires idempotency key.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan": {"type": "object"},
                "idempotency_key": {"type": "string"},
            },
            "required": ["plan", "idempotency_key"],
        },
        "write": True,
        "enabled": True,
    },
    {
        "name": "run_role",
        "description": "Manually trigger an LLM role. Disabled by default.",
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "enum": ["planner", "critic", "coach"]},
                "features": {"type": "object"},
            },
            "required": ["role"],
        },
        "write": True,
        "enabled": False,  # Disabled by default in v0.1
    },
]


@dataclass
class MCPResponse:
    """Response from an MCP tool call."""
    tool: str
    success: bool
    data: dict[str, Any] | None = None
    error: str = ""


class TraderMCPServer:
    """In-process MCP server for Hermes integration."""

    # Tools that are forbidden — never exposed
    FORBIDDEN_TOOLS = [
        "create_order",
        "close_position",
        "change_limit",
        "adjust_margin_direct",
        "reverse_position_direct",
    ]

    def __init__(self, *, repository=None, role_runner=None):
        self.repository = repository  # PaperTradingRepository or stub
        self.role_runner = role_runner
        self._applied_keys: set[str] = set()
        self._audit_log: list[dict[str, Any]] = []

    def list_tools(self) -> list[dict[str, Any]]:
        """Return available tools (only enabled ones)."""
        return [t for t in TOOL_DEFINITIONS if t["enabled"]]

    def verify_no_forbidden_tools(self) -> bool:
        """Verify that no forbidden tools are exposed."""
        exposed_names = {t["name"] for t in self.list_tools()}
        forbidden = set(self.FORBIDDEN_TOOLS)
        return not (exposed_names & forbidden)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> MCPResponse:
        """Execute a tool call with audit logging."""
        # Verify tool exists and is enabled
        tool = next((t for t in TOOL_DEFINITIONS if t["name"] == name and t["enabled"]), None)
        if tool is None:
            return MCPResponse(tool=name, success=False, error=f"tool '{name}' not found or disabled")

        # Verify not forbidden
        if name in self.FORBIDDEN_TOOLS:
            return MCPResponse(tool=name, success=False, error="forbidden tool")

        # Write tools require idempotency key
        if tool["write"]:
            key = arguments.get("idempotency_key")
            if not key:
                return MCPResponse(tool=name, success=False, error="idempotency_key required for write tools")
            if key in self._applied_keys:
                return MCPResponse(tool=name, success=True, data={"applied": False, "reason": "duplicate_idempotency_key"})
            self._applied_keys.add(key)

        # Audit log
        self._audit_log.append({
            "tool": name,
            "arguments": {k: v for k, v in arguments.items() if k != "idempotency_key"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "write": tool["write"],
        })

        # Dispatch
        try:
            if name == "get_hourly_digest":
                return self._get_hourly_digest()
            elif name == "get_positions":
                return self._get_positions(arguments.get("status", "all"))
            elif name == "get_forecast":
                return self._get_forecast()
            elif name == "get_agent_runs":
                return self._get_agent_runs(arguments.get("limit", 20))
            elif name == "set_mode":
                return self._set_mode(arguments["mode"], arguments.get("reason", ""))
            elif name == "submit_trade_plan":
                return self._submit_trade_plan(arguments["plan"])
            elif name == "run_role":
                return self._run_role(arguments.get("role", ""), arguments.get("features", {}))
            else:
                return MCPResponse(tool=name, success=False, error=f"tool '{name}' not implemented")
        except Exception as exc:
            log.exception("MCP tool %s failed", name)
            return MCPResponse(tool=name, success=False, error=str(exc))

    def _get_hourly_digest(self) -> MCPResponse:
        """Hourly digest of trading state."""
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "trade",
            "contract": "BTC-USDT",
            "open_positions": 0,
            "net_pnl_today": "0.00",
            "reason": "warmup",
            "feed_status": "ok",
        }
        if self.repository:
            # Would query real data
            pass
        return MCPResponse(tool="get_hourly_digest", success=True, data=data)

    def _get_positions(self, status: str) -> MCPResponse:
        """Get positions by status."""
        positions: list[dict[str, Any]] = []
        if self.repository:
            # Would query real positions
            pass
        return MCPResponse(tool="get_positions", success=True, data={"positions": positions, "status": status})

    def _get_forecast(self) -> MCPResponse:
        """Get latest forecast."""
        data = {"forecast": None, "message": "no forecast available"}
        if self.repository:
            pass
        return MCPResponse(tool="get_forecast", success=True, data=data)

    def _get_agent_runs(self, limit: int) -> MCPResponse:
        """Get recent agent runs and budget."""
        data = {
            "runs": [],
            "budget": self.role_runner.budget_status() if self.role_runner else {},
            "limit": limit,
        }
        return MCPResponse(tool="get_agent_runs", success=True, data=data)

    def _set_mode(self, mode: str, reason: str) -> MCPResponse:
        """Set trading mode."""
        if mode not in ("trade", "reduce_only", "pause"):
            return MCPResponse(tool="set_mode", success=False, error=f"invalid mode: {mode}")
        return MCPResponse(tool="set_mode", success=True, data={"mode": mode, "reason": reason, "applied": True})

    def _submit_trade_plan(self, plan: dict[str, Any]) -> MCPResponse:
        """Submit a trade plan."""
        if not isinstance(plan, dict):
            return MCPResponse(tool="submit_trade_plan", success=False, error="plan must be an object")
        return MCPResponse(tool="submit_trade_plan", success=True, data={"plan_version": plan.get("plan_version", "unknown"), "applied": True})

    def _run_role(self, role: str, features: dict[str, Any]) -> MCPResponse:
        """Manually trigger a role (disabled by default)."""
        if self.role_runner is None:
            return MCPResponse(tool="run_role", success=False, error="role_runner not configured")
        result = self.role_runner.run_role(role, features)
        return MCPResponse(
            tool="run_role",
            success=not result.used_fallback,
            data={"output": result.output, "used_fallback": result.used_fallback, "reason": result.fallback_reason},
        )

    def audit_log(self) -> list[dict[str, Any]]:
        """Return audit log of all tool calls."""
        return list(self._audit_log)
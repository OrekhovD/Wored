"""Unit tests for workspace_presenters — deterministic display logic."""
from __future__ import annotations

import pytest

from workspace_presenters import (
    build_attention,
    build_capabilities,
    day_stage,
    format_status_bar,
)


class TestDayStage:
    def test_none_maps_to_prepare(self):
        assert day_stage(None) == "prepare"

    def test_running_maps_to_work(self):
        assert day_stage("running") == "work"

    def test_idle_maps_to_prepare(self):
        assert day_stage("idle") == "prepare"

    def test_closing_maps_to_finishing(self):
        assert day_stage("closing") == "finishing"

    def test_settlement_maps_to_finishing(self):
        assert day_stage("settlement_pending") == "finishing"

    def test_closed_maps_to_results(self):
        assert day_stage("closed") == "results"

    def test_recovery_maps_to_blocked(self):
        assert day_stage("recovery_required") == "blocked"

    def test_unknown_state_defaults_to_prepare(self):
        assert day_stage("weird_state") == "prepare"


class TestBuildCapabilities:
    def test_domain_unavailable_blocks_all(self):
        caps = build_capabilities(day=None, ok=False, market_quality="live")
        assert caps["can_start"] is False
        assert caps["can_trade"] is False
        assert caps["reason_code"] == "domain_unavailable"

    def test_no_day_allows_start(self):
        caps = build_capabilities(day=None, ok=True, market_quality="live")
        assert caps["can_start"] is True
        assert caps["can_trade"] is False

    def test_running_allows_trade_when_live(self):
        caps = build_capabilities(day={"state": "running"}, ok=True, market_quality="live")
        assert caps["can_trade"] is True
        assert caps["can_finish"] is True

    def test_running_blocks_trade_when_stale(self):
        caps = build_capabilities(day={"state": "running"}, ok=True, market_quality="stale")
        assert caps["can_trade"] is False

    def test_closed_allows_report(self):
        caps = build_capabilities(day={"state": "closed"}, ok=True, market_quality="live")
        assert caps["can_view_report"] is True
        assert caps["can_start"] is False


class TestBuildAttention:
    def test_domain_error_critical(self):
        items = build_attention({"ok": False})
        assert len(items) == 1
        assert items[0]["severity"] == "critical"
        assert items[0]["reason_code"] == "domain_unavailable"

    def test_market_stale_produces_warning(self):
        items = build_attention({
            "ok": True, "day": {"state": "running"},
            "market": {"quality": "stale", "age_seconds": 18},
            "capabilities": {"can_trade": False},
            "pending_commands": [], "automation_state": None,
        })
        assert any(i["reason_code"] == "market_stale" for i in items)

    def test_no_day_ready_to_start(self):
        items = build_attention({
            "ok": True, "day": None,
            "market": {"quality": "live"},
            "capabilities": {"can_start": True},
            "pending_commands": [], "automation_state": None,
        })
        assert any(i["reason_code"] == "ready_to_start" for i in items)

    def test_waiting_signal_info(self):
        items = build_attention({
            "ok": True, "day": {"state": "running"},
            "market": {"quality": "live"},
            "capabilities": {"can_trade": True},
            "pending_commands": [], "automation_state": "waiting_signal",
        })
        auto_item = next((i for i in items if i["reason_code"] == "auto_waiting_signal"), None)
        assert auto_item is not None
        assert auto_item["severity"] == "info"

    def test_all_clear_when_no_problems(self):
        items = build_attention({
            "ok": True, "day": {"state": "running"},
            "market": {"quality": "live"},
            "capabilities": {"can_trade": True},
            "pending_commands": [], "automation_state": None,
        })
        assert any(i["reason_code"] == "all_clear" for i in items)

    def test_sorted_by_priority(self):
        items = build_attention({
            "ok": True, "day": {"state": "running"},
            "market": {"quality": "stale", "age_seconds": 30},
            "capabilities": {"can_trade": False},
            "pending_commands": [{"command_id": "c1", "action_code": "day.start"}],
            "automation_state": "risk_blocked",
        })
        priorities = [i["priority"] for i in items]
        assert priorities == sorted(priorities)


class TestFormatStatusBar:
    def test_returns_all_expected_keys(self):
        sb = format_status_bar(
            day={"local_date": "2026-09-26"}, accounts=[{"kind": "manual", "cash": "1000"}],
            market={"quality": "live"}, pending_count=0, stage="work",
        )
        assert sb["stage"] == "work"
        assert sb["day_date"] == "2026-09-26"
        assert sb["market_quality"] == "live"
        assert len(sb["accounts"]) == 1
        assert sb["pending_commands"] == 0

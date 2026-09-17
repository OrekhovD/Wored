"""Phase 4: LLM role runner tests — budget, schema, fallback."""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from agents.role_runner import (
    BudgetState,
    RoleRunner,
    RoleResult,
    _features_hash,
    _load_config,
)


class TestBudgetState:
    def test_new_day_resets(self):
        b = BudgetState(date="2020-01-01")
        b.requests_today = 10
        b.tokens_today = 5000
        b.reset_if_new_day()
        assert b.requests_today == 0
        assert b.tokens_today == 0
        assert b.date != "2020-01-01"

    def test_can_spend_respects_role_max(self):
        b = BudgetState(max_requests=48, max_tokens=200000)
        config = {"max_requests_per_day": 12}
        for _ in range(12):
            assert b.can_spend("planner", config)
            b.record("planner", 100, 50)
        # 13th should be blocked
        assert not b.can_spend("planner", config)

    def test_can_spend_respects_global_max(self):
        b = BudgetState(max_requests=3, max_tokens=200000)
        config = {"max_requests_per_day": 100}
        for _ in range(3):
            assert b.can_spend("planner", config)
            b.record("planner", 100, 50)
        assert not b.can_spend("planner", config)

    def test_zero_max_blocks(self):
        b = BudgetState()
        config = {"max_requests_per_day": 0}
        assert not b.can_spend("entry_gate", config)


class TestFeaturesHash:
    def test_same_features_same_hash(self):
        f1 = {"price": "78000", "vol": "100"}
        f2 = {"price": "78000", "vol": "100"}
        assert _features_hash(f1) == _features_hash(f2)

    def test_different_order_same_hash(self):
        f1 = {"a": "1", "b": "2"}
        f2 = {"b": "2", "a": "1"}
        assert _features_hash(f1) == _features_hash(f2)

    def test_different_features_different_hash(self):
        f1 = {"price": "78000"}
        f2 = {"price": "78001"}
        assert _features_hash(f1) != _features_hash(f2)


class TestRoleRunner:
    def test_no_config_returns_fallback(self):
        runner = RoleRunner(config={"roles": {}, "global": {}})
        result = runner.run_role("planner", {"price": "78000"})
        assert result.used_fallback is True
        assert result.fallback_reason == "role_not_configured"

    def test_disabled_role_returns_fallback(self):
        runner = RoleRunner(config={
            "roles": {"entry_gate": {"max_requests_per_day": 0, "model": None}},
            "global": {},
        })
        result = runner.run_role("entry_gate", {})
        assert result.used_fallback is True
        assert result.fallback_reason == "budget_blocked"

    def test_planner_fallback_is_reduce_only(self):
        runner = RoleRunner(config={
            "roles": {"planner": {
                "max_requests_per_day": 12,
                "model": None,
                "fallback": "previous_plan_30min_then_reduce_only",
            }},
            "global": {},
        })
        result = runner.run_role("planner", {"price": "78000"})
        assert result.used_fallback is True
        assert result.output is not None
        assert result.output["mode"] == "reduce_only"

    def test_critic_fallback_multiplier(self):
        runner = RoleRunner(config={
            "roles": {"critic": {
                "max_requests_per_day": 12,
                "model": None,
                "fallback": "confidence_multiplier_0.85",
            }},
            "global": {},
        })
        result = runner.run_role("critic", {"plan": "test"})
        assert result.used_fallback is True
        assert result.output["confidence_multiplier"] == 0.85

    def test_provider_call_success(self):
        """Simulate a successful provider call."""
        def mock_provider(model, features, max_in, max_out):
            return {
                "content": json.dumps({
                    "plan_version": "v1",
                    "mode": "trade",
                    "allowed_sides": ["long", "short"],
                    "profile": "P0",
                    "max_entries": 6,
                    "p_entry_min": 0.65,
                    "valid_until": "2026-09-18T20:00:00Z",
                    "reason": "test plan",
                }),
                "input_tokens": 500,
                "output_tokens": 200,
            }

        runner = RoleRunner(config={
            "roles": {"planner": {
                "max_requests_per_day": 12,
                "model": "deepseek-v4-flash",
                "max_input_tokens": 1500,
                "max_output_tokens": 300,
                "schema": "agents/schemas/trade_plan_v1.json",
                "fallback": "reduce_only",
            }},
            "global": {"feature_reuse_within_ttl": False},
        })
        result = runner.run_role("planner", {"price": "78000"}, provider_call=mock_provider)
        assert result.used_fallback is False
        assert result.output is not None
        assert result.output["mode"] == "trade"
        assert result.input_tokens == 500
        assert result.output_tokens == 200

    def test_provider_malformed_json_fallback(self):
        def bad_provider(model, features, max_in, max_out):
            return {"content": "not json", "input_tokens": 100, "output_tokens": 50}

        runner = RoleRunner(config={
            "roles": {"planner": {
                "max_requests_per_day": 12,
                "model": "deepseek-v4-flash",
                "fallback": "reduce_only",
                "schema": "agents/schemas/trade_plan_v1.json",
            }},
            "global": {"feature_reuse_within_ttl": False},
        })
        result = runner.run_role("planner", {}, provider_call=bad_provider)
        assert result.used_fallback is True
        assert result.output["mode"] == "reduce_only"

    def test_cache_reuse_same_hash(self):
        runner = RoleRunner(config={
            "roles": {"planner": {
                "max_requests_per_day": 12,
                "model": None,
                "fallback": "reduce_only",
            }},
            "global": {"feature_reuse_within_ttl": True},
        })
        features = {"price": "78000", "vol": "100"}
        r1 = runner.run_role("planner", features)
        r2 = runner.run_role("planner", features)
        assert r2.fallback_reason == "reused_cached_result"
        assert r2.output == r1.output

    def test_budget_status(self):
        runner = RoleRunner(config={
            "roles": {},
            "global": {"max_requests_per_day": 48, "max_tokens_per_day": 200000, "target_monthly_cost_usd": 5.0},
        })
        status = runner.budget_status()
        assert status["max_requests"] == 48
        assert status["max_tokens"] == 200000
        assert status["target_monthly_cost_usd"] == 5.0
        assert status["requests_today"] == 0
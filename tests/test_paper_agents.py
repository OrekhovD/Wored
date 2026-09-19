"""Contracts for advisory agents and deterministic trade approval."""
from __future__ import annotations

from decimal import Decimal

import pytest

from webui.paper_agents import (
    AgentContractError,
    agent_roles,
    parse_trade_proposal,
    select_approved_proposal,
)
from webui.paper_store import PAPER_TABLES_SQL


def proposal(role: str, decision: str, *, approved: bool = False, risk: str = "5") -> dict:
    is_long = decision == "long"
    return {
        "agent_role": role,
        "decision": decision,
        "confidence": "0.75",
        "snapshot_id": "snapshot-1",
        "strategy_version": 3,
        "forecast_request_id": "forecast-7",
        "entry_reference": "100" if decision != "skip" else None,
        "stop_price": ("98" if is_long else "102") if decision != "skip" else None,
        "take_profit": ("104" if is_long else "96") if decision != "skip" else None,
        "risk_usdt": risk if decision != "skip" else None,
        "approved": approved,
        "reason_codes": ["trend_confirmed"],
        "summary": "Сценарий подтверждён текущим снимком",
    }


def parse(payload: dict):
    return parse_trade_proposal(
        payload,
        expected_snapshot_id="snapshot-1",
        expected_strategy_version=3,
    )


def test_roles_use_active_cheap_model_slots_and_no_agent_activates_rules() -> None:
    roles = agent_roles({
        "OLLAMA_WORKER_MODEL": "deepseek-v4-flash",
        "OLLAMA_ANALYST_MODEL": "deepseek-v4-pro",
        "OLLAMA_PREMIUM_MODEL": "glm-5.2",
        "OLLAMA_ORACLE_MODEL": "minimax-m3",
    })
    models = {model for role in roles for model in role.model_candidates}
    assert {"deepseek-v4-flash", "deepseek-v4-pro", "glm-5.2", "minimax-m3"} <= models
    assert all(role.can_activate_rules is False for role in roles)


def test_agent_proposal_must_match_snapshot_and_strategy_version() -> None:
    payload = proposal("bull_analyst", "long")
    payload["snapshot_id"] = "old-snapshot"
    with pytest.raises(AgentContractError, match="snapshot_id"):
        parse(payload)


def test_non_arbiter_agent_cannot_approve_risk() -> None:
    with pytest.raises(AgentContractError, match="only risk_arbiter"):
        parse(proposal("bull_analyst", "long", approved=True))


def test_consensus_requires_directional_support_and_risk_limit() -> None:
    bull = parse(proposal("bull_analyst", "long"))
    arbiter = parse(proposal("risk_arbiter", "long", approved=True))
    approved = select_approved_proposal([bull, arbiter], max_risk_usdt=Decimal("10"))
    assert approved["decision"] == "long"
    assert approved["reason_code"] == "approved"

    blocked = select_approved_proposal([bull, arbiter], max_risk_usdt=Decimal("2"))
    assert blocked == {"decision": "skip", "reason_code": "risk_limit_exceeded"}


def test_arbiter_without_matching_analyst_is_skip() -> None:
    bear = parse(proposal("bear_analyst", "short"))
    arbiter = parse(proposal("risk_arbiter", "long", approved=True))
    result = select_approved_proposal([bear, arbiter], max_risk_usdt=Decimal("10"))
    assert result == {"decision": "skip", "reason_code": "direction_not_supported"}


def test_persistent_schema_has_agent_strategy_and_learning_tables() -> None:
    assert "CREATE TABLE IF NOT EXISTS paper_agent_runs" in PAPER_TABLES_SQL
    assert "CREATE TABLE IF NOT EXISTS paper_strategy_versions" in PAPER_TABLES_SQL
    assert "CREATE TABLE IF NOT EXISTS paper_learning_reviews" in PAPER_TABLES_SQL
    assert "WHERE status = 'active'" in PAPER_TABLES_SQL

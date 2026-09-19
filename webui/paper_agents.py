"""Contracts for the multi-agent paper-trading decision pipeline.

Agents are advisory. They can propose a direction and explain evidence, but
they cannot mutate balances, create fills or activate strategy rules. The
deterministic execution service remains the only authority for those actions.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


class AgentContractError(ValueError):
    """An agent response cannot enter the deterministic decision pipeline."""


@dataclass(frozen=True)
class AgentRole:
    key: str
    purpose: str
    model_candidates: tuple[str, ...]
    can_propose_trade: bool
    can_approve_risk: bool
    can_activate_rules: bool = False


@dataclass(frozen=True)
class TradeProposal:
    agent_role: str
    decision: str
    confidence: Decimal
    snapshot_id: str
    strategy_version: int
    forecast_request_id: str | None
    entry_reference: Decimal | None
    stop_price: Decimal | None
    take_profit: Decimal | None
    risk_usdt: Decimal | None
    approved: bool
    reason_codes: tuple[str, ...]
    summary: str

    def public_dict(self) -> dict[str, Any]:
        return {
            key: (format(value, "f") if isinstance(value, Decimal) else value)
            for key, value in asdict(self).items()
        }


def agent_roles(env: Mapping[str, str] | None = None) -> tuple[AgentRole, ...]:
    values = env or os.environ
    worker = values.get("OLLAMA_WORKER_MODEL", "deepseek-v4-flash")
    analyst = values.get("OLLAMA_ANALYST_MODEL", "deepseek-v4-pro")
    premium = values.get("OLLAMA_PREMIUM_MODEL", "glm-5.2")
    oracle = values.get("OLLAMA_ORACLE_MODEL", "minimax-m3")
    return (
        AgentRole(
            "signal_worker",
            "Нормализует рынок и признаки; не выбирает сделку",
            (worker,),
            False,
            False,
        ),
        AgentRole(
            "bull_analyst",
            "Ищет только подтверждённый Long-сценарий",
            (analyst, premium),
            True,
            False,
        ),
        AgentRole(
            "bear_analyst",
            "Ищет только подтверждённый Short-сценарий",
            (analyst, premium),
            True,
            False,
        ),
        AgentRole(
            "risk_arbiter",
            "Выбирает trade или skip и объясняет риск",
            (oracle, premium),
            True,
            True,
        ),
        AgentRole(
            "daily_learner",
            "Создаёт кандидаты правил из закрытых прогнозов и сделок",
            (premium, analyst),
            False,
            False,
        ),
        AgentRole(
            "promotion_auditor",
            "Детерминированно проверяет replay, out-of-sample и риск",
            (),
            False,
            False,
        ),
    )


def _decimal(value: Any, field: str, *, allow_zero: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AgentContractError(f"{field}: invalid decimal") from exc
    if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
        raise AgentContractError(f"{field}: must be positive")
    return result


def parse_trade_proposal(
    payload: Mapping[str, Any],
    *,
    expected_snapshot_id: str,
    expected_strategy_version: int,
) -> TradeProposal:
    role = str(payload.get("agent_role", ""))
    allowed_roles = {item.key: item for item in agent_roles()}
    role_spec = allowed_roles.get(role)
    if role_spec is None or not role_spec.can_propose_trade:
        raise AgentContractError("agent_role: role cannot propose a trade")

    decision = str(payload.get("decision", "")).lower()
    if decision not in {"long", "short", "skip"}:
        raise AgentContractError("decision: expected long, short or skip")
    snapshot_id = str(payload.get("snapshot_id", ""))
    if snapshot_id != expected_snapshot_id:
        raise AgentContractError("snapshot_id: stale or unrelated market evidence")
    try:
        strategy_version = int(payload.get("strategy_version"))
    except (TypeError, ValueError) as exc:
        raise AgentContractError("strategy_version: invalid integer") from exc
    if strategy_version != expected_strategy_version:
        raise AgentContractError("strategy_version: proposal used another rule set")

    confidence = _decimal(payload.get("confidence"), "confidence", allow_zero=True)
    if confidence > 1:
        raise AgentContractError("confidence: expected value from 0 to 1")
    approved = bool(payload.get("approved", False))
    if approved and not role_spec.can_approve_risk:
        raise AgentContractError("approved: only risk_arbiter may approve")

    entry = stop = target = risk = None
    if decision != "skip":
        entry = _decimal(payload.get("entry_reference"), "entry_reference")
        stop = _decimal(payload.get("stop_price"), "stop_price")
        target = _decimal(payload.get("take_profit"), "take_profit")
        risk = _decimal(payload.get("risk_usdt"), "risk_usdt")
        if decision == "long" and not (stop < entry < target):
            raise AgentContractError("prices: Long requires stop < entry < target")
        if decision == "short" and not (target < entry < stop):
            raise AgentContractError("prices: Short requires target < entry < stop")

    reason_codes_raw = payload.get("reason_codes")
    if not isinstance(reason_codes_raw, list) or not reason_codes_raw:
        raise AgentContractError("reason_codes: at least one evidence code is required")
    reason_codes = tuple(str(item) for item in reason_codes_raw if str(item).strip())
    if not reason_codes:
        raise AgentContractError("reason_codes: at least one evidence code is required")
    summary = str(payload.get("summary", "")).strip()
    if not summary or len(summary) > 500:
        raise AgentContractError("summary: required and limited to 500 characters")

    return TradeProposal(
        agent_role=role,
        decision=decision,
        confidence=confidence,
        snapshot_id=snapshot_id,
        strategy_version=strategy_version,
        forecast_request_id=(
            str(payload["forecast_request_id"])
            if payload.get("forecast_request_id") is not None
            else None
        ),
        entry_reference=entry,
        stop_price=stop,
        take_profit=target,
        risk_usdt=risk,
        approved=approved,
        reason_codes=reason_codes,
        summary=summary,
    )


def select_approved_proposal(
    proposals: list[TradeProposal],
    *,
    max_risk_usdt: Decimal,
    minimum_confidence: Decimal = Decimal("0.6"),
) -> dict[str, Any]:
    arbiters = [item for item in proposals if item.agent_role == "risk_arbiter"]
    if len(arbiters) != 1:
        return {"decision": "skip", "reason_code": "arbiter_missing_or_duplicated"}
    arbiter = arbiters[0]
    if arbiter.decision == "skip" or not arbiter.approved:
        return {"decision": "skip", "reason_code": "arbiter_rejected"}
    if arbiter.confidence < minimum_confidence:
        return {"decision": "skip", "reason_code": "confidence_below_threshold"}
    if arbiter.risk_usdt is None or arbiter.risk_usdt > max_risk_usdt:
        return {"decision": "skip", "reason_code": "risk_limit_exceeded"}

    supporting_role = "bull_analyst" if arbiter.decision == "long" else "bear_analyst"
    support = [
        item for item in proposals
        if item.agent_role == supporting_role
        and item.decision == arbiter.decision
        and item.snapshot_id == arbiter.snapshot_id
        and item.strategy_version == arbiter.strategy_version
        and item.confidence >= minimum_confidence
    ]
    if not support:
        return {"decision": "skip", "reason_code": "direction_not_supported"}
    return {
        "decision": arbiter.decision,
        "reason_code": "approved",
        "proposal": arbiter.public_dict(),
        "supporting_agent": max(support, key=lambda item: item.confidence).agent_role,
    }

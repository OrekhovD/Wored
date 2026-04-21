"""Pydantic schemas shared across all modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None


@dataclass
class CostEstimate:
    total_cost: float
    currency: str = "USD"
    input_cost: float = 0.0
    output_cost: float = 0.0
    per_token_costs: dict[str, float] = field(default_factory=dict)


@dataclass
class HealthResult:
    provider_id: str
    healthy: bool
    latency_ms: float
    error_message: str | None = None


@dataclass
class QuotaCheckResult:
    allowed: bool
    remaining_pct: float
    warning_triggered: bool
    critical_triggered: bool
    hard_stop: bool


@dataclass
class UsageRecord:
    request_id: str
    conversation_id: str | None
    telegram_user_id: int
    provider_id: str
    model_id: str
    input_tokens: int
    output_tokens: int
    total_tokens: int | None
    reasoning_tokens: int | None
    cached_tokens: int | None
    latency_ms: int
    status: str
    cost_estimate: float
    error_code: str | None
    quota_scope: str | None
    warning_triggered: bool
    fallback_triggered: bool
    context_handoff_triggered: bool
    uncertain_usage: bool
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class AIRequest:
    model: str
    messages: list[dict[str, str]]
    max_tokens: int | None = None
    temperature: float = 1.0
    stream: bool = False


@dataclass
class AIResponse:
    content: str
    model: str
    provider: str
    usage: dict[str, int] | None = None
    finish_reason: str | None = None


@dataclass
class RouteDecision:
    request_type: str
    mode: str
    current_model: str
    candidate_chain: list[str]
    selected_model: str
    handoff_required: bool
    handoff_summary_version: str
    reason_current_model_excluded: str | None = None


@dataclass
class HandoffPackage:
    version: str
    system_rules: str
    handoff_summary: str
    last_user_request: str
    delta_market_update: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ContextSnapshot:
    session_id: str
    version: str
    summary_text: str
    last_market_facts: str
    active_mode: str
    active_model: str
    token_budget_state: str
    compression_method: str = "none"
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class HTXTicker:
    symbol: str
    last: float
    volume: float
    change_pct: float
    high: float
    low: float
    timestamp: int


@dataclass
class HTXCandle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class HTXOrderBook:
    bid: list[tuple[float, float]]
    ask: list[tuple[float, float]]
    timestamp: int


@dataclass
class HTXRecentTrade:
    trade_id: int
    price: float
    quantity: float
    direction: str
    timestamp: int


@dataclass
class HealthStatus:
    service: str
    healthy: bool
    details: str | None = None
    checked_at: datetime = field(default_factory=datetime.utcnow)

"""Normalized request/response contracts and enums for the provider gateway.

R04: Provider gateway, usage and quota — data contracts.
These are the sole canonical types for inference requests/responses.
No direct SDK calls outside provider_adapters.py.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class RoutingMode(str, Enum):
    """Controls which cost tiers are admissible for a request."""
    FREE_ONLY = "free_only"      # Only free models
    BALANCED = "balanced"         # Free → included fallback
    PREMIUM = "premium"           # Free → included → metered (if LLM_PAID_ENABLED)


class FinishReason(str, Enum):
    """Why the model stopped generating."""
    STOP = "stop"                 # Natural end
    LENGTH = "length"             # Hit max tokens (truncation)
    TOOL_CALLS = "tool_calls"    # Model issued tool calls
    CONTENT_FILTER = "content_filter"
    ERROR = "error"               # Transport / provider error
    INVALID_RESPONSE = "invalid_response"  # Empty, NaN, malformed JSON, etc.


class RequestState(str, Enum):
    """llm_requests.final_state values."""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DENIED = "denied"


class AttemptState(str, Enum):
    """llm_attempts.state values."""
    RESERVED = "reserved"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN_CHARGE = "unknown_charge"


class UsageSource(str, Enum):
    """How token counts were obtained."""
    ACTUAL = "actual"        # Provider reported usage
    ESTIMATED = "estimated"  # Heuristic / tokenizer estimate
    UNKNOWN = "unknown"      # No usage data available (crash, timeout)


class CostClass(str, Enum):
    """Model cost classification."""
    FREE = "free"          # No monetary cost
    INCLUDED = "included"  # Bundled / quota-included (still tracked)
    METERED = "metered"    # Pay-per-use


class Decision(str, Enum):
    """Routing decision for a candidate."""
    ALLOWED = "allowed"
    SKIPPED = "skipped"


class SkipReason(str, Enum):
    """Why a candidate was skipped."""
    MISSING_KEY = "missing_key"
    DISABLED = "disabled"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    QUOTA_EXHAUSTED = "quota_exhausted"
    EXPIRED_GATE = "expired_gate"
    POLICY_DENIED = "policy_denied"
    CIRCUIT_OPEN = "circuit_open"


class ErrorCode(str, Enum):
    """Stable error codes returned to users (no internal details)."""
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    QUOTA_EXHAUSTED = "quota_exhausted"
    POLICY_DENIED = "policy_denied"
    INVALID_RESPONSE = "invalid_response"
    TIMEOUT = "timeout"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class NormalizedRequest:
    """Canonical inference request — the sole input to ProviderGateway.execute().

    Callers (router.py, prediction_engine.py, session_manager.py) must build this
    and call ProviderGateway.execute(request, candidates).
    """
    task_type: str                                       # e.g. "forecast", "chat", "trade_sim"
    source: str                                          # "chatbot" | "chatbot_wored" | "webui" | "collector"
    principal: str                                       # Verified identity string
    messages: list[dict[str, str]]                      # [{"role": ..., "content": ...}]
    routing_mode: RoutingMode = RoutingMode.BALANCED
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    snapshot_id: str | None = None
    snapshot_hash: str | None = None
    output_schema: dict[str, Any] | None = None
    max_output_tokens: int = 4096
    timeout_seconds: float = 120.0
    allowed_tools: list[str] | None = None              # Tool names the model may call


@dataclass
class NormalizedResponse:
    """Canonical inference response from ProviderGateway.execute().

    Never exposes chain-of-thought as final_text; never leaks exception text.
    """
    final_text: str
    parsed_json: dict[str, Any] | None = None
    actual_provider: str = ""
    actual_model: str = ""
    requested_model: str = ""
    finish_reason: FinishReason = FinishReason.STOP
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    tool_tokens: int | None = None
    usage_source: UsageSource = UsageSource.UNKNOWN
    latency_ms: int = 0
    tool_calls: list[dict[str, Any]] | None = None
    attempt_id: str = ""
    request_id: str = ""
    error_code: ErrorCode | None = None


@dataclass(frozen=True)
class ModelEntry:
    """One model in the provider registry."""
    provider: str
    model_id: str
    endpoint_type: str          # "ollama_native" | "openai_compatible"
    enabled: bool
    cost_class: CostClass
    context_tokens: int
    max_output_tokens: int
    capabilities: dict[str, bool] = field(default_factory=dict)  # tools, thinking, json_schema, vision
    pricing: dict[str, Any] = field(default_factory=dict)        # currency, input_per_million, output_per_million
    pricing_source_url: str = ""
    validation_gate_id: str | None = None
    api_key_env: str = ""
    endpoint: str = ""
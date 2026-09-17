"""Provider Gateway — the SOLE inference entry point.

All inference calls must go through ProviderGateway.execute().
Direct SDK/HTTP calls outside provider_adapters.py are forbidden.

Features:
  - Normalized request/response
  - Candidate routing with budget/gate checks
  - Retry with full jitter backoff
  - Circuit breaker per provider
  - Usage ledger with atomic reservation/settlement
  - Budget policy enforcement
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ai.contracts import (
    AttemptState,
    CostClass,
    Decision,
    ErrorCode,
    FinishReason,
    ModelEntry,
    NormalizedRequest,
    NormalizedResponse,
    RequestState,
    RoutingMode,
    SkipReason,
    UsageSource,
)
from ai.provider_adapters import (
    AdapterError,
    InvalidResponseError,
    OllamaCloudAdapter,
    OpenAICompatibleAdapter,
    RawProviderResponse,
    TransportError,
    normalize_response,
)
from ai.usage_ledger import UsageLedger
from ai.budget_policy import BudgetPolicy

log = logging.getLogger(__name__)

# ── Circuit Breaker ─────────────────────────────────────────────────────────

@dataclass
class CircuitState:
    """Per-provider circuit breaker state (in-memory; shared via Redis in prod)."""
    consecutive_failures: int = 0
    state: str = "closed"          # closed | open | half_open
    opened_at: float | None = None
    failure_threshold: int = 3     # R04: 3 consecutive transport failures → open
    recovery_seconds: float = 60.0  # R04: open 60s

    def can_execute(self, now: float | None = None) -> bool:
        now = now or time.monotonic()
        if self.state == "closed":
            return True
        if self.state == "open":
            if self.opened_at and (now - self.opened_at) >= self.recovery_seconds:
                self.state = "half_open"
                return True  # one probe
            return False
        if self.state == "half_open":
            return True  # one probe allowed
        return False

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.state = "closed"
        self.opened_at = None

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.state = "open"
            self.opened_at = time.monotonic()
        elif self.state == "half_open":
            self.state = "open"
            self.opened_at = time.monotonic()


class CircuitBreakerRegistry:
    """Registry of per-provider circuit breakers."""

    def __init__(self):
        self._circuits: dict[str, CircuitState] = {}

    def get(self, provider: str) -> CircuitState:
        if provider not in self._circuits:
            self._circuits[provider] = CircuitState()
        return self._circuits[provider]

    def reset_all(self) -> None:
        self._circuits.clear()


# ── Model Registry ──────────────────────────────────────────────────────────

def load_registry(path: str | None = None) -> dict[str, ModelEntry]:
    """Load provider_registry.json and return {key: ModelEntry}."""
    if path is None:
        path = os.getenv("LLM_REGISTRY_PATH") or os.path.join(
            os.path.dirname(__file__), "..", "config", "provider_registry.json"
        )
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        log.warning("Provider registry not found or invalid at %s", path)
        return {}

    entries: dict[str, ModelEntry] = {}
    models = data.get("models", [])
    version = data.get("registry_version", "unknown")
    log.info("Loading provider registry v%s with %d models", version, len(models))

    for m in models:
        key = f"{m.get('provider', '')}/{m.get('model_id', '')}"
        cost_class = CostClass(m.get("cost_class", "free"))
        caps = m.get("capabilities", {})
        pricing = m.get("pricing", {})
        entries[key] = ModelEntry(
            provider=m.get("provider", ""),
            model_id=m.get("model_id", ""),
            endpoint_type=m.get("endpoint_type", "openai_compatible"),
            enabled=m.get("enabled", True),
            cost_class=cost_class,
            context_tokens=m.get("context_tokens", 0),
            max_output_tokens=m.get("max_output_tokens", 4096),
            capabilities=caps if isinstance(caps, dict) else {},
            pricing=pricing,
            pricing_source_url=m.get("pricing_source_url", ""),
            validation_gate_id=m.get("validation_gate_id"),
            api_key_env=m.get("api_key_env", ""),
            endpoint=m.get("endpoint", ""),
        )
    return entries


# ── Routing Decision ───────────────────────────────────────────────────────

@dataclass
class RoutingDecision:
    request_id: str
    sequence: int
    candidate_key: str
    decision: Decision
    reason_code: SkipReason | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Provider Gateway ────────────────────────────────────────────────────────

class ProviderGateway:
    """The SOLE inference entry point. No direct SDK calls outside this class.

    Usage:
        gw = ProviderGateway(registry_path="config/provider_registry.json")
        response = await gw.execute(request, candidates=["ollama/minimax-m3", ...])
    """

    def __init__(
        self,
        registry_path: str | None = None,
        budget_policy: BudgetPolicy | None = None,
        ledger: UsageLedger | None = None,
        circuit_registry: CircuitBreakerRegistry | None = None,
    ):
        self.registry = load_registry(registry_path)
        self.budget_policy = budget_policy or BudgetPolicy()
        self.ledger = ledger or UsageLedger()
        self.circuits = circuit_registry or CircuitBreakerRegistry()
        self._adapters: dict[str, Any] = {}  # lazily created

        # Retry config per R04: max 2 transport attempts per candidate
        self.max_transport_attempts = 2
        self.max_jitter_cap = 4.0  # delay in 0..min(4, 2^attempt)

    def _get_adapter(self, entry: ModelEntry):
        """Lazily create and cache adapter for a model entry."""
        key = f"{entry.provider}/{entry.model_id}"
        if key in self._adapters:
            return self._adapters[key]

        if entry.endpoint_type == "ollama_native":
            adapter = OllamaCloudAdapter(
                base_url=entry.endpoint or None,
                api_key_env=entry.api_key_env,
            )
        else:
            # Default: OpenAI-compatible
            adapter = OpenAICompatibleAdapter(
                base_url=entry.endpoint,
                api_key_env=entry.api_key_env,
                model_id_override=None,
            )
        self._adapters[key] = adapter
        return adapter

    async def execute(
        self,
        request: NormalizedRequest,
        candidates: list[str],
    ) -> NormalizedResponse:
        """Execute an inference request through the gateway.

        Routes through candidates in order, respecting budget, circuit breaker,
        and validation gates. Returns the first successful NormalizedResponse
        or an error response if all candidates exhausted.
        """
        request_id = request.request_id

        # Create request row in ledger
        await self.ledger.create_request(
            request_id=request_id,
            source=request.source,
            principal=request.principal,
            task_type=request.task_type,
            snapshot_id=request.snapshot_id,
        )

        paid_enabled = os.getenv("LLM_PAID_ENABLED", "false").strip().lower() in ("true", "1", "yes")
        sequence = 0
        last_error_code: ErrorCode | None = None

        for candidate_key in candidates:
            entry = self.registry.get(candidate_key)
            if entry is None:
                log.warning("Candidate %s not in registry, skipping", candidate_key)
                await self._log_decision(request_id, sequence, candidate_key, Decision.SKIPPED, SkipReason.DISABLED)
                sequence += 1
                continue

            # ── Pre-call policy checks (no network call if skipped) ──

            # 1. Check enabled
            if not entry.enabled:
                await self._log_decision(request_id, sequence, candidate_key, Decision.SKIPPED, SkipReason.DISABLED)
                sequence += 1
                continue

            # 2. Check API key
            api_key = os.getenv(entry.api_key_env, "").strip()
            if not api_key:
                await self._log_decision(request_id, sequence, candidate_key, Decision.SKIPPED, SkipReason.MISSING_KEY)
                sequence += 1
                continue

            # 3. Check routing mode vs cost class
            if not self._routing_allows(request.routing_mode, entry.cost_class, paid_enabled):
                reason = SkipReason.POLICY_DENIED
                await self._log_decision(request_id, sequence, candidate_key, Decision.SKIPPED, reason)
                sequence += 1
                continue

            # 4. Check validation gate for metered
            if entry.cost_class == CostClass.METERED and entry.validation_gate_id:
                if not await self._gate_valid(entry):
                    await self._log_decision(request_id, sequence, candidate_key, Decision.SKIPPED, SkipReason.EXPIRED_GATE)
                    sequence += 1
                    continue

            # 5. Check circuit breaker
            circuit = self.circuits.get(entry.provider)
            if not circuit.can_execute():
                await self._log_decision(request_id, sequence, candidate_key, Decision.SKIPPED, SkipReason.CIRCUIT_OPEN)
                sequence += 1
                continue

            # 6. Check capability requirements
            if request.output_schema and not entry.capabilities.get("json_schema", False):
                await self._log_decision(request_id, sequence, candidate_key, Decision.SKIPPED, SkipReason.UNSUPPORTED_CAPABILITY)
                sequence += 1
                continue

            # ── Reserve budget (atomic) ──
            reservation = await self.ledger.reserve(
                request_id=request_id,
                sequence=sequence,
                provider=entry.provider,
                model=entry.model_id,
                principal=request.principal,
                source=request.source,
                context_tokens=entry.context_tokens,
                cost_class=entry.cost_class,
                pricing=entry.pricing,
            )
            if reservation is None:
                # Budget exhausted
                await self._log_decision(request_id, sequence, candidate_key, Decision.SKIPPED, SkipReason.QUOTA_EXHAUSTED)
                last_error_code = ErrorCode.QUOTA_EXHAUSTED
                sequence += 1
                continue

            attempt_id = reservation["attempt_id"]

            # ── Attempt with retry ──
            await self._log_decision(request_id, sequence, candidate_key, Decision.ALLOWED)
            await self.ledger.update_attempt(attempt_id, AttemptState.RUNNING)

            response = await self._attempt_with_retry(
                request=request,
                entry=entry,
                attempt_id=attempt_id,
                request_id=request_id,
            )

            if response is not None and response.error_code is None:
                # Success — settle budget
                await self.ledger.settle(
                    attempt_id=attempt_id,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cached_tokens=response.cached_tokens,
                    reasoning_tokens=response.reasoning_tokens,
                    tool_tokens=response.tool_tokens,
                    usage_source=response.usage_source,
                )
                # Update request final state
                await self.ledger.update_request_state(request_id, RequestState.COMPLETED)
                circuit.record_success()
                return response

            # Failure on this candidate
            circuit.record_failure()
            last_error_code = response.error_code if response else ErrorCode.PROVIDER_UNAVAILABLE
            sequence += 1
            continue

        # All candidates exhausted
        final_state = RequestState.FAILED
        error_code = last_error_code or ErrorCode.PROVIDER_UNAVAILABLE
        if error_code == ErrorCode.QUOTA_EXHAUSTED:
            final_state = RequestState.DENIED

        await self.ledger.update_request_state(request_id, final_state)

        return NormalizedResponse(
            final_text="",
            error_code=error_code,
            request_id=request_id,
            finish_reason=FinishReason.ERROR,
            actual_provider="",
            actual_model="",
        )

    async def _attempt_with_retry(
        self,
        request: NormalizedRequest,
        entry: ModelEntry,
        attempt_id: str,
        request_id: str,
    ) -> NormalizedResponse | None:
        """Try a candidate with up to max_transport_attempts, full jitter backoff."""
        adapter = self._get_adapter(entry)
        last_exc: Exception | None = None

        for attempt in range(self.max_transport_attempts):
            try:
                # Compute delay: full jitter 0..min(4, 2^attempt) seconds
                if attempt > 0:
                    max_delay = min(self.max_jitter_cap, 2 ** attempt)
                    delay = random.uniform(0, max_delay)
                    log.info("Retry attempt %d for %s/%s, delay %.2fs",
                             attempt, entry.provider, entry.model_id, delay)
                    await asyncio.sleep(delay)

                # Update attempt state to running
                await self.ledger.update_attempt(attempt_id, AttemptState.RUNNING)

                # Call adapter
                if entry.endpoint_type == "ollama_native":
                    raw = await adapter.call(
                        model_id=entry.model_id,
                        messages=request.messages,
                        max_tokens=request.max_output_tokens,
                        timeout_seconds=request.timeout_seconds,
                    )
                else:
                    raw = await adapter.call(
                        model_id=entry.model_id,
                        messages=request.messages,
                        max_tokens=request.max_output_tokens,
                        timeout_seconds=request.timeout_seconds,
                        output_schema=request.output_schema,
                        allowed_tools=request.allowed_tools,
                    )

                # Normalize and validate
                response = normalize_response(
                    raw=raw,
                    provider=entry.provider,
                    model_id=entry.model_id,
                    requested_model=entry.model_id,
                    request=request,
                    attempt_id=attempt_id,
                    request_id=request_id,
                )
                return response

            except InvalidResponseError as exc:
                # Invalid response — try next candidate (not retry)
                log.warning("Invalid response from %s/%s: %s", entry.provider, entry.model_id, exc)
                await self.ledger.settle_unknown(attempt_id, error_code="invalid_response")
                return NormalizedResponse(
                    final_text="",
                    error_code=ErrorCode.INVALID_RESPONSE,
                    finish_reason=FinishReason.INVALID_RESPONSE,
                    request_id=request_id,
                    attempt_id=attempt_id,
                )

            except TransportError as exc:
                last_exc = exc
                if not exc.retryable:
                    log.warning("Non-retryable transport error from %s/%s: %s",
                                entry.provider, entry.model_id, exc)
                    break  # Don't retry, move to next candidate
                log.info("Retryable transport error from %s/%s (attempt %d): %s",
                         entry.provider, entry.model_id, attempt, exc)
                continue  # Retry within attempt budget

            except AdapterError as exc:
                # Fatal adapter error — move to next candidate
                log.warning("Adapter error from %s/%s: %s", entry.provider, entry.model_id, exc)
                await self.ledger.settle_unknown(attempt_id, error_code="adapter_error")
                return NormalizedResponse(
                    final_text="",
                    error_code=ErrorCode.PROVIDER_UNAVAILABLE,
                    finish_reason=FinishReason.ERROR,
                    request_id=request_id,
                    attempt_id=attempt_id,
                )

            except Exception as exc:
                # Unexpected error — treat as transport failure
                log.exception("Unexpected error from %s/%s", entry.provider, entry.model_id)
                last_exc = exc
                continue

        # All transport retries exhausted for this candidate
        await self.ledger.settle_unknown(attempt_id, error_code="transport_exhausted")
        return NormalizedResponse(
            final_text="",
            error_code=ErrorCode.PROVIDER_UNAVAILABLE,
            finish_reason=FinishReason.ERROR,
            request_id=request_id,
            attempt_id=attempt_id,
        )

    def _routing_allows(self, mode: RoutingMode, cost_class: CostClass,
                         paid_enabled: bool) -> bool:
        """Check if routing mode permits this cost class."""
        if cost_class == CostClass.FREE:
            return True
        if cost_class == CostClass.INCLUDED:
            return mode in (RoutingMode.BALANCED, RoutingMode.PREMIUM)
        # METERED: only if premium and paid enabled
        if mode == RoutingMode.PREMIUM and paid_enabled:
            return True
        return False

    async def _gate_valid(self, entry: ModelEntry) -> bool:
        """Check if validation gate is valid (not expired)."""
        if not entry.validation_gate_id:
            return True  # No gate required
        # In production, query llm_validation_gates table.
        # For now, check if we have a cached gate result.
        return await self.ledger.check_gate(entry.provider, entry.model_id)

    async def _log_decision(self, request_id: str, sequence: int,
                             candidate_key: str, decision: Decision,
                             reason_code: SkipReason | None = None) -> None:
        """Log routing decision."""
        await self.ledger.log_routing_decision(request_id, sequence, candidate_key, decision.value, reason_code.value if reason_code else None)

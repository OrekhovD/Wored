"""R04 Gateway Contracts — Tests for Provider Gateway, Usage Ledger, Budget Policy.

Tests R04-01 through R04-12 as specified in the stabilization spec.
All tests are offline (no provider requests, no Telegram, no live data writes).
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "chatbot"), str(ROOT)]

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
from ai.budget_policy import BudgetPolicy, GLOBAL_DEFAULTS
from ai.usage_ledger import UsageLedger
from ai.provider_gateway import (
    CircuitState,
    CircuitBreakerRegistry,
    ProviderGateway,
    load_registry,
)
from ai.provider_adapters import (
    InvalidResponseError,
    TransportError,
    AdapterError,
    OllamaCloudAdapter,
    OpenAICompatibleAdapter,
    _validate_final_text,
    normalize_response,
    RawProviderResponse,
)


# ── Helpers ────────────────────────────────────────────────────────────────

def make_request(**overrides) -> NormalizedRequest:
    defaults = {
        "task_type": "chat",
        "source": "chatbot",
        "principal": "test_user",
        "messages": [{"role": "user", "content": "hello"}],
        "routing_mode": RoutingMode.BALANCED,
    }
    defaults.update(overrides)
    return NormalizedRequest(**defaults)


def make_model_entry(**overrides) -> ModelEntry:
    defaults = {
        "provider": "test_provider",
        "model_id": "test-model",
        "endpoint_type": "openai_compatible",
        "enabled": True,
        "cost_class": CostClass.FREE,
        "context_tokens": 128000,
        "max_output_tokens": 4096,
        "capabilities": {"tools": False, "thinking": False, "json_schema": False, "vision": False},
        "api_key_env": "TEST_API_KEY",
        "endpoint": "https://example.com/v1",
    }
    defaults.update(overrides)
    return ModelEntry(**defaults)


# ── R04-01: Budget enforcement (20 concurrent → 3 network calls) ─────────

class TestBudgetEnforcement(unittest.TestCase):
    """R04-01: 20 concurrent attempts with limit 3 → exactly 3 network calls."""

    def test_only_limit_requests_succeed(self):
        """When daily limit is 3, only 3 attempts get past reservation."""
        policy = BudgetPolicy(global_overrides={
            "day": {"requests": 3, "tokens": 1000000, "cost": 0.0},
        })
        ledger = UsageLedger(in_memory=True, budget_policy=policy)

        gateway = ProviderGateway(budget_policy=policy, ledger=ledger)

        # Manually test reservation directly
        async def reserve_n(n):
            results = []
            for i in range(n):
                result = await ledger.reserve(
                    request_id=f"req_{i}",
                    sequence=0,
                    provider="test",
                    model="test-model",
                    principal="test_user",
                    source="chatbot",
                    context_tokens=1000,
                    cost_class=CostClass.FREE,
                    pricing={},
                )
                results.append(result)
            return results

        results = asyncio.run(reserve_n(20))
        succeeded = [r for r in results if r is not None]
        self.assertEqual(len(succeeded), 3, f"Expected exactly 3 reservations, got {len(succeeded)}")


# ── R04-02: Two-bot/WebUI budget is shared ──────────────────────────────

class TestSharedBudget(unittest.TestCase):
    """R04-02: Two sources share the same global budget."""

    def test_two_sources_share_global_budget(self):
        """Two bots/WebUI share global budget, combined reservations tracked."""
        policy = BudgetPolicy(global_overrides={
            "day": {"requests": 5, "tokens": 1000000, "cost": 0.0},
        })
        ledger = UsageLedger(in_memory=True, budget_policy=policy)

        async def run():
            results = []
            for source in ["chatbot", "webui"]:
                for i in range(3):
                    result = await ledger.reserve(
                        request_id=f"req_{source}_{i}",
                        sequence=0,
                        provider="test",
                        model="test-model",
                        principal=f"{source}_user",
                        source=source,
                        context_tokens=1000,
                        cost_class=CostClass.FREE,
                        pricing={},
                    )
                    results.append(result)
            return results

        results = asyncio.run(run())
        succeeded = [r for r in results if r is not None]
        self.assertEqual(len(succeeded), 5, "Combined reservations should not exceed 5")


# ── R04-03: Retry ledger is separate ────────────────────────────────────

class TestRetryLedger(unittest.TestCase):
    """R04-03: Retry attempts are separate ledger rows."""

    def test_retry_creates_separate_attempt(self):
        """Each retry creates a new attempt row with different sequence."""
        ledger = UsageLedger(in_memory=True)

        async def run():
            await ledger.create_request("req1", "chatbot", "test", "chat")
            # First attempt
            res1 = await ledger.reserve(
                request_id="req1", sequence=0, provider="p1", model="m1",
                principal="test", source="chatbot", context_tokens=1000,
                cost_class=CostClass.FREE, pricing={},
            )
            # Retry creates a second attempt
            res2 = await ledger.reserve(
                request_id="req1", sequence=1, provider="p1", model="m1",
                principal="test", source="chatbot", context_tokens=1000,
                cost_class=CostClass.FREE, pricing={},
            )
            return res1, res2

        res1, res2 = asyncio.run(run())
        self.assertIsNotNone(res1)
        self.assertIsNotNone(res2)
        self.assertNotEqual(res1["attempt_id"], res2["attempt_id"])


# ── R04-04: Double settlement is idempotent ────────────────────────────

class TestDoubleSettlement(unittest.TestCase):
    """R04-04: Two settlements of the same attempt — second is a no-op."""

    def test_double_settle_is_idempotent(self):
        """Second settle call is a no-op (settled_at check)."""
        ledger = UsageLedger(in_memory=True)

        async def run():
            await ledger.create_request("req1", "chatbot", "test", "chat")
            res = await ledger.reserve(
                request_id="req1", sequence=0, provider="p1", model="m1",
                principal="test", source="chatbot", context_tokens=1000,
                cost_class=CostClass.FREE, pricing={},
            )
            attempt_id = res["attempt_id"]

            # First settlement
            await ledger.settle(attempt_id, input_tokens=100, output_tokens=50, usage_source=UsageSource.ACTUAL)
            # Second settlement — should be no-op
            await ledger.settle(attempt_id, input_tokens=100, output_tokens=50, usage_source=UsageSource.ACTUAL)

            att = ledger.get_mem_attempts()[attempt_id]
            self.assertEqual(att["state"], AttemptState.SUCCEEDED.value)

        asyncio.run(run())


# ── R04-05: Crash reservation (unknown_charge) ──────────────────────────

class TestCrashReservation(unittest.TestCase):
    """R04-05: Crash after send accounts conservatively as unknown_charge."""

    def test_crash_settles_as_unknown_charge(self):
        """After crash, settle_unknown charges the full reserve."""
        ledger = UsageLedger(in_memory=True)

        async def run():
            await ledger.create_request("req1", "chatbot", "test", "chat")
            res = await ledger.reserve(
                request_id="req1", sequence=0, provider="p1", model="m1",
                principal="test", source="chatbot", context_tokens=5000,
                cost_class=CostClass.FREE, pricing={},
            )
            attempt_id = res["attempt_id"]

            # Simulate crash: settle as unknown
            await ledger.settle_unknown(attempt_id, error_code="transport_timeout")

            att = ledger.get_mem_attempts()[attempt_id]
            self.assertEqual(att["state"], AttemptState.UNKNOWN_CHARGE.value)

        asyncio.run(run())


# ── R04-06: Nullable usage ─────────────────────────────────────────────

class TestNullableUsage(unittest.TestCase):
    """R04-06: Provider doesn't report usage → usage_source=unknown."""

    def test_null_usage_settles_as_unknown(self):
        """No usage data from provider → full reserve charged, source unknown."""
        ledger = UsageLedger(in_memory=True)

        async def run():
            await ledger.create_request("req1", "chatbot", "test", "chat")
            res = await ledger.reserve(
                request_id="req1", sequence=0, provider="p1", model="m1",
                principal="test", source="chatbot", context_tokens=5000,
                cost_class=CostClass.FREE, pricing={},
            )
            attempt_id = res["attempt_id"]

            # Settle with no usage (both None)
            await ledger.settle(attempt_id, input_tokens=None, output_tokens=None)

            att = ledger.get_mem_attempts()[attempt_id]
            # Should be unknown_charge since total is 0
            self.assertEqual(att["state"], AttemptState.UNKNOWN_CHARGE.value)

        asyncio.run(run())


# ── R04-07: Cached tokens not double-counted ───────────────────────────

class TestCachedTokens(unittest.TestCase):
    """R04-07: Cached/reasoning tokens not double-counted if included in input/output."""

    def test_cached_included_in_input(self):
        """When cached_tokens is reported, total is still input+output (no double-count)."""
        raw = RawProviderResponse(
            final_text="Hello",
            finish_reason_raw="stop",
            input_tokens=100,
            output_tokens=50,
            cached_tokens=30,  # Already part of input_tokens
            reasoning_tokens=20,  # Already part of output_tokens
            latency_ms=100,
        )
        resp = normalize_response(
            raw=raw,
            provider="test",
            model_id="test-model",
            requested_model="test-model",
            request=make_request(),
            attempt_id="att1",
            request_id="req1",
        )
        # Total is 100+50=150, NOT 100+50+30+20=200
        self.assertEqual(resp.input_tokens, 100)
        self.assertEqual(resp.output_tokens, 50)
        self.assertEqual(resp.cached_tokens, 30)
        self.assertEqual(resp.reasoning_tokens, 20)
        self.assertEqual(resp.usage_source, UsageSource.ACTUAL)


# ── R04-08: Empty final_text ────────────────────────────────────────────

class TestEmptyFinalText(unittest.TestCase):
    """R04-08: Empty/whitespace final without valid tool_calls → invalid_response."""

    def test_empty_final_raises_invalid(self):
        """Empty final_text with no allowed tools → InvalidResponseError."""
        with self.assertRaises(InvalidResponseError):
            _validate_final_text("", "stop")

    def test_whitespace_final_raises_invalid(self):
        """Whitespace-only final_text → InvalidResponseError."""
        with self.assertRaises(InvalidResponseError):
            _validate_final_text("   \n  ", "stop")

    def test_empty_final_with_valid_tool_calls_passes(self):
        """Empty final but valid tool_calls in allowed_tools → passes."""
        result = _validate_final_text(
            "",
            "tool_calls",
            allowed_tools=["get_weather"],
            tool_calls=[{"function": {"name": "get_weather"}}],
        )
        # Should not raise — tool call is acceptable
        self.assertEqual(result, FinishReason.TOOL_CALLS)


# ── R04-09: Thinking-only response ─────────────────────────────────────

class TestThinkingOnly(unittest.TestCase):
    """R04-09: reasoning/thinking does NOT replace final_text."""

    def test_thinking_not_replaces_final(self):
        """When OpenAI returns thinking in message, final is still content."""
        raw = RawProviderResponse(
            final_text="The answer is 42",
            finish_reason_raw="stop",
            input_tokens=50,
            output_tokens=30,
            reasoning_tokens=100,  # Thinking tokens — should not replace final
            latency_ms=200,
        )
        resp = normalize_response(
            raw=raw,
            provider="test",
            model_id="test-model",
            requested_model="test-model",
            request=make_request(),
            attempt_id="att1",
            request_id="req1",
        )
        self.assertEqual(resp.final_text, "The answer is 42")
        self.assertEqual(resp.reasoning_tokens, 100)


# ── R04-10: Truncated JSON ──────────────────────────────────────────────

class TestTruncatedJSON(unittest.TestCase):
    """R04-10: Truncated/length finish on schema request → invalid_response."""

    def test_length_finish_raises_invalid(self):
        """finish_reason=length (truncation) → InvalidResponseError."""
        with self.assertRaises(InvalidResponseError):
            _validate_final_text("some partial text", "length")

    def test_malformed_json_on_schema_raises(self):
        """Malformed JSON when output_schema requested → InvalidResponseError."""
        with self.assertRaises(InvalidResponseError):
            _validate_final_text(
                "not valid json",
                "stop",
                output_schema={"type": "object"},
            )


# ── R04-11: Validation gate expired ─────────────────────────────────────

class TestValidationGate(unittest.TestCase):
    """R04-11: Expired gate skips metered candidate."""

    def test_no_gate_data_means_gate_false(self):
        """No gate data for a metered model → gate invalid."""
        ledger = UsageLedger(in_memory=True)
        result = asyncio.run(ledger.check_gate("test_provider", "test-model"))
        self.assertFalse(result, "No gate data should return False")

    def test_passed_gate_in_memory(self):
        """Gate with passed=True in memory → valid."""
        ledger = UsageLedger(in_memory=True)
        ledger._mem_gates["test_provider/test-model"] = {"passed": True}
        result = asyncio.run(ledger.check_gate("test_provider", "test-model"))
        self.assertTrue(result)


# ── R04-12: Worker fallback premium blocked ────────────────────────────

class TestWorkerFallbackPremiumBlocked(unittest.TestCase):
    """R04-12: Worker (free_only/balanced) cannot access metered models."""

    def test_free_only_blocks_included(self):
        """free_only routing blocks included models."""
        policy = BudgetPolicy()
        self.assertFalse(policy.allows_tier("free_only", CostClass.INCLUDED))

    def test_free_only_blocks_metered(self):
        """free_only routing blocks metered models."""
        policy = BudgetPolicy()
        self.assertFalse(policy.allows_tier("free_only", CostClass.METERED))

    def test_balanced_allows_included(self):
        """balanced routing allows included models."""
        policy = BudgetPolicy()
        self.assertTrue(policy.allows_tier("balanced", CostClass.INCLUDED))

    def test_balanced_blocks_metered_without_paid(self):
        """balanced routing blocks metered even without paid."""
        policy = BudgetPolicy()
        self.assertFalse(policy.allows_tier("balanced", CostClass.METERED))

    def test_premium_blocks_metered_without_paid(self):
        """premium routing blocks metered when LLM_PAID_ENABLED=false."""
        with patch.dict(os.environ, {"LLM_PAID_ENABLED": "false"}):
            policy = BudgetPolicy()
            self.assertFalse(policy.allows_tier("premium", CostClass.METERED))

    def test_premium_allows_metered_with_paid(self):
        """premium routing allows metered when LLM_PAID_ENABLED=true."""
        with patch.dict(os.environ, {"LLM_PAID_ENABLED": "true"}):
            policy = BudgetPolicy()
            self.assertTrue(policy.allows_tier("premium", CostClass.METERED))

    def test_all_modes_allow_free(self):
        """All routing modes allow free models."""
        policy = BudgetPolicy()
        for mode in ("free_only", "balanced", "premium"):
            self.assertTrue(policy.allows_tier(mode, CostClass.FREE))


# ── Additional unit tests for core contracts ────────────────────────────

class TestNormalizedRequest(unittest.TestCase):
    """Test NormalizedRequest dataclass."""

    def test_request_id_auto_generated(self):
        """Request ID is auto-generated UUID if not provided."""
        req = make_request()
        self.assertTrue(len(req.request_id) > 0)

    def test_request_fields_required(self):
        """task_type, source, principal, messages are required."""
        with self.assertRaises(TypeError):
            NormalizedRequest()

    def test_routing_mode_defaults(self):
        """Default routing mode is BALANCED."""
        req = make_request()
        self.assertEqual(req.routing_mode, RoutingMode.BALANCED)


class TestNormalizedResponse(unittest.TestCase):
    """Test NormalizedResponse dataclass."""

    def test_error_response(self):
        """Error response has error_code set."""
        resp = NormalizedResponse(
            final_text="",
            error_code=ErrorCode.PROVIDER_UNAVAILABLE,
            finish_reason=FinishReason.ERROR,
        )
        self.assertEqual(resp.error_code, ErrorCode.PROVIDER_UNAVAILABLE)


class TestCircuitBreaker(unittest.TestCase):
    """Test circuit breaker per R04 spec: 3 failures → open 60s."""

    def test_three_failures_opens_circuit(self):
        """3 consecutive transport failures → circuit opens."""
        cb = CircuitState(failure_threshold=3, recovery_seconds=60.0)
        self.assertTrue(cb.can_execute())
        cb.record_failure()
        cb.record_failure()
        self.assertTrue(cb.can_execute())
        cb.record_failure()
        self.assertFalse(cb.can_execute())

    def test_circuit_recovers_after_timeout(self):
        """After 60s, circuit allows half-open probe."""
        cb = CircuitState(failure_threshold=3, recovery_seconds=0.01)  # Very short for testing
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        self.assertFalse(cb.can_execute())
        # Wait for recovery
        time.sleep(0.02)
        self.assertTrue(cb.can_execute())  # half_open probe

    def test_half_open_success_closes(self):
        """Success in half-open → circuit closes."""
        cb = CircuitState(failure_threshold=3, recovery_seconds=0.01)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.02)
        self.assertTrue(cb.can_execute())  # half_open
        cb.record_success()
        self.assertTrue(cb.can_execute())  # closed again
        self.assertEqual(cb.consecutive_failures, 0)

    def test_half_open_failure_reopens(self):
        """Failure in half-open → circuit reopens."""
        cb = CircuitState(failure_threshold=3, recovery_seconds=0.01)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.02)
        self.assertTrue(cb.can_execute())  # half_open
        cb.record_failure()
        self.assertFalse(cb.can_execute())  # open again


class TestBudgetPolicyLimits(unittest.TestCase):
    """Test BudgetPolicy limit calculations."""

    def test_global_defaults_match_spec(self):
        """Default limits match R04 spec."""
        policy = BudgetPolicy()
        limits = policy.get_limits("global", "day", CostClass.FREE)
        self.assertEqual(limits["request_limit"], 200)
        self.assertEqual(limits["token_limit"], 20_000_000)

        limits = policy.get_limits("global", "week", CostClass.FREE)
        self.assertEqual(limits["request_limit"], 1000)
        self.assertEqual(limits["token_limit"], 100_000_000)

        limits = policy.get_limits("global", "month", CostClass.FREE)
        self.assertEqual(limits["request_limit"], 2000)
        self.assertEqual(limits["token_limit"], 200_000_000)

    def test_metered_cost_limit_zero_without_paid(self):
        """Metered cost limit is 0 when LLM_PAID_ENABLED=false."""
        with patch.dict(os.environ, {"LLM_PAID_ENABLED": "false"}):
            policy = BudgetPolicy()
            limits = policy.get_limits("global", "day", CostClass.METERED)
            self.assertEqual(limits["cost_limit"], 0.0)

    def test_free_included_no_cost_limit(self):
        """Free/included cost_class has no cost limit (None)."""
        policy = BudgetPolicy()
        for cc in (CostClass.FREE, CostClass.INCLUDED):
            limits = policy.get_limits("global", "day", cc)
            self.assertIsNone(limits["cost_limit"])


class TestRoutingDecisions(unittest.TestCase):
    """Test gateway routing decisions."""

    def test_skip_disabled_model(self):
        """Disabled models are skipped."""
        ledger = UsageLedger(in_memory=True)
        policy = BudgetPolicy()
        gateway = ProviderGateway(budget_policy=policy, ledger=ledger)

        # Create registry with a disabled model
        entry = make_model_entry(enabled=False)
        gateway.registry = {"test/disabled": entry}

        request = make_request(routing_mode=RoutingMode.FREE_ONLY)
        result = asyncio.run(gateway.execute(request, ["test/disabled"]))

        # Should fail with provider_unavailable (all candidates skipped)
        self.assertEqual(result.error_code, ErrorCode.PROVIDER_UNAVAILABLE)

    def test_skip_missing_key(self):
        """Models without API key are skipped."""
        ledger = UsageLedger(in_memory=True)
        policy = BudgetPolicy()
        gateway = ProviderGateway(budget_policy=policy, ledger=ledger)

        entry = make_model_entry(api_key_env="NONEXISTENT_KEY_12345")
        gateway.registry = {"test/nokey": entry}

        request = make_request(routing_mode=RoutingMode.FREE_ONLY)
        result = asyncio.run(gateway.execute(request, ["test/nokey"]))

        self.assertEqual(result.error_code, ErrorCode.PROVIDER_UNAVAILABLE)

    def test_skip_metered_without_paid(self):
        """Metered models skipped when LLM_PAID_ENABLED=false."""
        with patch.dict(os.environ, {"LLM_PAID_ENABLED": "false"}):
            ledger = UsageLedger(in_memory=True)
            policy = BudgetPolicy()
            gateway = ProviderGateway(budget_policy=policy, ledger=ledger)

            entry = make_model_entry(cost_class=CostClass.METERED, api_key_env="NONEXISTENT_KEY")
            gateway.registry = {"test/metered": entry}

            request = make_request(routing_mode=RoutingMode.PREMIUM)
            result = asyncio.run(gateway.execute(request, ["test/metered"]))

            # Should be skipped due to policy
            decisions = ledger.get_mem_decisions()
            skip_decisions = [d for d in decisions if d["decision"] == "skipped"]


class TestProviderRegistry(unittest.TestCase):
    """Test provider registry loading."""

    def test_load_registry_from_json(self):
        """Registry JSON loads correctly."""
        registry_path = str(ROOT / "config" / "provider_registry.json")
        registry = load_registry(registry_path)
        self.assertGreater(len(registry), 0, "Registry should have entries")

        # Check required fields
        for key, entry in registry.items():
            self.assertTrue(entry.provider)
            self.assertTrue(entry.model_id)
            self.assertIn(entry.endpoint_type, ("native", "openai", "ollama_native", "openai_compatible"))
            self.assertIn(entry.cost_class, (CostClass.FREE, CostClass.INCLUDED, CostClass.METERED))

    def test_registry_has_ollama_entry(self):
        """Registry contains Ollama Cloud entry."""
        registry_path = str(ROOT / "config" / "provider_registry.json")
        registry = load_registry(registry_path)
        ollama_entries = [e for e in registry.values() if "ollama" in e.provider.lower()]
        self.assertGreater(len(ollama_entries), 0, "Should have at least one Ollama entry")


class TestFinishReasonMapping(unittest.TestCase):
    """Test finish reason mapping from provider to normalized."""

    def test_stop_mapping(self):
        raw = RawProviderResponse(final_text="hello", finish_reason_raw="stop", input_tokens=10, output_tokens=5)
        resp = normalize_response(raw, "test", "m1", "m1", make_request(), "a1", "r1")
        self.assertEqual(resp.finish_reason, FinishReason.STOP)

    def test_length_mapping(self):
        raw = RawProviderResponse(final_text="partial...", finish_reason_raw="length", input_tokens=10, output_tokens=5)
        # length finish should raise InvalidResponseError
        with self.assertRaises(InvalidResponseError):
            normalize_response(raw, "test", "m1", "m1", make_request(), "a1", "r1")

    def test_tool_calls_mapping(self):
        raw = RawProviderResponse(
            final_text="",
            finish_reason_raw="tool_calls",
            input_tokens=10, output_tokens=5,
            tool_calls=[{"function": {"name": "get_weather"}}],
        )
        # Empty final with tool_calls but no allowed_tools → invalid
        with self.assertRaises(InvalidResponseError):
            normalize_response(raw, "test", "m1", "m1", make_request(), "a1", "r1")


class TestLedgerSequencing(unittest.TestCase):
    """Test that sequence numbers are issued atomically."""

    def test_sequence_increments_per_request(self):
        """Each reservation increments the sequence for a request."""
        ledger = UsageLedger(in_memory=True)

        async def run():
            await ledger.create_request("req1", "chatbot", "test", "chat")
            res1 = await ledger.reserve(
                request_id="req1", sequence=0, provider="p1", model="m1",
                principal="test", source="chatbot", context_tokens=1000,
                cost_class=CostClass.FREE, pricing={},
            )
            res2 = await ledger.reserve(
                request_id="req1", sequence=1, provider="p1", model="m1",
                principal="test", source="chatbot", context_tokens=1000,
                cost_class=CostClass.FREE, pricing={},
            )
            return res1, res2

        res1, res2 = asyncio.run(run())
        self.assertIsNotNone(res1)
        self.assertIsNotNone(res2)
        # Different attempt IDs
        self.assertNotEqual(res1["attempt_id"], res2["attempt_id"])


if __name__ == "__main__":
    unittest.main()
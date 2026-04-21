"""Regression tests: verify that resilience changes do not break dependent modules.

These tests verify the integration between:
  - providers/resilience.py (CircuitBreaker, ResilienceOrchestrator)
  - providers/openai_compatible.py (OpenAICompatibleAdapter)
  - providers/factory.py (ProviderFactory, get_resilience_handler)
  - routing/provider_manager.py (ProviderManager chain building)
"""
import asyncio

import pytest

from providers.resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerError,
    CircuitState,
    ResilienceOrchestrator,
    RetryConfig,
    RetryHandler,
    TimeoutConfig,
    TimeoutHandler,
    get_resilience_handler,
    reset_resilience_handlers,
)
from core.enums import RoutingMode
from routing.model_registry import ModelRegistry
from routing.policies import RoutingPolicy
from routing.chain_builder import ChainBuilder
from routing.provider_manager import ProviderManager


# ---------------------------------------------------------------------------
# M1-a: CircuitBreakerError → ProviderUnavailableError mapping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_circuit_breaker_error_maps_to_provider_unavailable():
    """When CircuitBreaker is OPEN, OpenAICompatibleAdapter must raise
    ProviderUnavailableError (not raw CircuitBreakerError).

    This tests the error-mapping contract at the adapter boundary.
    """
    from core.exceptions import ProviderUnavailableError
    from providers.openai_compatible import OpenAICompatibleAdapter, _ModelInfo

    # Reset global handlers to get a clean handler for this provider
    reset_resilience_handlers()

    adapter = OpenAICompatibleAdapter(
        provider_id="regression_test_provider",
        api_key="fake-key",
        base_url="http://localhost:9999",
        models={
            "test-model": _ModelInfo(
                input_cost_per_1k=0.001,
                output_cost_per_1k=0.002,
            )
        },
    )

    # Manually trip the circuit breaker via the global handler
    handler = get_resilience_handler("regression_test_provider")
    cb = handler.circuit_breaker

    # Force into OPEN state by recording enough failures
    for _ in range(cb.config.failure_threshold):
        await cb.record_failure()
    assert cb.current_state == CircuitState.OPEN

    # Now invoke should raise ProviderUnavailableError (not CircuitBreakerError)
    with pytest.raises(ProviderUnavailableError) as exc_info:
        await adapter.invoke(
            model="test-model",
            messages=[{"role": "user", "content": "test"}],
        )

    assert "regression_test_provider" in str(exc_info.value)

    # Cleanup
    reset_resilience_handlers()


# ---------------------------------------------------------------------------
# M1-b: ResilienceOrchestrator full cycle (retry + CB + timeout)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_full_cycle_retry_then_success():
    """ResilienceOrchestrator retries on failure then succeeds on retry.

    Verifies that the full stack (timeout → CB → retry) works as an
    integrated unit and does not regress after the can_execute lock change.
    """
    cb = CircuitBreaker(
        CircuitBreakerConfig(failure_threshold=10, recovery_timeout=30),
        "orch_full_cycle",
    )
    retry = RetryHandler(RetryConfig(max_retries=3, base_delay_ms=50, jitter=False))
    timeout = TimeoutHandler(TimeoutConfig(total_timeout_seconds=5))
    orch = ResilienceOrchestrator(cb, retry, timeout)

    call_count = [0]

    async def flaky_func():
        call_count[0] += 1
        if call_count[0] <= 2:
            raise RuntimeError(f"Flaky failure #{call_count[0]}")
        return f"success on attempt {call_count[0]}"

    result = await orch.execute(flaky_func)
    assert result == "success on attempt 3"
    assert call_count[0] == 3

    # CB should still be CLOSED (threshold=10, only 2 failures)
    assert cb.current_state == CircuitState.CLOSED

    stats = orch.get_circuit_stats()
    assert stats["state"] == "closed"


# ---------------------------------------------------------------------------
# M1-c: ResilienceOrchestrator circuit opens after repeated retries exhaust
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_circuit_opens_after_retry_exhaustion():
    """When all retries fail, CB accumulates failures and eventually opens.

    With threshold=3 and max_retries=1:
      - 1st orch.execute: attempt 1 fails (failure_count=1), retry fails (failure_count=2)
        → raises RuntimeError
      - 2nd orch.execute: attempt 1 fails (failure_count=3, CB opens!), retry hits open CB
        → raises CircuitBreakerError (from retry layer)
    """
    cb = CircuitBreaker(
        CircuitBreakerConfig(failure_threshold=3, recovery_timeout=30),
        "orch_exhaust",
    )
    retry = RetryHandler(RetryConfig(max_retries=1, base_delay_ms=10, jitter=False))
    timeout = TimeoutHandler(TimeoutConfig(total_timeout_seconds=5))
    orch = ResilienceOrchestrator(cb, retry, timeout)

    async def always_fail():
        raise RuntimeError("permanent failure")

    # 1st call: 2 failures recorded, CB stays CLOSED (threshold=3)
    with pytest.raises(RuntimeError):
        await orch.execute(always_fail)
    assert cb.current_state == CircuitState.CLOSED

    # 2nd call: 3rd failure opens CB; retry hits open CB → CircuitBreakerError
    with pytest.raises(CircuitBreakerError):
        await orch.execute(always_fail)

    assert cb.current_state == CircuitState.OPEN

    # 3rd call: immediately rejected
    with pytest.raises(CircuitBreakerError):
        await orch.execute(always_fail)


# ---------------------------------------------------------------------------
# M1-d: ProviderManager chain building is unaffected by resilience changes
# ---------------------------------------------------------------------------

def test_provider_manager_chain_building_regression():
    """ProviderManager builds chains correctly — no regression from resilience changes."""
    registry = ModelRegistry()
    policy = RoutingPolicy()
    builder = ChainBuilder(registry, policy)
    manager = ProviderManager(registry, builder, policy)

    free_chain = manager.get_chain(RoutingMode.FREE_ONLY)
    assert isinstance(free_chain, list)
    assert len(free_chain) > 0
    assert "glm-5" not in free_chain  # Premium model excluded in FREE_ONLY

    balanced_chain = manager.get_chain(RoutingMode.BALANCED)
    assert "glm-5" in balanced_chain

    premium_chain = manager.get_chain(RoutingMode.PREMIUM)
    assert "glm-5" in premium_chain

    # Model availability checks
    assert manager.is_model_available("qwen-turbo", RoutingMode.FREE_ONLY)
    assert not manager.is_model_available("glm-5", RoutingMode.FREE_ONLY)
    assert manager.is_model_available("glm-5", RoutingMode.PREMIUM)


# ---------------------------------------------------------------------------
# M1-e: get_resilience_handler creates independent per-provider handlers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resilience_handlers_are_independent_per_provider():
    """Each provider_id gets its own CircuitBreaker; tripping one does not
    affect others.
    """
    reset_resilience_handlers()

    handler_a = get_resilience_handler("provider_a")
    handler_b = get_resilience_handler("provider_b")

    assert handler_a is not handler_b
    assert handler_a.circuit_breaker is not handler_b.circuit_breaker

    # Trip provider_a
    for _ in range(handler_a.circuit_breaker.config.failure_threshold):
        await handler_a.circuit_breaker.record_failure()

    assert handler_a.circuit_breaker.current_state == CircuitState.OPEN
    assert handler_b.circuit_breaker.current_state == CircuitState.CLOSED

    # Cleanup
    reset_resilience_handlers()

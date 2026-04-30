"""Тесты resilience-слоя chatbot (адаптировано из hypercube)."""
import asyncio
import pytest
from ai.resilience import (
    CircuitBreaker, CircuitBreakerConfig, CircuitBreakerError, CircuitState,
    RetryHandler, RetryConfig,
    TimeoutHandler, TimeoutConfig,
    ResilienceOrchestrator,
    get_resilience_handler, reset_resilience_handlers,
)

@pytest.fixture(autouse=True)
def cleanup():
    reset_resilience_handlers()
    yield
    reset_resilience_handlers()

# ── Circuit Breaker ──

@pytest.mark.asyncio
async def test_cb_starts_closed():
    cb = CircuitBreaker(CircuitBreakerConfig(), name="test")
    assert cb.current_state == CircuitState.CLOSED
    assert await cb.can_execute() is True

@pytest.mark.asyncio
async def test_cb_opens_after_threshold():
    cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3), name="test")
    for _ in range(3):
        await cb.record_failure()
    assert cb.current_state == CircuitState.OPEN
    assert await cb.can_execute() is False

@pytest.mark.asyncio
async def test_cb_recovers_after_timeout():
    cfg = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0.1)
    cb = CircuitBreaker(cfg, name="test")
    await cb.record_failure()
    assert cb.current_state == CircuitState.OPEN
    await asyncio.sleep(0.15)
    assert await cb.can_execute() is True
    assert cb.current_state == CircuitState.HALF_OPEN

@pytest.mark.asyncio
async def test_cb_execute_success():
    cb = CircuitBreaker(CircuitBreakerConfig(), name="test")
    async def ok(): return "ok"
    result = await cb.execute(ok)
    assert result == "ok"

@pytest.mark.asyncio
async def test_cb_execute_failure_raises():
    cb = CircuitBreaker(CircuitBreakerConfig(), name="test")
    async def fail(): raise ValueError("boom")
    with pytest.raises(ValueError):
        await cb.execute(fail)

# ── Retry ──

@pytest.mark.asyncio
async def test_retry_succeeds_on_second_attempt():
    call_count = 0
    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ConnectionError("tmp")
        return "ok"
    
    handler = RetryHandler(RetryConfig(max_retries=3, base_delay_ms=10, jitter=False))
    result = await handler.execute(flaky)
    assert result == "ok"
    assert call_count == 2

@pytest.mark.asyncio
async def test_retry_exhausted_raises():
    async def always_fail(): raise ConnectionError("permanent")
    handler = RetryHandler(RetryConfig(max_retries=2, base_delay_ms=10, jitter=False))
    with pytest.raises(ConnectionError):
        await handler.execute(always_fail)

# ── Timeout ──

@pytest.mark.asyncio
async def test_timeout_fires():
    async def slow(): await asyncio.sleep(10)
    handler = TimeoutHandler(TimeoutConfig(total_timeout_seconds=0.05))
    with pytest.raises(TimeoutError):
        await handler.execute(slow)

# ── Orchestrator ──

@pytest.mark.asyncio
async def test_orchestrator_success():
    async def ok(): return 42
    orch = ResilienceOrchestrator(
        CircuitBreaker(CircuitBreakerConfig(), name="t"),
        RetryHandler(RetryConfig(max_retries=1, base_delay_ms=10)),
        TimeoutHandler(TimeoutConfig(total_timeout_seconds=5.0)),
    )
    assert await orch.execute(ok) == 42

@pytest.mark.asyncio
async def test_orchestrator_retry_then_success():
    count = 0
    async def flaky():
        nonlocal count
        count += 1
        if count < 2: raise ConnectionError("tmp")
        return "recovered"
    
    orch = ResilienceOrchestrator(
        CircuitBreaker(CircuitBreakerConfig(failure_threshold=5), name="t"),
        RetryHandler(RetryConfig(max_retries=3, base_delay_ms=10, jitter=False)),
        TimeoutHandler(TimeoutConfig(total_timeout_seconds=5.0)),
    )
    assert await orch.execute(flaky) == "recovered"

# ── Factory ──

def test_get_handler_singleton():
    h1 = get_resilience_handler("worker")
    h2 = get_resilience_handler("worker")
    assert h1 is h2

def test_get_handler_different_tiers():
    h1 = get_resilience_handler("worker")
    h2 = get_resilience_handler("analyst")
    assert h1 is not h2

# ── Concurrency (stress) ──

@pytest.mark.asyncio
async def test_concurrent_cb_access():
    """50 concurrent calls не должны вызывать race condition."""
    cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=100), name="stress")
    async def task():
        if await cb.can_execute():
            await cb.record_success()
    await asyncio.gather(*[task() for _ in range(50)])
    assert cb.current_state == CircuitState.CLOSED

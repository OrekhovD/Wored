"""Tests for resilience patterns."""
import pytest
import asyncio
import time

from providers.resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    CircuitBreakerError,
    RetryHandler,
    RetryConfig,
    TimeoutHandler,
    TimeoutConfig,
    ResilienceOrchestrator,
)


@pytest.mark.asyncio
async def test_circuit_breaker_closed_state():
    """Circuit breaker starts in closed state."""
    config = CircuitBreakerConfig(failure_threshold=3, recovery_timeout=10)
    cb = CircuitBreaker(config, "test")
    assert cb.current_state == CircuitState.CLOSED
    assert await cb.can_execute() is True


@pytest.mark.asyncio
async def test_circuit_breaker_transitions_to_open():
    """Circuit breaker opens after threshold failures."""
    config = CircuitBreakerConfig(failure_threshold=3, recovery_timeout=10)
    cb = CircuitBreaker(config, "test")
    
    async def failing_func():
        raise RuntimeError("Test failure")
    
    # Should succeed 2 times, then fail
    for i in range(2):
        try:
            await cb.execute(failing_func)
        except RuntimeError:
            pass
    
    assert cb.current_state == CircuitState.CLOSED
    
    # 3rd failure should open circuit
    try:
        await cb.execute(failing_func)
    except RuntimeError:
        pass
    
    assert cb.current_state == CircuitState.OPEN
    assert await cb.can_execute() is False


@pytest.mark.asyncio
async def test_circuit_breaker_records_success():
    """Circuit breaker records successes."""
    config = CircuitBreakerConfig(failure_threshold=3, recovery_timeout=10)
    cb = CircuitBreaker(config, "test")
    
    async def success_func():
        return "success"
    
    result = await cb.execute(success_func)
    assert result == "success"
    assert cb.current_state == CircuitState.CLOSED
    
    stats = cb.get_stats()
    assert stats["failure_count"] == 0


@pytest.mark.asyncio
async def test_circuit_breaker_transitions_half_open():
    """Circuit breaker transitions to half-open after recovery timeout."""
    config = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=1)
    cb = CircuitBreaker(config, "test")
    
    async def failing_func():
        raise RuntimeError("Test failure")
    
    # Open circuit
    try:
        await cb.execute(failing_func)
    except RuntimeError:
        pass
    
    assert cb.current_state == CircuitState.OPEN
    
    # Wait for recovery timeout
    await asyncio.sleep(1.1)
    await cb._check_state()
    assert cb.current_state == CircuitState.HALF_OPEN
    
    stats = cb.get_stats()
    assert stats["state"] == "half_open"
    assert stats["half_open_calls"] == 0


@pytest.mark.asyncio
async def test_circuit_breaker_recovers_from_half_open():
    """Circuit breaker closes after success in half-open."""
    config = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=1, half_open_max_calls=2)
    cb = CircuitBreaker(config, "test")
    
    async def failing_func():
        raise RuntimeError("Test failure")
    async def success_func():
        return "success"
    
    # Open circuit
    try:
        await cb.execute(failing_func)
    except RuntimeError:
        pass
    
    # Wait for recovery timeout
    await asyncio.sleep(1.1)
    
    await cb._check_state()
    assert cb.current_state == CircuitState.HALF_OPEN
    
    # Try while in half-open state
    result = await cb.execute(success_func)
    assert result == "success"
    
    # Circuit should still be in half-open
    assert cb.current_state == CircuitState.HALF_OPEN
    
    # Second success should close circuit
    result = await cb.execute(success_func)
    assert result == "success"
    assert cb.current_state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_retry_handler_retries_failing_function():
    """Retry handler retries failing function."""
    config = RetryConfig(max_retries=2, base_delay_ms=100)
    retry = RetryHandler(config)
    
    call_count = [0]
    
    async def failing_func():
        call_count[0] += 1
        if call_count[0] < 3:
            raise RuntimeError(f"Failed {call_count[0]}")
        return "success"
    
    result = await retry.execute(failing_func)
    assert result == "success"
    assert call_count[0] == 3


@pytest.mark.asyncio
async def test_retry_handler_respects_retryable_exceptions():
    """Retry handler only retries specified exceptions."""
    config = RetryConfig(
        max_retries=2,
        base_delay_ms=100,
        retryable_exceptions=(RuntimeError, ValueError)
    )
    retry = RetryHandler(config)
    
    call_count = [0]
    
    async def failing_func():
        call_count[0] += 1
        if call_count[0] < 3:
            raise KeyError(f"Not retryable {call_count[0]}")
        return "success"
    
    # Should fail immediately because KeyError is not retryable
    try:
        await retry.execute(failing_func)
    except KeyError:
        pass
    
    assert call_count[0] == 1


@pytest.mark.asyncio
async def test_timeout_handler_raises_timeout():
    """Timeout handler raises timeout for slow functions."""
    config = TimeoutConfig(total_timeout_seconds=0.5)
    timeout = TimeoutHandler(config)
    
    async def slow_func():
        await asyncio.sleep(1)
        return "success"
    
    try:
        await timeout.execute(slow_func)
    except asyncio.TimeoutError:
        assert True
    else:
        assert False, "Timeout should have been raised"


@pytest.mark.asyncio
async def test_resilience_orchestrator_combines_patterns():
    """Resilience orchestrator combines all patterns."""
    circuit_config = CircuitBreakerConfig(failure_threshold=3, recovery_timeout=10)
    retry_config = RetryConfig(max_retries=2, base_delay_ms=100)
    timeout_config = TimeoutConfig(total_timeout_seconds=10)
    
    circuit = CircuitBreaker(circuit_config, "test")
    retry = RetryHandler(retry_config)
    timeout = TimeoutHandler(timeout_config)
    
    orchestrator = ResilienceOrchestrator(circuit, retry, timeout)
    
    call_count = [0]
    
    async def test_func():
        call_count[0] += 1
        if call_count[0] < 2:
            raise RuntimeError(f"Failed {call_count[0]}")
        return f"success {call_count[0]}"
    
    result = await orchestrator.execute(test_func)
    assert result == "success 2"
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_circuit_breaker_error_message():
    """Circuit breaker error has clear message."""
    config = CircuitBreakerConfig(failure_threshold=1, recovery_timeout=10)
    cb = CircuitBreaker(config, "test_provider")
    
    async def failing_func():
        raise RuntimeError("Test failure")
    
    # Open circuit
    try:
        await cb.execute(failing_func)
    except RuntimeError:
        pass
    
    assert cb.current_state == CircuitState.OPEN
    assert await cb.can_execute() is False
    
    async def safe_func():
        return "should not run"
    
    try:
        await cb.execute(safe_func)
    except CircuitBreakerError as e:
        assert "test_provider" in str(e)


@pytest.mark.asyncio
async def test_circuit_breaker_stats():
    """Circuit breaker stats are accurate."""
    config = CircuitBreakerConfig(failure_threshold=2, recovery_timeout=10)
    cb = CircuitBreaker(config, "test")
    
    stats = cb.get_stats()
    assert stats["name"] == "test"
    assert stats["state"] == "closed"
    assert stats["failure_count"] == 0
    assert stats["success_count"] == 0
    
    async def success_func():
        return "success"
    
    await cb.execute(success_func)
    
    stats = cb.get_stats()
    assert stats["success_count"] == 0
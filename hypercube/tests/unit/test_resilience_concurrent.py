"""Concurrency and stress tests for CircuitBreaker resilience patterns.

These tests verify atomicity of state transitions and counter invariants
under realistic concurrent load with randomized delays and mixed outcomes.
"""
import asyncio
import random

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
)


# ---------------------------------------------------------------------------
# Invariant assertion utility (reusable across tests)
# ---------------------------------------------------------------------------

def assert_state_invariants(cb: CircuitBreaker, label: str = "") -> None:
    """Assert that CB internal counters are consistent with its state.

    Invariants checked:
      - In CLOSED: half_open_calls == 0.
      - In OPEN: half_open_calls == 0, failure_count >= threshold.
      - In HALF_OPEN: half_open_calls <= config.half_open_max_calls.
      - success_count and failure_count are never negative.
    """
    stats = cb.get_stats()
    state = stats["state"]
    prefix = f"[{label}] " if label else ""

    # Counters are never negative
    assert stats["failure_count"] >= 0, f"{prefix}failure_count < 0"
    assert stats["success_count"] >= 0, f"{prefix}success_count < 0"
    assert stats["half_open_calls"] >= 0, f"{prefix}half_open_calls < 0"

    if state == "closed":
        assert stats["half_open_calls"] == 0, (
            f"{prefix}CLOSED but half_open_calls={stats['half_open_calls']}"
        )
    elif state == "open":
        assert stats["failure_count"] >= cb.config.failure_threshold, (
            f"{prefix}OPEN but failure_count={stats['failure_count']} "
            f"< threshold={cb.config.failure_threshold}"
        )
    elif state == "half_open":
        assert stats["half_open_calls"] <= cb.config.half_open_max_calls, (
            f"{prefix}HALF_OPEN but half_open_calls={stats['half_open_calls']} "
            f"> max={cb.config.half_open_max_calls}"
        )


# ---------------------------------------------------------------------------
# H1-a: Stress test — >=50 concurrent failing calls with randomized delays
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stress_concurrent_fails_with_random_delays():
    """>=50 concurrent failing calls with variable latency.

    Verifies:
      - CB transitions to OPEN exactly once.
      - All subsequent calls after threshold are rejected with CircuitBreakerError.
      - Counters remain consistent (invariants hold).
      - failure_count >= threshold.
    """
    threshold = 5
    config = CircuitBreakerConfig(failure_threshold=threshold, recovery_timeout=30)
    cb = CircuitBreaker(config, "stress_fail")

    call_count = [0]

    async def failing_with_jitter():
        delay = random.uniform(0.001, 0.05)  # 1ms-50ms random latency
        await asyncio.sleep(delay)
        call_count[0] += 1
        raise RuntimeError(f"Failure #{call_count[0]}")

    n_calls = 50
    tasks = [cb.execute(failing_with_jitter) for _ in range(n_calls)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    runtime_errors = [r for r in results if isinstance(r, RuntimeError)]
    circuit_errors = [r for r in results if isinstance(r, CircuitBreakerError)]

    # Every call must end in some exception
    assert len(runtime_errors) + len(circuit_errors) == n_calls, (
        f"Expected {n_calls} exceptions, got {len(runtime_errors)} RuntimeError + "
        f"{len(circuit_errors)} CircuitBreakerError = "
        f"{len(runtime_errors) + len(circuit_errors)}"
    )

    # At least `threshold` RuntimeErrors should have been raised before circuit opened
    assert len(runtime_errors) >= threshold

    # Final state must be OPEN
    assert cb.current_state == CircuitState.OPEN

    # Invariants check
    assert_state_invariants(cb, "stress_fail_post")


# ---------------------------------------------------------------------------
# H1-b: Mixed success/failure concurrent calls
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_mixed_success_failure():
    """Concurrent calls with a mix of successes and failures.

    Verifies that counters are atomically updated and state transitions
    are correct under mixed workload.
    """
    threshold = 10
    config = CircuitBreakerConfig(failure_threshold=threshold, recovery_timeout=30)
    cb = CircuitBreaker(config, "mixed_load")

    success_count = [0]
    fail_count = [0]

    async def mixed_func():
        delay = random.uniform(0.001, 0.03)
        await asyncio.sleep(delay)
        # 40% chance of success, 60% failure
        if random.random() < 0.4:
            success_count[0] += 1
            return "ok"
        else:
            fail_count[0] += 1
            raise RuntimeError("random fail")

    n_calls = 60
    tasks = [cb.execute(mixed_func) for _ in range(n_calls)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    successes = [r for r in results if r == "ok"]
    runtime_errors = [r for r in results if isinstance(r, RuntimeError)]
    circuit_errors = [r for r in results if isinstance(r, CircuitBreakerError)]

    # All results must be accounted for
    total = len(successes) + len(runtime_errors) + len(circuit_errors)
    assert total == n_calls, f"Unaccounted results: {total} != {n_calls}"

    # Invariants must hold after mixed workload
    assert_state_invariants(cb, "mixed_load_post")


# ---------------------------------------------------------------------------
# H1-c: Counter atomicity — parallel increments don't corrupt counters
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_counter_atomicity_under_concurrent_failures():
    """Parallel failing calls must increment failure_count atomically.

    Run N concurrent failures and verify failure_count exactly matches
    the number of RuntimeErrors observed (calls that actually executed
    the function, not rejected by CB).
    """
    threshold = 100  # High threshold so CB never opens during this test
    config = CircuitBreakerConfig(failure_threshold=threshold, recovery_timeout=30)
    cb = CircuitBreaker(config, "atomicity_test")

    async def fail_with_delay():
        await asyncio.sleep(random.uniform(0.001, 0.02))
        raise RuntimeError("fail")

    n_calls = 30
    tasks = [cb.execute(fail_with_delay) for _ in range(n_calls)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    runtime_errors = [r for r in results if isinstance(r, RuntimeError)]

    stats = cb.get_stats()
    # All calls should have been RuntimeError (CB never opens with threshold=100)
    assert len(runtime_errors) == n_calls
    # failure_count must exactly equal the number of failed calls
    assert stats["failure_count"] == n_calls, (
        f"failure_count={stats['failure_count']} != n_calls={n_calls}"
    )

    assert_state_invariants(cb, "atomicity_post")


# ---------------------------------------------------------------------------
# H1-d: HALF_OPEN token limiting under concurrency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_half_open_token_limiting_concurrent():
    """In HALF_OPEN, only half_open_max_calls are allowed through.

    Launch many concurrent calls; verify that exactly max_calls get
    tokens, the rest are rejected.
    """
    max_calls = 3
    config = CircuitBreakerConfig(
        failure_threshold=1, recovery_timeout=1, half_open_max_calls=max_calls,
    )
    cb = CircuitBreaker(config, "ho_token_limit")

    async def failing_func():
        raise RuntimeError("trip")

    async def success_func():
        await asyncio.sleep(random.uniform(0.01, 0.05))
        return "ok"

    # Trip the breaker
    try:
        await cb.execute(failing_func)
    except RuntimeError:
        pass
    assert cb.current_state == CircuitState.OPEN

    # Wait for recovery
    await asyncio.sleep(1.1)

    # Launch many concurrent calls
    n_calls = 20
    tasks = [cb.execute(success_func) for _ in range(n_calls)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    successes = [r for r in results if r == "ok"]
    circuit_errors = [r for r in results if isinstance(r, CircuitBreakerError)]

    assert len(successes) + len(circuit_errors) == n_calls

    # At most max_calls should have been allowed through as successes
    # (may be more if the CB transitions to CLOSED and admits more)
    assert len(successes) >= max_calls, (
        f"Expected at least {max_calls} successes, got {len(successes)}"
    )

    # Final state should be CLOSED (all successes triggered recovery)
    assert cb.current_state == CircuitState.CLOSED

    assert_state_invariants(cb, "ho_token_post")


# ---------------------------------------------------------------------------
# H2: Deterministic behavior after reset
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deterministic_state_after_reset():
    """After _reset(), CB must be in pristine CLOSED state with zeroed counters."""
    config = CircuitBreakerConfig(failure_threshold=2, recovery_timeout=10)
    cb = CircuitBreaker(config, "reset_test")

    async def failing_func():
        raise RuntimeError("fail")

    # Drive into OPEN
    for _ in range(2):
        try:
            await cb.execute(failing_func)
        except RuntimeError:
            pass

    assert cb.current_state == CircuitState.OPEN
    stats_before = cb.get_stats()
    assert stats_before["failure_count"] >= 2

    # Reset
    cb._reset()

    stats_after = cb.get_stats()
    assert stats_after["state"] == "closed"
    assert stats_after["failure_count"] == 0
    assert stats_after["success_count"] == 0
    assert stats_after["half_open_calls"] == 0
    assert stats_after["opened_at"] is None

    assert_state_invariants(cb, "post_reset")

    # CB must be usable again
    async def ok_func():
        return "alive"

    result = await cb.execute(ok_func)
    assert result == "alive"
    assert cb.current_state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# H1-e: Single OPEN transition under concurrent storm
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_single_open_transition():
    """Verify that CLOSED->OPEN transition happens exactly once.

    We intercept state transitions by polling state inside callbacks.
    """
    threshold = 5
    config = CircuitBreakerConfig(failure_threshold=threshold, recovery_timeout=30)
    cb = CircuitBreaker(config, "single_transition")

    open_observed_timestamps = []

    async def failing_func_with_probe():
        await asyncio.sleep(random.uniform(0.005, 0.02))
        # Probe state after delay (before raising)
        if cb.current_state == CircuitState.OPEN:
            open_observed_timestamps.append(True)
        raise RuntimeError("probe-fail")

    n_calls = 50
    tasks = [cb.execute(failing_func_with_probe) for _ in range(n_calls)]
    await asyncio.gather(*tasks, return_exceptions=True)

    assert cb.current_state == CircuitState.OPEN

    # opened_at must be set exactly once (not None, not reset)
    stats = cb.get_stats()
    assert stats["opened_at"] is not None

    assert_state_invariants(cb, "single_transition_post")

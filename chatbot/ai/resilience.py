"""Resilience patterns: Circuit Breaker, Retry, Timeout for provider calls."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing, reject immediately
    HALF_OPEN = "half_open" # Testing if recovered


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration."""
    failure_threshold: int = 5          # Failures before opening
    recovery_timeout: float = 60.0      # Seconds before trying again
    half_open_max_calls: int = 3        # Test calls in half-open state
    expected_exception_types: tuple | None = None  # Exceptions that count as failures


@dataclass
class RetryConfig:
    """Retry configuration."""
    max_retries: int = 3
    base_delay_ms: int = 1000
    max_delay_ms: int = 30000
    exponential_backoff: bool = True
    jitter: bool = True
    retryable_exceptions: tuple | None = None


@dataclass
class TimeoutConfig:
    """Timeout configuration."""
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 30.0
    total_timeout_seconds: float = 60.0


@dataclass
class CircuitBreakerState:
    """Internal circuit breaker state."""
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float | None = None
    half_open_calls: int = 0
    opened_at: float | None = None


class CircuitBreakerError(Exception):
    """Circuit breaker is open, request rejected."""
    pass


class CircuitBreaker:
    """Circuit breaker pattern implementation.
    
    Prevents cascading failures by failing fast when a service is known to be down.
    """

    def __init__(self, config: CircuitBreakerConfig, name: str = "default") -> None:
        self.config = config
        self.name = name
        self._state = CircuitBreakerState()
        self._lock = asyncio.Lock()

    @property
    def current_state(self) -> CircuitState:
        return self._state.state

    async def _check_state(self) -> None:
        """Check and potentially transition circuit state."""
        async with self._lock:
            if self._state.state == CircuitState.OPEN:
                # Check if recovery timeout has passed
                if self._state.opened_at:
                    elapsed = time.monotonic() - self._state.opened_at
                    if elapsed >= self.config.recovery_timeout:
                        self._state.state = CircuitState.HALF_OPEN
                        self._state.half_open_calls = 0
                        log.info("Circuit %s transitioning to HALF_OPEN", self.name)

    async def can_execute(self) -> bool:
        """Check if request can be executed."""
        await self._check_state()
        
        async with self._lock:
            if self._state.state == CircuitState.CLOSED:
                return True
            
            if self._state.state == CircuitState.HALF_OPEN:
                if self._state.half_open_calls < self.config.half_open_max_calls:
                    self._state.half_open_calls += 1
                    return True
                return False
            
            # OPEN state
            return False

    async def record_success(self) -> None:
        """Record a successful call."""
        async with self._lock:
            if self._state.state == CircuitState.HALF_OPEN:
                self._state.success_count += 1
                if self._state.success_count >= self.config.half_open_max_calls:
                    self._reset()
                    log.info("Circuit %s closed after recovery", self.name)
            elif self._state.state == CircuitState.CLOSED:
                self._state.failure_count = 0

    async def record_failure(self) -> None:
        """Record a failed call."""
        async with self._lock:
            self._state.failure_count += 1
            self._state.last_failure_time = time.monotonic()
            
            if self._state.state == CircuitState.HALF_OPEN:
                # Immediate trip back to open on failure in half-open
                self._state.state = CircuitState.OPEN
                self._state.opened_at = time.monotonic()
                log.warning("Circuit %s reopened after failure in HALF_OPEN", self.name)
            elif self._state.state == CircuitState.CLOSED:
                if self._state.failure_count >= self.config.failure_threshold:
                    self._state.state = CircuitState.OPEN
                    self._state.opened_at = time.monotonic()
                    log.warning("Circuit %s opened after %d failures", self.name, self._state.failure_count)

    def _reset(self) -> None:
        """Reset circuit to closed state."""
        self._state.state = CircuitState.CLOSED
        self._state.failure_count = 0
        self._state.success_count = 0
        self._state.half_open_calls = 0
        self._state.opened_at = None

    async def execute(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection."""
        if not await self.can_execute():
            raise CircuitBreakerError(f"Circuit {self.name} is open")
        
        try:
            result = await func(*args, **kwargs)
            await self.record_success()
            return result
        except Exception as e:
            await self.record_failure()
            raise

    def get_stats(self) -> dict[str, Any]:
        """Get circuit breaker statistics."""
        return {
            "name": self.name,
            "state": self._state.state.value,
            "failure_count": self._state.failure_count,
            "success_count": self._state.success_count,
            "half_open_calls": self._state.half_open_calls,
            "opened_at": self._state.opened_at,
            "last_failure_time": self._state.last_failure_time,
        }


class RetryHandler:
    """Retry logic with exponential backoff and jitter."""

    def __init__(self, config: RetryConfig) -> None:
        self.config = config

    async def execute(
        self,
        func: Callable[..., Any],
        *args,
        **kwargs,
    ) -> Any:
        """Execute function with retry logic."""
        last_exception: Exception | None = None

        for attempt in range(self.config.max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                
                # Check if we should retry this exception
                if self.config.retryable_exceptions:
                    if not isinstance(e, self.config.retryable_exceptions):
                        raise
                
                if attempt >= self.config.max_retries:
                    break
                
                # Calculate delay
                delay = self._calculate_delay(attempt)
                log.warning(
                    "Attempt %d/%d failed: %s. Retrying in %.2fs",
                    attempt + 1, self.config.max_retries, e, delay
                )
                await asyncio.sleep(delay)

        raise last_exception

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay with exponential backoff and optional jitter."""
        if self.config.exponential_backoff:
            delay = self.config.base_delay_ms * (2 ** attempt) / 1000
        else:
            delay = self.config.base_delay_ms / 1000
        
        # Cap at max delay
        delay = min(delay, self.config.max_delay_ms / 1000)
        
        # Add jitter
        if self.config.jitter:
            import random
            delay *= (0.5 + random.random())
        
        return delay


class TimeoutHandler:
    """Timeout enforcement for async operations."""

    def __init__(self, config: TimeoutConfig) -> None:
        self.config = config

    async def execute(
        self,
        func: Callable[..., Any],
        *args,
        timeout_seconds: float | None = None,
        **kwargs,
    ) -> Any:
        """Execute function with timeout enforcement."""
        effective_timeout = timeout_seconds or self.config.total_timeout_seconds
        
        try:
            return await asyncio.wait_for(
                func(*args, **kwargs),
                timeout=effective_timeout
            )
        except asyncio.TimeoutError:
            raise TimeoutError(f"Operation timed out after {effective_timeout}s")


class ResilienceOrchestrator:
    """Combines circuit breaker, retry, and timeout patterns."""

    def __init__(
        self,
        circuit_breaker: CircuitBreaker,
        retry_handler: RetryHandler,
        timeout_handler: TimeoutHandler,
    ) -> None:
        self.circuit_breaker = circuit_breaker
        self.retry_handler = retry_handler
        self.timeout_handler = timeout_handler

    async def execute(
        self,
        func: Callable[..., Any],
        *args,
        **kwargs,
    ) -> Any:
        """Execute with full resilience stack: timeout -> circuit breaker -> retry."""
        # Wrap with circuit breaker
        async def cb_wrapped():
            return await self.circuit_breaker.execute(
                self.timeout_handler.execute,
                func,
                *args,
                **kwargs,
            )
        
        # Apply retry on top
        return await self.retry_handler.execute(cb_wrapped)

    def get_circuit_stats(self) -> dict[str, Any]:
        """Get circuit breaker stats."""
        return self.circuit_breaker.get_stats()


# Global registry for per-provider resilience handlers
_resilience_handlers: dict[str, ResilienceOrchestrator] = {}


# Конфигурация per-tier (соответствует models.py)
_TIER_CONFIGS = {
    "worker":  {"timeout": 15.0,  "retries": 1, "cb_threshold": 8},
    "worker_qwen35": {"timeout": 15.0, "retries": 1, "cb_threshold": 8},
    "worker_qwen_legacy": {"timeout": 15.0, "retries": 1, "cb_threshold": 8},
    "worker_glm": {"timeout": 15.0, "retries": 1, "cb_threshold": 8},
    "worker_gemini": {"timeout": 15.0, "retries": 1, "cb_threshold": 8},
    "analyst": {"timeout": 65.0,  "retries": 2, "cb_threshold": 5},
    "analyst_qwen27b": {"timeout": 65.0, "retries": 2, "cb_threshold": 5},
    "analyst_qwen_extra": {"timeout": 65.0, "retries": 2, "cb_threshold": 5},
    "analyst_glm": {"timeout": 65.0, "retries": 2, "cb_threshold": 5},
    "premium": {"timeout": 95.0,  "retries": 2, "cb_threshold": 5},
    "premium_qwen35b": {"timeout": 95.0, "retries": 2, "cb_threshold": 5},
    "premium_glm": {"timeout": 95.0, "retries": 2, "cb_threshold": 5},
    # Second-opinion routing should fail fast and move on when MiniMax/NIM is slow or unavailable.
    "minimax": {"timeout": 12.0,  "retries": 0, "cb_threshold": 2},
}

def get_resilience_handler(provider_id: str) -> ResilienceOrchestrator:
    """Get or create resilience handler for a provider with tier-specific config."""
    if provider_id not in _resilience_handlers:
        tier_cfg = _TIER_CONFIGS.get(provider_id, {})
        
        config = CircuitBreakerConfig(
            failure_threshold=tier_cfg.get("cb_threshold", 5),
            recovery_timeout=60.0,
            half_open_max_calls=3,
        )
        retry_config = RetryConfig(
            max_retries=tier_cfg.get("retries", 2),
            base_delay_ms=1000,
            max_delay_ms=10000,
            exponential_backoff=True,
            jitter=True,
        )
        timeout_config = TimeoutConfig(
            total_timeout_seconds=tier_cfg.get("timeout", 60.0),
        )
        
        _resilience_handlers[provider_id] = ResilienceOrchestrator(
            circuit_breaker=CircuitBreaker(config, name=provider_id),
            retry_handler=RetryHandler(retry_config),
            timeout_handler=TimeoutHandler(timeout_config),
        )
    
    return _resilience_handlers[provider_id]


def reset_resilience_handlers() -> None:
    """Reset all resilience handlers (useful for testing)."""
    _resilience_handlers.clear()

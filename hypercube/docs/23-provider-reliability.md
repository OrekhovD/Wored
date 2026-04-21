# Provider Reliability, Timeouts, Retries, Circuit Breakers

## 1. Overview

Система устойчивости обеспечивает надежные вызовы к AI-провайдерам через три основных паттерна:
- **Circuit Breaker**: Предотвращает каскадные отказы при недоступности провайдера
- **Retry**: Автоматические повторы при временных сбоях с экспоненциальной задержкой
- **Timeout**: Принудительное завершение зависших запросов

## 2. Circuit Breaker Pattern

### 2.1 States

```
┌─────────────┐
│   CLOSED    │ ← Normal operation, requests pass through
└──────┬──────┘
       │ failure_threshold failures
       ▼
┌─────────────┐
│    OPEN     │ ← Requests rejected immediately
└──────┬──────┘
       │ recovery_timeout elapsed
       ▼
┌─────────────┐
│  HALF_OPEN  │ ← Testing recovery (limited calls)
└──────┬──────┘
       │ half_open_max_calls successes
       ▼
   (CLOSED again)
```

### 2.2 Configuration

```python
CircuitBreakerConfig(
    failure_threshold=5,          # Failures before opening
    recovery_timeout=60.0,        # Seconds before trying again
    half_open_max_calls=3,        # Test calls in half-open state
)
```

### 2.3 Behavior

- **CLOSED**: Все запросы проходят, ошибки подсчитываются
- **OPEN**: Запросы отклоняются сразу, не тратятся ресурсы
- **HALF_OPEN**: Ограниченное число тестовых запросов для проверки восстановления

## 3. Retry Pattern

### 3.1 Configuration

```python
RetryConfig(
    max_retries=3,                # Maximum retry attempts
    base_delay_ms=1000,           # Initial delay
    max_delay_ms=30000,           # Cap for backoff
    exponential_backoff=True,     # Double delay each attempt
    jitter=True,                  # Add randomness to prevent thundering herd
)
```

### 3.2 Delay Calculation

```
Attempt 1: base_delay * 2^0 * jitter = 1s ± 50%
Attempt 2: base_delay * 2^1 * jitter = 2s ± 50%
Attempt 3: base_delay * 2^2 * jitter = 4s ± 50%
...
Capped at max_delay_ms
```

## 4. Timeout Pattern

### 4.1 Configuration

```python
TimeoutConfig(
    connect_timeout_seconds=10.0,  # TCP connection timeout
    read_timeout_seconds=30.0,     # Response read timeout
    total_timeout_seconds=60.0,    # Overall operation timeout
)
```

### 4.2 Enforcement

Все вызовы провайдеров ограничиваются `total_timeout_seconds`. Превышение времени выбрасывает `asyncio.TimeoutError`.

## 5. ResilienceOrchestrator Stack

Порядок применения паттернов:

```
User Request
     │
     ▼
┌──────────────────┐
│   RetryHandler   │ ← Outermost: retries on failure
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ CircuitBreaker   │ ← Rejects if circuit is open
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  TimeoutHandler  │ ← Innermost: enforces time limit
└────────┬─────────┘
         │
         ▼
   Provider Call
```

## 6. Per-Provider Handlers

Каждый провайдер получает свой изолированный circuit breaker:

```python
from providers.resilience import get_resilience_handler

handler = get_resilience_handler("dashscope")
# Independent from "nvapi", "zhipu", etc.
```

## 7. Monitoring & Stats

### 7.1 Get Circuit Stats

```python
stats = handler.get_circuit_stats()
# {
#     "name": "dashscope",
#     "state": "closed",
#     "failure_count": 0,
#     "success_count": 15,
#     "half_open_calls": 0,
#     "opened_at": None,
#     "last_failure_time": 1713891234.567
# }
```

### 7.2 Logging

- Circuit opening: `WARNING` уровень
- Retry attempts: `WARNING` уровень
- Recovery: `INFO` уровень

## 8. Integration with OpenAICompatibleAdapter

Адаптеры автоматически используют resilience:

```python
class OpenAICompatibleAdapter(AIProviderInterface):
    def __init__(self, provider_id, ...):
        self._resilience = get_resilience_handler(provider_id)
    
    async def invoke(self, ...):
        return await self._resilience.execute(_do_request)
```

## 9. Fallback Behavior

При открытии circuit breaker:
1. Возвращается `ProviderUnavailableError`
2. `FallbackEngine` переключается на следующую модель в цепочке
3. Пользователь видит ответ от резервного провайдера

## 10. Testing Commands

```bash
# Unit tests for resilience patterns
pytest -m unit tests/test_resilience.py

# Check circuit stats during runtime
curl http://localhost:8000/internal/providers/{provider_id}/circuit-stats
```

## 11. Tuning Recommendations

| Сценарий | failure_threshold | recovery_timeout | max_retries |
|----------|-------------------|------------------|-------------|
| Быстрый fallback | 3 | 30s | 1 |
| Строгий режим | 5 | 60s | 2 |
| Гибкий режим | 10 | 120s | 3 |

## 12. File Locations

| Файл | Назначение |
|------|-----------|
| `providers/resilience.py` | CircuitBreaker, Retry, Timeout, Orchestrator |
| `providers/openai_compatible.py` | Интеграция с адаптером |
| `routing/fallback_engine.py` | Переключение при отказах |

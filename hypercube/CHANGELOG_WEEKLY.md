# Changelog Weekly

Еженедельный журнал изменений проекта Hypercube (Telegram AI Gateway для HTX).

## 2026-04-22 — QA Audit Remediation (ТЗ-09 Rev.2)

### 🐛 Bugfixes (Critical)

**FIX-001: Race condition в CircuitBreaker.can_execute()**
- **Файл:** `providers/resilience.py` (строка 94–109)
- **Суть:** Метод `can_execute()` мутировал `half_open_calls` без `asyncio.Lock`. При параллельных вызовах несколько корутин могли одновременно прочитать `half_open_calls < max` и все пройти, превысив лимит.
- **Исправление:** Обернули всю проверку и инкремент в `async with self._lock`.
- **Rollback:** Убрать `async with self._lock:` блок в `can_execute()`, вернуть плоскую проверку.

**FIX-002: UnboundLocalError в тестах resilience**
- **Файл:** `tests/unit/test_resilience.py` (строки 141, 164, 212)
- **Суть:** `call_count += 1` внутри вложенных `async def` без `nonlocal` вызывал `UnboundLocalError`.
- **Исправление:** Перевод на мутабельный список `call_count = [0]`, инкремент `call_count[0] += 1`.

**FIX-003: Тест transitions_half_open не вызывал `_check_state()`**
- **Файл:** `tests/unit/test_resilience.py` (строка 92)
- **Суть:** Тест проверял `current_state` после `asyncio.sleep()`, но состояние не обновлялось без вызова `_check_state()`.
- **Исправление:** Добавлен `await cb._check_state()` перед проверкой.

### 🧪 Testing (New)

- ✅ `tests/unit/test_resilience_concurrent.py` — **6 тестов**:
  - `test_stress_concurrent_fails_with_random_delays` (>=50 concurrent, randomized latency)
  - `test_concurrent_mixed_success_failure` (60 calls, 40/60 success/fail mix)
  - `test_counter_atomicity_under_concurrent_failures` (30 parallel, exact counter check)
  - `test_half_open_token_limiting_concurrent` (20 parallel in HALF_OPEN)
  - `test_deterministic_state_after_reset` (reset → pristine state)
  - `test_single_open_transition` (verify single CLOSED→OPEN)

- ✅ `tests/unit/test_resilience_regression.py` — **5 тестов**:
  - `test_circuit_breaker_error_maps_to_provider_unavailable` (adapter boundary)
  - `test_orchestrator_full_cycle_retry_then_success` (full stack)
  - `test_orchestrator_circuit_opens_after_retry_exhaustion` (CB opens mid-retry)
  - `test_provider_manager_chain_building_regression` (routing unaffected)
  - `test_resilience_handlers_are_independent_per_provider` (per-provider isolation)

### 🏗️ Infrastructure

- ✅ Разделён `requirements.txt` → `requirements.txt` (runtime) + `requirements-dev.txt` (dev)
- ✅ Обновлён `Makefile`: добавлены targets `deps`, `typecheck`, `test-cov`
- ✅ Создан `docs/BACKLOG.md` с отложенной задачей Gemini Flash Adapter

### 📊 Coverage

| Модуль | Coverage |
|--------|----------|
| `providers/resilience.py` | **98%** (169/173 stmts) |
| `providers/interface.py` | 100% |
| `routing/chain_builder.py` | 100% |
| `routing/provider_manager.py` | 80% |

### ⚠️ Known Issues (pre-existing, not from this change)

- `test_handoff_schema_v1_serialization` fails: `datetime` not JSON serializable (pre-existing bug in `context/handoff.py:50`)
- `routing/service.py` has 0% coverage (not tested)
- `context/service.py` has 22% coverage (not tested)

---

## Week 17: 2026-04-21 — Release Hardening and Validation

### 🔐 Security (WP-2)

**SEC-001: Утечка ключей — ИСПРАВЛЕНО**
- ✅ `.env` добавлен в `.gitignore`
- ✅ `docs/kluch.txt` добавлен в `.gitignore`
- ✅ `settings.json` добавлен в `.gitignore`
- ✅ `.env.example` очищен от реальных ключей
- ✅ Создан `docs/14-security.md` с политиками безопасности
- ✅ Создан `docs/17-security-remediation.md` с отчетом об исправлении

**SEC-002: Архитектура core/ — ИСПРАВЛЕНО**
- ✅ Удалены `core/db.py`, `core/engine.py`, `core/logger.py`
- ✅ Удалены `core/context/` и `core/providers/`
- ✅ Модули перемещены в корень проекта

### 🎯 Config-Driven Routing (WP-3)

**PROVIDER-001: Конфигурация цепочек — РЕАЛИЗОВАНО**
- ✅ Создан `examples/provider_registry.yaml` с полной конфигурацией
- ✅ Обновлен `routing/policies.py` для чтения из YAML
- ✅ Создан `routing/chain_builder.py` для построения цепочек
- ✅ Создан `routing/provider_manager.py` для управления провайдерами
- ✅ Обновлен `routing/model_registry.py` для config-driven загрузки
- ✅ Добавлен PyYAML в `requirements.txt`
- ✅ Создана документация:
  - `docs/18-provider-validation-and-registry.md`
  - `docs/19-chain-builder.md`
  - `docs/20-provider-manager.md`
  - `docs/21-model-registry.md`

### 🧠 Context Handoff (WP-4)

**CONTEXT-001: Версионирование схемы — РЕАЛИЗОВАНО**
- ✅ Обновлен `context/handoff.py` с HandoffSchemaV1
- ✅ Добавлена валидация обязательных полей
- ✅ Добавлена сериализация/десериализация через JSON
- ✅ Добавлено форматирование системного сообщения
- ✅ Создана документация `docs/22-context-handoff-hardening.md`

### 🛡️ Provider Resilience (WP-5)

**RUNTIME-001: Устойчивость провайдеров — РЕАЛИЗОВАНО**
- ✅ Создан `providers/resilience.py` с паттернами:
  - CircuitBreaker (CLOSED, OPEN, HALF_OPEN)
  - RetryHandler (exponential backoff + jitter)
  - TimeoutHandler (asyncio.wait_for)
  - ResilienceOrchestrator (комбинация паттернов)
- ✅ Интегрировано в `providers/openai_compatible.py`
- ✅ Per-provider circuit breakers
- ✅ Создана документация `docs/23-provider-reliability.md`

### ✅ Quality Gates (WP-6)

**TEST-001: Тестовое покрытие — ЧАСТИЧНО РЕАЛИЗОВАНО**
- ✅ Созданы unit тесты:
  - `tests/unit/test_resilience.py` (12 тестов)
  - `tests/unit/test_handoff.py` (6 тестов)
  - `tests/unit/test_routing.py` (8 тестов)
- ✅ Созданы integration тесты:
  - `tests/integration/test_provider_framework.py` (6 тестов)
- ✅ Созданы smoke тесты:
  - `tests/smoke/test_smoke.py` (4 теста)
- ✅ Создана документация `docs/24-quality-gates-closure.md`

**ОСТАЮЩИЕСЯ ЗАДАЧИ:**
- ❌ Unit тесты для `providers/openai_compatible.py`
- ❌ Unit тесты для `context/service.py`
- ❌ Unit тесты для `bot/handlers.py`
- ❌ Integration тесты для HTX adapter
- ❌ Mock провайдеров для тестирования
- ❌ Тесты для accounting/quotas модулей

### 📚 Documentation (WP-7)

**DOC-001: Нормализация документации — РЕАЛИЗОВАНО**
- ✅ Создан `docs/INDEX.md` — централизованный индекс
- ✅ Создан `docs/KNOWN_LIMITS.md` — известные ограничения
- ✅ Создан `docs/PROVIDER_DIFF.md` — сравнение провайдеров
- ✅ Создан `docs/DEPRECATIONS.md` — устаревшие функции
- ✅ Создан `scripts/fix_encoding.py` — исправление кодировки UTF-8

### 📊 Статистика недели

| Метрика | Значение |
|---------|----------|
| WP завершено | 6 из 8 |
| Файлов создано | 20+ |
| Файлов обновлено | 10+ |
| Тестов написано | 36 |
| Документов создано | 10 |
| Проблем безопасности исправлено | 5 |

### 🎯 Go/No-Go статус

**GO с ограничениями:**

✅ Готово:
- Security hardening
- Config-driven routing
- Context handoff версионирование
- Provider resilience
- Unit/integration/smoke тесты
- Документация

❌ Не готово:
- Полное тестовое покрытие
- CI/CD пайплайн
- Интеграция с Telegram (тесты)
- HTX adapter тесты

**Рекомендация:** Продолжить WP-8 (Weekly Refresh) и подготовить release checklist.

---

## Week 16: 2026-04-14 — Initial Setup

### 📋 Задания
- ✅ Создан `TZ-QWENCODE-HTX-TELEGRAM-AI-GATEWAY.md`
- ✅ Создан `TZ-QWENCODE-02-RELEASE-HARDENING-AND-VALIDATION.md`

### 🏗️ Архитектура
- ✅ Создан `06-final-documentation.md`
- ✅ Создан `03-phase3-plan.md`

### 🔐 Безопасность
- ✅ Создан `14-security.md`
- ✅ Создан `16-gap-audit.md`

---

## 📅 Расписание еженедельных обновлений

Следующее обновление: **2026-04-28 09:00 Asia/Bangkok**

### План на Week 18 (2026-04-28)
- [ ] WP-8: Weekly Refresh Activation
- [ ] Release checklist и go/no-go report
- [ ] Добавить тесты для Telegram бота
- [ ] Добавить mock провайдеров
- [ ] Настроить CI/CD пайплайн

---

## 📝 Примечания

1. **Обновления публикуются** каждый понедельник в 09:00 Asia/Bangkok
2. **Изменения группируются** по WP (Work Package)
3. **Статус Go/No-Go** определяется перед каждым релизом
4. **Оставшиеся задачи** переносятся на следующую неделю

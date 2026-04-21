# Техническое задание 03 для QwenCode

## INTEGRATION PIPELINE — Склейка модулей в рабочую систему

**Дата:** 21.04.2026
**Репозиторий:** `D:\WORED\hypercube`
**Предыдущие ТЗ:** TZ-01 (MVP), TZ-02 (Hardening)
**Исполнитель:** QwenCode в терминале Antigravity

---

## 1. Назначение документа

Это ТЗ завершает интеграцию проекта Hypercube. Предыдущие этапы создали все модули как изолированные компоненты. Этот этап **склеивает их в рабочий end-to-end pipeline**.

Документ написан для QwenCode, который выполняет реализацию в терминале Antigravity под управлением Lead Agent.

---

## 2. Текущее состояние проекта (факт, подтверждён аудитом)

### 2.1 Что реально работает

- ORM-схема: 14 таблиц в `storage/models.py` (полная)
- Repositories: `storage/repositories.py` (CRUD для всех таблиц)
- AI Provider adapter: `providers/openai_compatible.py` с resilience
- HTX adapter: `providers/htx_adapter.py` (read-only, 6 endpoints)
- Context handoff: `context/handoff.py` (SchemaV1, validated)
- Quota engine: `quotas/engine.py` (логика проверки порогов)
- Accounting service: `accounting/service.py` (запись и агрегация usage)
- Config: `core/config.py` (Pydantic, все env vars)
- Routing: `routing/service.py`, `routing/fallback_engine.py`

### 2.2 Что НЕ работает (блокеры)

1. **Telegram-команды — stubs.** 7 из 13 команд возвращают хардкод, не обращаются к сервисам.
2. **`/ask` не подключён к pipeline.** Нет: проверки квот, HTX-обогащения, persist usage, persist route decision, обновления контекста.
3. **`/mode` не сохраняет выбранный режим.** Просто отвечает текстом.
4. **Ни одна таблица не записывается** из бизнес-логики.
5. **Routing service дублирует hardcoded chains** вместо YAML registry.
6. **`admin/` пустая** — нет реализации.
7. **`core/context/` и `core/providers/`** — пустые мусорные директории.
8. **Alembic migrations** — 0 миграций в `alembic/versions/`.
9. **HTX adapter** использует `time.sleep()` вместо `asyncio.sleep()` в async-контексте.

---

## 3. Цель этого этапа

**Превратить набор разрозненных компонентов в работающую систему**, где:

- `/ask` выполняет полный цикл: quota check → HTX enrich → route → invoke AI → persist usage → persist route decision → update context → reply
- все Telegram-команды читают реальные данные из БД
- `/mode` сохраняет режим в сессии
- `/switch_model` выполняет реальный handoff
- admin-команды показывают реальную статистику
- все events записываются в БД

---

## 4. Границы этапа

### 4.1 Что входит в scope

- Интеграция всех существующих модулей в единый pipeline
- Подключение Telegram-команд к реальным сервисам
- Session management (создание/хранение сессий пользователей)
- Запись usage, route decisions, context snapshots в БД
- Устранение технического долга (мусорные папки, hardcoded chains, async sleep)
- Создание alembic-миграции
- Реализация admin-модуля

### 4.2 Что НЕ входит в scope

- Новые AI-провайдеры или модели
- Торговые операции HTX
- CI/CD
- Multi-user / multi-tenant
- Документация ТЗ-01 §19 (отдельный этап)
- Переход на VPS

---

## 5. Непереговорные требования

- Не добавлять торговые методы HTX.
- Не менять ORM-модели без обновления alembic-миграции.
- Не оставлять stub-ответы с хардкоженными значениями.
- Не смешивать бизнес-логику с Telegram handlers — handlers остаются thin.
- Не ломать существующие тесты (36 тестов должны проходить).
- Каждый WP завершается командой `pytest` для проверки.

---

## 6. Архитектура интеграции

### 6.1 Service Layer (новый файл: `core/services.py`)

Создать **единый Service Locator**, который инициализируется при старте и передаётся в handlers.

```python
# core/services.py
"""Service container — initializes and holds all service instances."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from accounting.service import TokenAccountingService
from context.service import ContextService
from context.handoff import HandoffBuilder
from quotas.engine import QuotaPolicyEngine
from routing.service import FallbackEngine, ModelRegistry
from providers.htx_adapter import HTXMarketDataAdapter
from storage.repositories import (
    UsageRecordRepository,
    QuotaStateRepository,
    ConversationSessionRepository,
    ConversationMessageRepository,
    ContextSnapshotRepository,
    ContextHandoffRepository,
    RouteDecisionRepository,
    UserRepository,
    TelegramChatRepository,
    ProviderHealthEventRepository,
    HTXMarketSnapshotRepository,
    AdminEventRepository,
)


@dataclass
class ServiceContainer:
    """Holds all initialized services."""
    # repositories
    user_repo: UserRepository = field(default=None)
    chat_repo: TelegramChatRepository = field(default=None)
    session_repo: ConversationSessionRepository = field(default=None)
    message_repo: ConversationMessageRepository = field(default=None)
    usage_repo: UsageRecordRepository = field(default=None)
    quota_repo: QuotaStateRepository = field(default=None)
    snapshot_repo: ContextSnapshotRepository = field(default=None)
    handoff_repo: ContextHandoffRepository = field(default=None)
    route_repo: RouteDecisionRepository = field(default=None)
    health_repo: ProviderHealthEventRepository = field(default=None)
    htx_snapshot_repo: HTXMarketSnapshotRepository = field(default=None)
    admin_repo: AdminEventRepository = field(default=None)

    # services
    accounting: TokenAccountingService = field(default=None)
    quota_engine: QuotaPolicyEngine = field(default=None)
    context_service: ContextService = field(default=None)
    handoff_builder: HandoffBuilder = field(default=None)
    model_registry: ModelRegistry = field(default=None)
    fallback_engine: FallbackEngine = field(default=None)
    htx_adapter: HTXMarketDataAdapter = field(default=None)

    # provider adapters
    provider_adapters: dict[str, Any] = field(default_factory=dict)

    # user session state (in-memory, per telegram_user_id)
    user_sessions: dict[int, "UserSessionState"] = field(default_factory=dict)


@dataclass
class UserSessionState:
    """Per-user in-memory session state."""
    telegram_user_id: int
    current_mode: str = "free_only"
    current_model: str = ""
    session_id: str = ""
    conversation_id: str = ""
```

### 6.2 Инициализация в `app/main.py`

В функции `lifespan()` после инициализации БД:

1. Создать все repositories с текущей DB session factory
2. Создать все services, передав repositories
3. Создать provider adapters из `config.provider_configs`
4. Собрать `ServiceContainer`
5. Передать в `setup_bot(bot, config, container)`

### 6.3 Handler pattern

Каждый handler в `bot/handlers.py` получает доступ к `ServiceContainer` через глобальную переменную `_services: ServiceContainer`.

```python
# Пример thin handler:
@router.message(Command("usage"))
async def cmd_usage(message: Message) -> None:
    user_id = message.from_user.id
    try:
        day = await _services.accounting.get_user_usage(user_id, "day")
        week = await _services.accounting.get_user_usage(user_id, "week")
        month = await _services.accounting.get_user_usage(user_id, "month")
        text = (
            f"📊 **Использование (user {user_id}):**\n\n"
            f"За день: {day.get('requests', 0)} запросов, {day.get('total_tokens', 0)} токенов, ${day.get('cost', 0):.4f}\n"
            f"За неделю: {week.get('requests', 0)} запросов, {week.get('total_tokens', 0)} токенов, ${week.get('cost', 0):.4f}\n"
            f"За месяц: {month.get('requests', 0)} запросов, {month.get('total_tokens', 0)} токенов, ${month.get('cost', 0):.4f}"
        )
    except Exception as e:
        text = f"❌ Ошибка получения статистики: {str(e)[:200]}"
    await message.answer(text, parse_mode="Markdown")
```

---

## 7. Рабочие пакеты

---

### WP-1. Очистка и подготовка

**Цель:** Устранить технический долг перед интеграцией.

**Обязательные действия:**

1. **Удалить пустые мусорные директории:**
   - `core/context/` (пустая)
   - `core/providers/` (пустая)
   
   Команда:
   ```powershell
   Remove-Item -Recurse -Force "D:\WORED\hypercube\core\context"
   Remove-Item -Recurse -Force "D:\WORED\hypercube\core\providers"
   ```

2. **Исправить async bug в HTX adapter.**
   
   Файл: `providers/htx_adapter.py`, метод `_wait_ratelimit()` (строки 93-101)
   
   Текущий код (НЕПРАВИЛЬНЫЙ):
   ```python
   def _wait_ratelimit(self) -> None:
       now = time.monotonic()
       elapsed = now - self._last_ts
       if elapsed < 0.25:
           import asyncio
           import time as t
           import time as _t
           time.sleep(0.25 - elapsed)  # БЛОКИРУЕТ event loop!
       self._last_ts = time.monotonic()
   ```
   
   Заменить на:
   ```python
   async def _wait_ratelimit(self) -> None:
       import asyncio
       now = time.monotonic()
       elapsed = now - self._last_ts
       if elapsed < 0.25:
           await asyncio.sleep(0.25 - elapsed)
       self._last_ts = time.monotonic()
   ```
   
   Также обновить вызов в `_get()` (строка 80):
   ```python
   # Было:
   self._wait_ratelimit()
   # Стало:
   await self._wait_ratelimit()
   ```

3. **Удалить дублирующие legacy test-файлы** из корня `tests/`:
   - `tests/integration_test.py` (дублирует `tests/integration/test_provider_framework.py`)
   - `tests/smoke_test.py` (дублирует `tests/smoke/test_smoke.py`)

4. **Создать alembic initial migration:**
   ```powershell
   cd D:\WORED\hypercube
   python -m alembic revision --autogenerate -m "initial_schema"
   ```

**Проверка:**
```powershell
# Директории удалены
Test-Path "D:\WORED\hypercube\core\context"  # False
Test-Path "D:\WORED\hypercube\core\providers"  # False
# Миграция создана
Get-ChildItem "D:\WORED\hypercube\alembic\versions\*.py"  # 1 файл
# Тесты проходят
cd D:\WORED\hypercube
python -m pytest tests/unit/ -v
```

---

### WP-2. Repositories — недостающие методы

**Цель:** Убедиться, что `storage/repositories.py` содержит все методы, нужные для pipeline.

**Файл:** `storage/repositories.py`

**Проверить наличие и при необходимости добавить следующие repositories и методы:**

#### UserRepository
```python
class UserRepository:
    async def get_by_telegram_id(self, telegram_id: int) -> User | None
    async def create_or_update(self, telegram_id: int, username: str = None, 
                                first_name: str = None, last_name: str = None) -> User
```

#### TelegramChatRepository
```python
class TelegramChatRepository:
    async def get_by_chat_id(self, chat_id: int) -> TelegramChat | None
    async def create(self, chat_id: int, chat_type: str, user_id: int, title: str = None) -> TelegramChat
```

#### ConversationSessionRepository
```python
class ConversationSessionRepository:
    async def get_active_session(self, user_id: int, chat_id: int) -> ConversationSession | None
    async def create_session(self, session_id: str, user_id: int, chat_id: int, 
                              mode: str = "free_only", active_model: str = "") -> ConversationSession
    async def update_mode(self, session_id: str, mode: str) -> None
    async def update_model(self, session_id: str, model: str) -> None
    async def close_session(self, session_id: str) -> None
```

#### ConversationMessageRepository
```python
class ConversationMessageRepository:
    async def add_message(self, session_id: str, role: str, content: str, 
                           token_count: int = None) -> ConversationMessage
    async def get_history(self, session_id: str, limit: int = 50) -> list[ConversationMessage]
    async def count_messages(self, session_id: str) -> int
```

#### UsageRecordRepository
```python
class UsageRecordRepository:
    async def create(self, record: UsageRecord) -> UsageRecord
    async def aggregate_by_user(self, user_id: int, start: datetime, end: datetime) -> dict
    async def by_model(self, model_id: str, start: datetime, end: datetime) -> list[UsageRecord]
    async def by_provider(self, provider_id: str, start: datetime, end: datetime) -> list[UsageRecord]
    async def error_rate(self, user_id: int, start: datetime, end: datetime) -> float
    async def recent_fallbacks(self, limit: int = 20) -> list[UsageRecord]
    async def recent_handoffs(self, limit: int = 20) -> list[UsageRecord]
```

#### QuotaStateRepository
```python
class QuotaStateRepository:
    async def get_for_provider_model(self, provider_id: str, model_id: str) -> QuotaState | None
    async def update(self, id: int, **kwargs) -> None
    async def get_current_status(self, provider_id: str, model_id: str) -> dict
```

#### RouteDecisionRepository
```python
class RouteDecisionRepository:
    async def create(self, decision: RouteDecision) -> RouteDecision
    async def recent(self, limit: int = 20) -> list[RouteDecision]
```

#### ContextSnapshotRepository
```python
class ContextSnapshotRepository:
    async def create(self, snapshot: ContextSnapshot) -> ContextSnapshot
    async def get_latest(self, session_id: str) -> ContextSnapshot | None
```

#### ContextHandoffRepository
```python
class ContextHandoffRepository:
    async def create(self, handoff: ContextHandoff) -> ContextHandoff
    async def recent(self, limit: int = 20) -> list[ContextHandoff]
```

#### ProviderHealthEventRepository
```python
class ProviderHealthEventRepository:
    async def record_event(self, provider_id: str, event_type: str, is_healthy: bool, details: str = None) -> None
    async def get_latest(self, provider_id: str) -> ProviderHealthEvent | None
```

#### HTXMarketSnapshotRepository
```python
class HTXMarketSnapshotRepository:
    async def save_snapshot(self, symbol: str, data_type: str, raw_data: str) -> None
    async def get_latest(self, symbol: str, data_type: str) -> HTXMarketSnapshot | None
```

#### AdminEventRepository
```python
class AdminEventRepository:
    async def log_event(self, event_type: str, admin_id: int = None, details: str = None) -> None
    async def recent(self, limit: int = 50) -> list[AdminEvent]
```

**Важно:** Все repository-методы должны использовать `async with self._session_factory() as session:` pattern, соответствующий текущему стилю файла. Проверить текущий стиль в `storage/repositories.py` перед началом работы.

**Проверка:**
```powershell
cd D:\WORED\hypercube
python -c "from storage.repositories import *; print('All repositories imported OK')"
```

---

### WP-3. Service Container и инициализация

**Цель:** Создать service container и подключить его к lifecycle приложения.

**Действие 1: Создать `core/services.py`**

Содержимое — как описано в §6.1 выше. Файл содержит только `ServiceContainer` и `UserSessionState` dataclasses.

**Действие 2: Обновить `storage/database.py`**

Добавить функцию `get_session_factory()` которая возвращает async session factory для repositories:

```python
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

_session_factory: async_sessionmaker[AsyncSession] | None = None

def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _session_factory
```

Убедиться, что `init_db()` сохраняет session factory в `_session_factory`.

**Действие 3: Создать `core/bootstrap.py`**

```python
"""Bootstrap — builds ServiceContainer from config and DB."""
from __future__ import annotations

import logging
from typing import Any

from core.config import AppConfiguration
from core.services import ServiceContainer, UserSessionState
from storage.database import get_session_factory
from storage.repositories import (
    UserRepository, TelegramChatRepository, ConversationSessionRepository,
    ConversationMessageRepository, UsageRecordRepository, QuotaStateRepository,
    ContextSnapshotRepository, ContextHandoffRepository, RouteDecisionRepository,
    ProviderHealthEventRepository, HTXMarketSnapshotRepository, AdminEventRepository,
)
from accounting.service import TokenAccountingService
from quotas.engine import QuotaPolicyEngine
from context.service import ContextService
from context.handoff import HandoffBuilder
from routing.service import ModelRegistry, FallbackEngine
from providers.htx_adapter import HTXMarketDataAdapter
from providers.factory import create_provider_adapters

log = logging.getLogger(__name__)


async def build_service_container(config: AppConfiguration) -> ServiceContainer:
    """Build fully initialized ServiceContainer."""
    sf = get_session_factory()

    # repositories
    user_repo = UserRepository(sf)
    chat_repo = TelegramChatRepository(sf)
    session_repo = ConversationSessionRepository(sf)
    message_repo = ConversationMessageRepository(sf)
    usage_repo = UsageRecordRepository(sf)
    quota_repo = QuotaStateRepository(sf)
    snapshot_repo = ContextSnapshotRepository(sf)
    handoff_repo = ContextHandoffRepository(sf)
    route_repo = RouteDecisionRepository(sf)
    health_repo = ProviderHealthEventRepository(sf)
    htx_snapshot_repo = HTXMarketSnapshotRepository(sf)
    admin_repo = AdminEventRepository(sf)

    # services
    accounting = TokenAccountingService(usage_repo)
    quota_engine = QuotaPolicyEngine(quota_repo, usage_repo, config)
    context_service = ContextService(session_repo, message_repo, snapshot_repo)
    handoff_builder = HandoffBuilder(context_service)
    model_registry = ModelRegistry()
    
    # provider adapters
    provider_adapters = create_provider_adapters(config)
    
    fallback_engine = FallbackEngine(model_registry, provider_adapters)

    # HTX
    htx_adapter = HTXMarketDataAdapter(
        api_key=config.HTX_API_KEY,
        api_secret=config.HTX_API_SECRET,
        base_url=config.HTX_BASE_URL,
    )

    container = ServiceContainer(
        user_repo=user_repo,
        chat_repo=chat_repo,
        session_repo=session_repo,
        message_repo=message_repo,
        usage_repo=usage_repo,
        quota_repo=quota_repo,
        snapshot_repo=snapshot_repo,
        handoff_repo=handoff_repo,
        route_repo=route_repo,
        health_repo=health_repo,
        htx_snapshot_repo=htx_snapshot_repo,
        admin_repo=admin_repo,
        accounting=accounting,
        quota_engine=quota_engine,
        context_service=context_service,
        handoff_builder=handoff_builder,
        model_registry=model_registry,
        fallback_engine=fallback_engine,
        htx_adapter=htx_adapter,
        provider_adapters=provider_adapters,
    )

    log.info("ServiceContainer built: %d provider adapters", len(provider_adapters))
    return container
```

**Действие 4: Проверить/создать `providers/factory.py`**

Файл `providers/factory.py` (2392 байт) уже существует. Проверить, что функция `create_provider_adapters(config: AppConfiguration) -> dict[str, Any]` существует и возвращает dict вида `{"dashscope": OpenAICompatibleAdapter(...), ...}`.

Если функция отсутствует или имеет другую сигнатуру, создать:
```python
def create_provider_adapters(config: AppConfiguration) -> dict[str, Any]:
    """Create all AI provider adapters from config."""
    from providers.openai_compatible import OpenAICompatibleAdapter, _ModelInfo
    
    adapters = {}
    for pid, pconf in config.provider_configs.items():
        api_key = pconf.get("api_key", "")
        if not api_key or api_key.startswith("your_"):
            continue  # skip unconfigured providers
        
        models = {}
        for mid, mconf in pconf.get("supported_models", {}).items():
            costs = pconf.get("costs", {}).get(mid, {})
            models[mid] = _ModelInfo(
                input_cost_per_1k=costs.get("input", 0.0),
                output_cost_per_1k=costs.get("output", 0.0),
                is_premium=mconf.get("is_premium", False),
                supports_streaming=mconf.get("supports_streaming", True),
                supports_system_prompt=mconf.get("supports_system_prompt", True),
            )
        
        adapter = OpenAICompatibleAdapter(
            provider_id=pid,
            api_key=api_key,
            base_url=pconf["base_url"],
            models=models,
        )
        adapters[pid] = adapter
    
    return adapters
```

**Действие 5: Обновить `app/main.py` lifespan**

В `lifespan()` заменить текущую инициализацию:
```python
# БЫЛО:
setup_bot(bot, config, {"providers": {}})

# СТАЛО:
from core.bootstrap import build_service_container
container = await build_service_container(config)
setup_bot(bot, config, container)
```

**Проверка:**
```powershell
cd D:\WORED\hypercube
python -c "
from core.services import ServiceContainer, UserSessionState
print('ServiceContainer OK')
"
python -c "
from core.bootstrap import build_service_container
print('Bootstrap module OK')
"
```

---

### WP-4. Pipeline `/ask` — полный цикл

**Цель:** Команда `/ask` выполняет полный pipeline.

**Файл:** `bot/handlers.py`, функция `cmd_ask()`

**Полный алгоритм:**

```
1. Извлечь user_query из message.text
2. Получить/создать UserSessionState в _services.user_sessions
3. Получить/создать User в БД через _services.user_repo
4. Получить/создать ConversationSession через _services.session_repo
5. Сохранить user message через _services.message_repo
6. QUOTA CHECK:
   a. Определить текущую модель из session state
   b. Определить provider для модели
   c. Вызвать _services.quota_engine.check_quota(provider_id, model_id)
   d. Если hard_stop → ответить предупреждением, предложить /switch_model
   e. Если warning → добавить предупреждение к ответу
7. HTX ENRICH (опционально):
   a. Проверить, содержит ли запрос ключевые слова рыночного анализа
   b. Если да → вызвать _services.htx_adapter для получения данных
   c. Добавить market context в system prompt
8. ROUTE & INVOKE:
   a. Вызвать _services.fallback_engine.execute_with_fallback(request, mode, user_id)
   b. Получить response
9. PERSIST:
   a. Записать usage record через _services.accounting.record_usage()
   b. Записать route decision через _services.route_repo.create()
   c. Сохранить assistant message через _services.message_repo
   d. Обновить quota state через _services.quota_engine.update_quota_state()
10. REPLY:
    a. Отформатировать ответ с метаданными (модель, fallback, токены)
    b. Отправить пользователю
```

**Конкретная реализация:**

```python
@router.message(Command("ask"))
async def cmd_ask(message: Message) -> None:
    if not message.text:
        await message.answer("Пожалуйста, укажите вопрос после /ask")
        return

    user_query = message.text.split("/ask", 1)[1].strip()
    if not user_query:
        await message.answer("Пожалуйста, укажите вопрос после /ask")
        return

    await message.answer("🤔 Обрабатываю запрос...", parse_mode="Markdown")

    try:
        user_id = message.from_user.id
        
        # 1. Ensure user exists
        user = await _services.user_repo.get_by_telegram_id(user_id)
        if not user:
            user = await _services.user_repo.create_or_update(
                telegram_id=user_id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
            )

        # 2. Get session state
        session_state = _services.user_sessions.get(user_id)
        if not session_state:
            session_state = UserSessionState(telegram_user_id=user_id)
            _services.user_sessions[user_id] = session_state

        # 3. Get current mode and model
        mode = session_state.current_mode or "free_only"
        
        # 4. Quota check
        chain = await _services.fallback_engine.resolve_chain(mode)
        primary_model = chain[0] if chain else ""
        provider_id = _services.fallback_engine._provider_for_model(primary_model)
        
        if provider_id:
            quota_result = await _services.quota_engine.check_quota(provider_id, primary_model)
            if quota_result.hard_stop:
                await message.answer(
                    f"⛔ Квота исчерпана для {primary_model}.\n"
                    f"Остаток: {quota_result.remaining_pct:.1f}%\n\n"
                    f"Используйте /switch_model для переключения.",
                    parse_mode="Markdown"
                )
                return
            quota_warning = quota_result.warning_triggered
        else:
            quota_warning = False

        # 5. HTX enrichment (detect market-related query)
        market_context = ""
        market_keywords = ["btc", "eth", "usdt", "рынок", "цена", "курс", "анализ", 
                          "торг", "свеч", "объем", "volume", "ticker", "тикер"]
        if any(kw in user_query.lower() for kw in market_keywords):
            try:
                tickers = await _services.htx_adapter.get_tickers()
                top5 = sorted(tickers, key=lambda t: t.volume, reverse=True)[:5]
                market_lines = [f"- {t.symbol}: ${t.last:.4f} ({t.change_pct:+.2f}%)" for t in top5]
                market_context = (
                    "\n\n[HTX Market Context - Top 5 by volume]\n"
                    + "\n".join(market_lines)
                )
            except Exception as e:
                log.warning("HTX enrichment failed: %s", e)

        # 6. Build messages
        system_prompt = (
            "Ты — AI-аналитик криптобиржи HTX. "
            "Предоставляй структурированную аналитику без исполнения сделок. "
            "Всегда добавляй дисклеймер: бот не является финансовым советником."
        )
        if market_context:
            system_prompt += market_context

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ]

        # 7. Build request and execute
        from core.schemas import AIRequest
        request = AIRequest(
            model=primary_model,
            messages=messages,
            max_tokens=1024,
            temperature=0.7,
            stream=False,
        )

        response = await _services.fallback_engine.execute_with_fallback(
            request, mode, user_id
        )

        # 8. Persist usage
        from core.request_id import generate_request_id
        request_id = generate_request_id()
        
        from storage.models import UsageRecord as UsageRecordModel
        usage_record = UsageRecordModel(
            request_id=request_id,
            conversation_id=session_state.conversation_id or "",
            telegram_user_id=user_id,
            provider_id=_services.fallback_engine._provider_for_model(response.model) or "unknown",
            model_id=response.model,
            input_tokens=response.usage.input_tokens if response.usage else 0,
            output_tokens=response.usage.output_tokens if response.usage else 0,
            total_tokens=response.usage.total_tokens if response.usage else 0,
            latency_ms=0,
            status="success",
            cost_estimate=0.0,
            warning_triggered=quota_warning,
            fallback_triggered=response.model != primary_model,
        )
        await _services.usage_repo.create(usage_record)

        # 9. Reply
        fallback_note = ""
        if response.model != primary_model:
            fallback_note = f"\n⚡ _Fallback: {primary_model} → {response.model}_"
        
        warning_note = ""
        if quota_warning:
            warning_note = "\n⚠️ _Предупреждение: квота близка к исчерпанию_"

        reply_text = (
            f"📊 **Ответ:**\n\n"
            f"{response.content}\n\n"
            f"---\n"
            f"_Модель: {response.model}_"
            f"{fallback_note}"
            f"{warning_note}"
        )
        
        # Telegram limit: 4096 chars
        if len(reply_text) > 4000:
            reply_text = reply_text[:3990] + "\n...(обрезано)"
        
        await message.answer(reply_text, parse_mode="Markdown")

    except Exception as e:
        log.exception("Ask command failed")
        await message.answer(f"❌ Ошибка: {str(e)[:200]}")
```

**Важно:** Адаптировать под реальные сигнатуры `FallbackEngine.execute_with_fallback()` и `AIResponse`. Проверить, что `response.content` и `response.model` и `response.usage` существуют. Если `execute_with_fallback` возвращает tuple `(response, decision)` — распаковать.

**Проверка:**
```powershell
cd D:\WORED\hypercube
python -c "
from bot.handlers import cmd_ask
print('cmd_ask imported OK')
"
```

---

### WP-5. Оживление stub-команд

**Цель:** Все Telegram-команды читают реальные данные.

**Файл:** `bot/handlers.py`

**Для каждой команды — точное описание изменений:**

#### `/mode` (строки 133-150)

```python
@router.message(Command("mode"))
async def cmd_mode(message: Message) -> None:
    user_id = message.from_user.id
    session_state = _services.user_sessions.get(user_id)
    if not session_state:
        session_state = UserSessionState(telegram_user_id=user_id)
        _services.user_sessions[user_id] = session_state

    args = message.text.split()
    if len(args) < 2:
        text = (
            f"**Текущий режим:** {session_state.current_mode}\n\n"
            f"Использование: `/mode free_only|balanced|premium`"
        )
        await message.answer(text, parse_mode="Markdown")
        return

    new_mode = args[1].lower()
    if new_mode not in ("free_only", "balanced", "premium"):
        await message.answer("Неверный режим. Используйте: free_only, balanced, premium")
        return

    session_state.current_mode = new_mode
    await message.answer(f"✅ Режим переключен на **{new_mode}**", parse_mode="Markdown")
```

#### `/usage` (строки 186-194)
Заменить хардкод на вызов `_services.accounting.get_user_usage()` — как показано в §6.3.

#### `/quota` (строки 197-207)
```python
@router.message(Command("quota"))
async def cmd_quota(message: Message) -> None:
    user_id = message.from_user.id
    session_state = _services.user_sessions.get(user_id)
    mode = session_state.current_mode if session_state else "free_only"
    
    try:
        chain = await _services.fallback_engine.resolve_chain(mode)
        lines = [f"📏 **Статус квоты (режим: {mode}):**\n"]
        for model_id in chain[:3]:
            provider_id = _services.fallback_engine._provider_for_model(model_id)
            if provider_id:
                result = await _services.quota_engine.check_quota(provider_id, model_id)
                icon = "✅" if result.allowed else "⛔"
                lines.append(f"{icon} **{model_id}**: {result.remaining_pct:.1f}% осталось")
            else:
                lines.append(f"❓ **{model_id}**: провайдер не найден")
        
        lines.append(f"\n_Пороги: warning={_config.QUOTA_WARNING_THRESHOLD_PCT}%, "
                     f"critical={_config.QUOTA_CRITICAL_THRESHOLD_PCT}%, "
                     f"hard stop={_config.QUOTA_HARD_STOP_THRESHOLD_PCT}%_")
        await message.answer("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)[:200]}")
```

#### `/context` (строки 210-219)
```python
@router.message(Command("context"))
async def cmd_context(message: Message) -> None:
    user_id = message.from_user.id
    session_state = _services.user_sessions.get(user_id)
    
    if not session_state or not session_state.session_id:
        await message.answer("🧠 **Состояние контекста:**\n\nАктивная сессия: нет", parse_mode="Markdown")
        return
    
    try:
        msg_count = await _services.message_repo.count_messages(session_state.session_id)
        snapshot = await _services.snapshot_repo.get_latest(session_state.session_id)
        snapshot_info = f"Последний snapshot: {snapshot.created_at.isoformat()}" if snapshot else "Последний snapshot: нет"
        
        text = (
            f"🧠 **Состояние контекста:**\n\n"
            f"Активная сессия: {session_state.session_id[:8]}...\n"
            f"Режим: {session_state.current_mode}\n"
            f"Модель: {session_state.current_model or 'auto'}\n"
            f"Сообщений в истории: {msg_count}\n"
            f"{snapshot_info}"
        )
        await message.answer(text, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)[:200]}")
```

#### `/switch_model` (строки 222-231)
```python
@router.message(Command("switch_model"))
async def cmd_switch_model(message: Message) -> None:
    user_id = message.from_user.id
    session_state = _services.user_sessions.get(user_id)
    if not session_state:
        session_state = UserSessionState(telegram_user_id=user_id)
        _services.user_sessions[user_id] = session_state
    
    args = message.text.split()
    if len(args) < 2:
        models = _services.model_registry.list_all()
        model_list = ", ".join(m["model_id"] for m in models if m["status"] == "active")
        await message.answer(
            f"Использование: `/switch_model <model_id>`\n\nДоступные: {model_list}",
            parse_mode="Markdown"
        )
        return

    target_model = args[1]
    old_model = session_state.current_model or "auto"
    mode = session_state.current_mode

    try:
        # Verify model is available
        if not _services.model_registry.is_model_available(target_model, mode):
            await message.answer(f"❌ Модель {target_model} недоступна в режиме {mode}")
            return
        
        # Perform handoff if there was an active session
        handoff_note = ""
        if session_state.session_id:
            try:
                handoff = await _services.handoff_builder.build_handoff(
                    old_session_id=session_state.session_id,
                    new_model=target_model,
                    old_model=old_model,
                )
                handoff_note = "\n✅ Контекст сохранён и передан"
            except Exception as e:
                log.warning("Handoff failed: %s", e)
                handoff_note = "\n⚠️ Контекст не удалось передать"

        # Update session state
        session_state.current_model = target_model
        
        await message.answer(
            f"🔄 Переключение выполнено:\n\n"
            f"**Было:** {old_model}\n"
            f"**Стало:** {target_model}\n"
            f"**Режим:** {mode}"
            f"{handoff_note}",
            parse_mode="Markdown"
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)[:200]}")
```

#### `/health` (строки 234-245)
```python
@router.message(Command("health"))
async def cmd_health(message: Message) -> None:
    checks = {}
    
    # Gateway
    checks["Gateway"] = True
    
    # Database
    try:
        from storage.database import get_session_factory
        sf = get_session_factory()
        checks["Database"] = sf is not None
    except Exception:
        checks["Database"] = False
    
    # HTX API
    try:
        ts = await _services.htx_adapter.get_server_time()
        checks["HTX API"] = ts > 0
    except Exception:
        checks["HTX API"] = False
    
    # AI Providers
    provider_checks = {}
    for pid, adapter in _services.provider_adapters.items():
        try:
            health = await adapter.healthcheck()
            provider_checks[pid] = health.healthy
        except Exception:
            provider_checks[pid] = False
    
    checks["AI Providers"] = any(provider_checks.values()) if provider_checks else False

    lines = []
    all_ok = all(checks.values())
    lines.append(f"{'💚' if all_ok else '🔴'} **Состояние системы:**\n")
    for name, ok in checks.items():
        lines.append(f"{'✅' if ok else '❌'} {name}")
    
    if provider_checks:
        lines.append("\n**Провайдеры:**")
        for pid, ok in provider_checks.items():
            lines.append(f"  {'✅' if ok else '❌'} {pid}")
    
    await message.answer("\n".join(lines), parse_mode="Markdown")
```

#### `/admin_stats` (строки 258-275)
```python
@router.message(Command("admin_stats"))
async def cmd_admin_stats(message: Message) -> None:
    admin_ids = _config.admin_user_ids if _config else set()
    if message.from_user.id not in admin_ids:
        await message.answer("❌ Доступ запрещён")
        return

    try:
        day_usage = await _services.accounting.get_user_usage(0, "day")  # 0 = all users
        recent_fallbacks = await _services.usage_repo.recent_fallbacks(limit=5)
        recent_handoffs = await _services.usage_repo.recent_handoffs(limit=5)
        
        text = (
            f"📈 **Расширенная статистика:**\n\n"
            f"Запросов за сегодня: {day_usage.get('requests', 0)}\n"
            f"Токенов за сегодня: {day_usage.get('total_tokens', 0)}\n"
            f"Стоимость за сегодня: ${day_usage.get('cost', 0):.4f}\n"
            f"Fallback events (последние): {len(recent_fallbacks)}\n"
            f"Context handoff events (последние): {len(recent_handoffs)}\n"
        )
        await message.answer(text, parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)[:200]}")
```

**Важно:** Обновить `setup_bot()` для приёма `ServiceContainer` вместо `dict`:

```python
from core.services import ServiceContainer, UserSessionState

_services: ServiceContainer | None = None

def setup_bot(bot: Bot, config: AppConfiguration, services: ServiceContainer) -> None:
    global _bot, _config, _services
    _bot = bot
    _config = config
    _services = services
```

**Проверка:**
```powershell
cd D:\WORED\hypercube
python -c "from bot.handlers import *; print('All handlers OK')"
python -m pytest tests/unit/ -v
```

---

### WP-6. Admin Module

**Цель:** Реализовать `admin/service.py` с базовыми diagnostic-функциями.

**Файл:** `admin/__init__.py` (обновить) и `admin/service.py` (создать)

```python
# admin/service.py
"""Admin diagnostics and system management service."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


class AdminService:
    """Provides admin diagnostics and system information."""

    def __init__(self, container) -> None:
        """container: ServiceContainer instance."""
        self._c = container

    async def get_system_status(self) -> dict[str, Any]:
        """Get overall system health status."""
        status = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "database": True,
            "providers": {},
            "htx_api": False,
        }
        
        # Check providers
        for pid, adapter in self._c.provider_adapters.items():
            try:
                health = await adapter.healthcheck()
                status["providers"][pid] = {
                    "healthy": health.healthy,
                    "latency_ms": health.latency_ms,
                }
            except Exception as e:
                status["providers"][pid] = {"healthy": False, "error": str(e)}
        
        # Check HTX
        try:
            ts = await self._c.htx_adapter.get_server_time()
            status["htx_api"] = ts > 0
        except Exception:
            status["htx_api"] = False
        
        return status

    async def get_usage_summary(self, window: str = "day") -> dict[str, Any]:
        """Get aggregated usage summary."""
        # Aggregate across all users (user_id=0 convention)
        return await self._c.accounting.get_user_usage(0, window)

    async def get_recent_events(self, limit: int = 20) -> dict[str, Any]:
        """Get recent system events."""
        return {
            "recent_fallbacks": await self._c.usage_repo.recent_fallbacks(limit),
            "recent_handoffs": await self._c.usage_repo.recent_handoffs(limit),
        }

    async def log_admin_action(self, admin_id: int, action: str, details: str = None) -> None:
        """Log an admin action."""
        await self._c.admin_repo.log_event(
            event_type=action,
            admin_id=admin_id,
            details=details,
        )
```

```python
# admin/__init__.py
"""Admin module — diagnostics, usage exports, runtime control."""
from admin.service import AdminService

__all__ = ["AdminService"]
```

**Проверка:**
```powershell
cd D:\WORED\hypercube
python -c "from admin.service import AdminService; print('AdminService OK')"
```

---

### WP-7. Устранение дублирования routing

**Цель:** Убрать hardcoded chains из `routing/service.py`, использовать config-driven подход.

**Файл:** `routing/service.py`

**Действия:**

1. Заменить `_DEFAULT_CHAINS` (строки 27-31) на загрузку из `examples/provider_registry.yaml`
2. Заменить `_MODE_MODEL_MAP` (строки 33-39) на вычисление из YAML registry
3. Заменить `_MODEL_COSTS` (строки 42-48) на данные из YAML registry

```python
# Вместо хардкода — загрузка из YAML:
import yaml
from pathlib import Path

def _load_registry() -> dict:
    """Load provider registry from YAML config."""
    paths = [
        Path("examples/provider_registry.yaml"),
        Path("D:/WORED/hypercube/examples/provider_registry.yaml"),
    ]
    for p in paths:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
    return {}

def _build_chains_from_registry(registry: dict) -> dict[str, list[str]]:
    """Build candidate chains from registry data."""
    # ... extract models, sort by cost, build chains per mode
    pass

def _build_mode_map_from_registry(registry: dict) -> dict[str, dict[str, bool]]:
    """Build mode availability map from registry."""
    pass

def _build_costs_from_registry(registry: dict) -> dict[str, dict[str, float]]:
    """Build cost map from registry."""
    pass
```

**Fallback:** Если YAML не найден — использовать текущие хардкоженные значения как fallback. Не ломать существующую функциональность.

**Проверка:**
```powershell
cd D:\WORED\hypercube
python -m pytest tests/unit/test_routing.py -v
```

---

### WP-8. Финальная валидация

**Цель:** Убедиться, что всё работает вместе.

**Обязательные проверки:**

```powershell
cd D:\WORED\hypercube

# 1. Все imports работают
python -c "
from core.services import ServiceContainer
from core.bootstrap import build_service_container
from admin.service import AdminService
from bot.handlers import cmd_ask, cmd_usage, cmd_quota, cmd_health
from providers.htx_adapter import HTXMarketDataAdapter
from context.handoff import HandoffBuilder
from quotas.engine import QuotaPolicyEngine
from accounting.service import TokenAccountingService
from routing.service import FallbackEngine, ModelRegistry
from storage.repositories import *
print('ALL IMPORTS OK')
"

# 2. Все тесты проходят
python -m pytest tests/ -v --tb=short

# 3. Alembic-миграция существует
python -m alembic heads

# 4. FastAPI startable (проверка импорта)
python -c "from app.main import app; print(f'FastAPI app: {app.title}')"
```

**Создать финальный smoke test:**

Файл: `tests/smoke/test_integration_smoke.py`

```python
"""Smoke test — verify all modules integrate correctly."""
import pytest


def test_service_container_imports():
    from core.services import ServiceContainer, UserSessionState
    assert ServiceContainer is not None

def test_bootstrap_imports():
    from core.bootstrap import build_service_container
    assert build_service_container is not None

def test_admin_service_imports():
    from admin.service import AdminService
    assert AdminService is not None

def test_all_handlers_importable():
    from bot.handlers import (
        cmd_start, cmd_help, cmd_ask, cmd_mode, cmd_models,
        cmd_providers, cmd_usage, cmd_quota, cmd_context,
        cmd_switch_model, cmd_health, cmd_reload, cmd_admin_stats,
    )

def test_htx_adapter_async_ratelimit():
    """Verify _wait_ratelimit is async."""
    import inspect
    from providers.htx_adapter import HTXMarketDataAdapter
    adapter = HTXMarketDataAdapter()
    assert inspect.iscoroutinefunction(adapter._wait_ratelimit)

def test_no_stale_directories():
    from pathlib import Path
    assert not Path("core/context").exists(), "core/context/ should be deleted"
    assert not Path("core/providers").exists(), "core/providers/ should be deleted"

def test_alembic_has_migrations():
    from pathlib import Path
    versions = list(Path("alembic/versions").glob("*.py"))
    assert len(versions) >= 1, "At least one alembic migration expected"
```

---

## 8. Порядок выполнения

QwenCode **обязан** работать строго в этом порядке:

```
WP-1 → WP-2 → WP-3 → WP-4 → WP-5 → WP-6 → WP-7 → WP-8
```

Каждый WP завершается командой проверки. Нельзя переходить к WP+1, если проверка WP не прошла.

---

## 9. Формат отчетности

Каждый WP-отчёт должен содержать:

1. Номер WP и goal
2. Список файлов: создано / изменено / удалено
3. Команды проверки и их вывод
4. Количество пройденных тестов
5. Блокеры (если есть)

---

## 10. Явные запреты

- Не добавлять торговые методы HTX.
- Не менять сигнатуры ORM-моделей без alembic-миграции.
- Не удалять существующие тесты.
- Не добавлять новые Python-зависимости без обоснования.
- Не создавать глобальные переменные вне `bot/handlers.py`.
- Не хардкодить API-ключи.
- Не ловить Exception без логирования.
- Не отвечать в Telegram текстом длиннее 4000 символов без обрезки.

---

## 11. Критерии приёмки

Этап считается завершённым, когда:

1. ✅ `/ask` выполняет полный pipeline (quota → HTX → route → invoke → persist → reply)
2. ✅ `/usage` показывает реальные данные из БД
3. ✅ `/quota` показывает реальный остаток квоты
4. ✅ `/context` показывает реальное состояние сессии
5. ✅ `/health` проверяет реальные сервисы (DB, HTX, AI providers)
6. ✅ `/switch_model` выполняет handoff
7. ✅ `/mode` сохраняет выбранный режим
8. ✅ `/admin_stats` показывает реальную статистику из БД
9. ✅ `admin/service.py` существует и импортируется
10. ✅ `core/context/` и `core/providers/` удалены
11. ✅ HTX adapter использует `asyncio.sleep()` вместо `time.sleep()`
12. ✅ Alembic имеет хотя бы 1 миграцию
13. ✅ Все тесты проходят (`pytest tests/ -v`)
14. ✅ Routing использует YAML registry (с хардкод-fallback)

---

## 12. Зависимости — что нужно проверить перед стартом

Перед началом работы QwenCode обязан выполнить:

```powershell
cd D:\WORED\hypercube
python --version
pip install -r requirements.txt
python -m pytest tests/unit/ -v --tb=short
```

Если `requirements.txt` не содержит `pyyaml`, добавить:
```
pyyaml>=6.0
```

Если тесты не проходят — зафиксировать ошибки и доложить Lead Agent перед началом WP-1.

---

## 13. Следующие шаги после завершения

После полного выполнения этого ТЗ:

1. Создать ТЗ-04 на обязательную документацию по ТЗ-01 §19 (15 doc-файлов)
2. Обновить GO_NO_GO_REPORT.md с реальным статусом
3. Провести end-to-end тестирование через Telegram
4. Рассмотреть VPS migration

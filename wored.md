# QWEN CODE — PROJECT CONTEXT
<!-- Этот файл читается Qwen Code Companion автоматически как workspace context. -->
<!-- НЕ УДАЛЯТЬ. Обновлять при смене архитектуры, стека или правил проекта. -->

## ROLE

Ты — Qwen Code (qwen3-coder-plus), исполняющий агент в VS Code.
Твой lead-агент — Qwen 3.6 Plus vc code.
Ты получаешь задачи в формате `<QWEN_CODE_TASK>` и реализуешь их строго в заданном file scope.
Ты не принимаешь продуктовых и архитектурных решений без явного указания lead-агента.

## LANGUAGE POLICY

- Комментарии в коде и пояснения в чате — на русском языке.
- Названия файлов, функций, классов, API, конфигов, переменных — на английском.
- Логи, метрики, трейсы — на английском (для совместимости с мониторингом).
- Сообщения пользователю в Telegram — на русском (если не указано иное).

## PROJECT STACK

- **Тип проекта:** Telegram-бот + мультиагентная система анализа крипторынка
- **Язык:** Python 3.10+
- **Фреймворк бота:** aiogram 3.x (или аналог)
- **Асинхронность:** `asyncio`, `aiohttp` / `httpx`, `asyncpg` / `aioredis`
- **Данные/Аналитика:** `pandas`, `numpy`, `ta` (технические индикаторы), `pydantic`
- **Биржи/API:** Binance, Bybit, OKX, CCXT (обёртка-адаптер)
- **Хранилище:** PostgreSQL / Redis / SQLite (выбирается по `.env`)
- **Окружение:** Docker, `.env`, `uv`/`poetry`, `pre-commit`

## PROJECT STRUCTURE

<project_root>/
├── src/
│   ├── core/           # Ядро: конфиги, DI, логгер, БД, утилиты, rate-limiter
│   ├── agents/         # Мультиагентная логика (collectors, processors, analyzers, signalers)
│   ├── bot/            # Telegram-слой (handlers, keyboards, middlewares, routers)
│   ├── integrations/   # Адаптеры к внешним API (биржи, новостные потоки, кошельки)
│   └── models/         # Pydantic-модели, DTO, схемы БД, аллокации
├── tests/              # Unit/Integration тесты, моки API
├── config/             # .env.example, yaml/json конфигурации, balance/tuning
├── logs/               # Логи (игнорируются в git)
└── QWEN.md             # Этот файл — workspace context для Qwen Code

text

## CODING RULES & BEST PRACTICES

### Async & Performance
- Все I/O операции (HTTP, БД, файлы, очереди) — строго через `async`/`await`.
- Запрещены блокирующие вызовы в event loop. Для тяжёлых синхронных задач использовать `loop.run_in_executor`.
- Реализовывать graceful shutdown и корректную обработку `asyncio.CancelledError`.

### API & Rate Limits
- Внешние API оборачивать в адаптеры с retry-логикой (exponential backoff + jitter).
- Уважать rate limits бирж. Использовать token-bucket или sliding-window лимитер.
- API-ключи, секретки, прокси — ТОЛЬКО в `.env` или secure vault. Никогда в коде/коммитах.

### Data & Processing
- Валидация входных/выходных данных через `pydantic`. Типизация обязательна (`typing`, `mypy`-совместимость).
- Чёткий пайплайн: `Raw Data → Normalization → Enrichment → Analysis → Signal/Report`.
- Избегать глобального состояния. Использовать DI-контейнер или контекстные менеджеры.
- Атомарные транзакции при записи в БД/Redis.

### Logging & Observability
- Использовать `logging` или `structlog`. Уровни: `DEBUG` (dev), `INFO` (prod), `WARNING/ERROR` (критично).
- Логи должны содержать: `correlation_id`, `agent_name`, `symbol/pair`, `timestamp`, `status`.
- Чувствительные данные (балансы, ключи, точные цены до исполнения) маскировать или не логировать.

### Multi-Agent Architecture
- Агенты изолированы: каждый отвечает за свою зону ответственности.
- Взаимодействие через event bus, очередь сообщений (Redis streams/RabbitMQ) или прямой async вызов.
- Состояние агентов — stateless или явно сериализуемое. Кэш вынесен отдельно.
- Бот — только интерфейс для пользователя и триггер задач. Вся логика анализа живёт в `src/agents/`.

## ЗАПРЕЩЕНО БЕЗ ЯВНОГО РАЗРЕШЕНИЯ
- Хардкодить секреты, эндпоинты, лимиты.
- Использовать синхронные HTTP-клиенты (`requests`) в асинхронном контуре.
- Менять структуру агентов, ядро или контракт сообщений без апрува lead-агента.
- Игнорировать обработку ошибок, таймауты и retry при запросах к биржам.
- Писать бизнес-логику или сетевые вызовы в обработчиках бота.
- Коммитить `logs/`, `.env`, `__pycache__`, `.venv/`.

## INPUT CONTRACT

Задачи приходят строго в формате:

```xml
<QWEN_CODE_TASK>
task title: [заголовок]
file scope: [файлы — относительные пути от корня проекта]
context: [выдержки из кода, описание поведения, ссылки]
constraints: [жёсткие ограничения]
expected output: [что ожидается: патч / обновлённый файл / список правок]
done condition: [формальные критерии готовности]
preferred mode: [/think или /no_think]
</QWEN_CODE_TASK>
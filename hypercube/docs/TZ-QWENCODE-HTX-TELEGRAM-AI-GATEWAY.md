# Техническое задание для QwenCode

## 1. Назначение документа

Этот документ является основным техническим заданием на разработку локального Telegram-бота для аналитической помощи криптотрейдеру в рамках одной биржи HTX. Бот работает как локальный "OpenRouter" под управлением Telegram, умеет переключать AI-модели, учитывать расход токенов, сохранять и восстанавливать контекст, предупреждать об исчерпании квоты и бесшовно переводить пользователя на другую модель без потери рабочего состояния.

Документ написан для разработчика `QwenCode`, который будет выполнять реализацию в терминале под управлением Lead Agent.

## 2. Цель проекта

Разработать локальный, Dockerized, reproducible AI Telegram bot platform со следующими свойствами:

- бот работает как интерфейс управления и общения через Telegram;
- данные по рынку и аналитические запросы ограничены биржей HTX;
- на первом этапе ключ HTX используется только для чтения и аналитики, без торговых операций;
- AI-слой поддерживает несколько моделей и провайдеров;
- бот умеет выбирать модель по политике, доступности, квоте, стоимости и надежности;
- бот предупреждает о малом остатке токенов и риске прерывания запроса;
- бот предлагает переключение модели при низкой квоте или исчерпании лимита;
- бот сохраняет последний актуальный контекст, сжимает его при необходимости и передает новой модели после переключения;
- бот ведет единый учет использования токенов, запросов, задержек и ошибок;
- локальный runtime запускается через Docker Compose на персональном ПК;
- архитектура должна быть пригодна для последующего переноса на VPS без переписывания core-логики.

## 3. Основной пользовательский сценарий

Ключевой сценарий:

1. Пользователь пишет в Telegram запрос на анализ рынка HTX.
2. Бот классифицирует запрос: обзор рынка, анализ символа, сравнение активов, оценка риска, разбор движения цены, анализ объемов, краткий торговый план без исполнения сделки.
3. Бот собирает необходимый рыночный контекст по HTX.
4. Бот выбирает AI-модель в соответствии с текущим режимом и квотной политикой.
5. Бот отправляет нормализованный запрос выбранной модели.
6. Во время подготовки ответа бот контролирует остаток токенов, лимиты и ошибки провайдера.
7. Если квота близка к исчерпанию, бот заранее предупреждает пользователя.
8. Если квота исчерпана или модель недоступна, бот предлагает переключение или выполняет автопереключение по политике.
9. Перед переключением бот сохраняет последний полезный контекст, актуализирует его и передает новой модели как первый системный пакет.
10. Пользователь получает аналитический ответ и краткую техническую метку: какая модель сработала, был ли fallback, сколько токенов израсходовано, был ли перенос контекста.

## 4. Предметная область и ограничения

### 4.1 Что входит в scope

- Telegram bot как UI и control plane.
- HTX market data integration по API только для чтения.
- AI routing между несколькими моделями и провайдерами.
- Unified token accounting.
- Quota and budget enforcement.
- Context save, summarize, restore, and handoff.
- Локальный OpenAI-compatible gateway layer.
- Локальное хранение данных в SQLite.
- Docker Compose runtime.
- Admin commands и health diagnostics.
- Test suite и подробная документация.

### 4.2 Что не входит в scope первой версии

- Реальная торговля через API.
- Выставление ордеров, отмена ордеров, управление позициями.
- Работа с несколькими биржами.
- Облачный multi-tenant SaaS режим.
- Публичный интернет-hosting как обязательный режим по умолчанию.

### 4.3 Обязательные ограничения

- HTX only на первом этапе.
- HTX API key read-only.
- Ни один модуль не должен содержать код выполнения торговых операций.
- Любая аналитика должна сопровождаться дисклеймером, что бот не исполняет сделки и не является финансовым советником.
- Telegram handlers должны оставаться thin handlers.
- Core routing, accounting, quotas, context engine и HTX integrations должны быть отделены друг от друга.

## 5. Роли в системе

### 5.1 Пользователь

Инициирует аналитические запросы, переключает режимы, смотрит usage, подтверждает или отклоняет предложенное переключение модели, читает предупреждения о токенах и квотах.

### 5.2 Администратор

Управляет конфигурацией моделей, режимами работы, health checks, reload policies, quota policies, weekly refresh, диагностикой системы.

### 5.3 Система

Собирает рыночные данные HTX, маршрутизирует запросы, управляет контекстом, выбирает модель, отслеживает токены и квоты, ведет audit trail.

## 6. Продуктовые требования

### 6.1 Telegram как единственный пользовательский интерфейс

Бот должен предоставлять команды и диалоговые сценарии через Telegram. Telegram является внешним интерфейсом управления и вывода аналитики. Бизнес-логика не должна находиться в handler-слое.

### 6.2 Локальный OpenRouter

Система должна содержать локальный routing layer, который:

- принимает унифицированный внутренний AI request;
- подбирает модель и провайдера;
- применяет fallback chain;
- ведет decision trace;
- возвращает унифицированный AI response;
- может, при необходимости, предоставлять локальный OpenAI-compatible HTTP facade для внутренних сервисов.

### 6.3 Несколько моделей и провайдеров

Нужно поддержать минимум три класса моделей:

- основная рабочая модель;
- дешевая fallback модель;
- резервная устойчивая модель для деградационного режима.

Пример продуктового сценария:

- пользователь работает на `GLM 5.1`;
- лимит квоты или токенов у этой модели заканчивается;
- бот предупреждает заранее;
- бот предлагает переключение на доступную альтернативу;
- система сохраняет рабочий контекст;
- новая модель получает контекст и продолжает диалог без потери состояния.

### 6.4 Контекст не должен теряться при переключении модели

Нужно реализовать context handoff pipeline:

1. взять последний conversation state;
2. выделить рабочую суть контекста;
3. выделить user intent;
4. выделить последние рыночные факты HTX;
5. выделить ограничения и активные инструкции;
6. собрать handoff prompt;
7. передать handoff prompt новой модели до основного user prompt.

### 6.5 Учет токенов и квот

Нужно учитывать:

- input tokens;
- output tokens;
- total tokens;
- reasoning tokens, если модель их возвращает;
- cached tokens, если поддерживается;
- request count;
- success/failure;
- latency;
- cost estimate;
- provider quota;
- model quota;
- warning threshold;
- hard stop threshold.

### 6.6 Предупреждение о низком остатке

При достижении порога предупреждения бот обязан:

- сообщить, что токенов или квоты осталось мало;
- сообщить, что текущий запрос может оборваться;
- предложить краткий режим ответа;
- предложить переключение на альтернативную модель;
- записать это событие в usage log.

### 6.7 Автоматическое и ручное переключение модели

Система должна поддерживать два режима:

- `manual-confirmed`: бот предлагает альтернативу, пользователь подтверждает переключение;
- `auto-fallback`: бот переключает модель автоматически по policy rules.

## 7. Функциональные требования

### 7.1 Команды Telegram

Обязательные команды первой версии:

- `/start`
- `/help`
- `/ask`
- `/mode`
- `/models`
- `/providers`
- `/usage`
- `/quota`
- `/context`
- `/switch_model`
- `/health`
- `/reload`
- `/admin_stats`

### 7.2 Поведение команд

#### `/start`

Показывает краткое описание бота, текущий режим работы, активную модель по умолчанию и ограничения: только HTX, только аналитика, без торговли.

#### `/help`

Показывает список команд, режимы, примеры запросов и объясняет работу переключения моделей и предупреждений о квоте.

#### `/ask`

Основная пользовательская команда. Принимает аналитический запрос, запускает полную цепочку: normalize -> enrich with HTX context -> route model -> run -> persist -> reply.

#### `/mode`

Позволяет выбрать режим выполнения:

- `free_only`
- `balanced`
- `premium`

#### `/models`

Показывает список доступных моделей, их статус, примерную стоимость, наличие квоты, режимы допуска и пригодность для аналитических задач.

#### `/providers`

Показывает список AI providers и их текущий health state.

#### `/usage`

Показывает расход токенов и запросов за день, неделю и месяц.

#### `/quota`

Показывает квоты по текущей модели, провайдеру и предупреждающие пороги.

#### `/context`

Показывает краткое состояние текущего рабочего контекста: какой conversation state сохранен, когда обновлялся, какой его размер, есть ли compressed handoff summary.

#### `/switch_model`

Позволяет вручную переключиться на другую модель. Перед завершением переключения система обязана сформировать context handoff package.

#### `/health`

Показывает состояние bot service, gateway service, DB, HTX API connectivity и AI providers.

#### `/reload`

Админская команда. Перезагружает конфиги, модельный registry, routing policies и quota policies без потери persisted state.

#### `/admin_stats`

Админская команда. Показывает расширенную статистику использования, ошибок, fallback events, context handoff events и provider failures.

## 8. Требования к аналитике HTX

### 8.1 Источники данных

Разработать HTX integration layer только для read-only сценариев. На первом этапе нужны:

- список торговых пар;
- тикеры;
- candle data;
- order book snapshot;
- recent trades;
- базовые market metadata, если доступны;
- rate limit awareness.

### 8.2 Разрешенные аналитические use cases

- анализ одного символа;
- обзор краткосрочного движения;
- сравнение двух-трех активов;
- текстовый разбор волатильности;
- анализ объема и импульса;
- подготовка краткого сценарного плана;
- резюме состояния рынка на основе последних свечей.

### 8.3 Запрещенные действия

- открытие ордеров;
- закрытие ордеров;
- отмена ордеров;
- перевод средств;
- управление кошельком;
- любые POST/DELETE/PUT операции на HTX trading endpoints.

## 9. Нефункциональные требования

### 9.1 Локальный запуск

Система обязана запускаться на локальном ПК через Docker Compose.

### 9.2 Репродуцируемость

После заполнения `.env` проект должен подниматься одной командой. Все порты, healthchecks, volume mounts и env vars должны быть задокументированы.

### 9.3 Наблюдаемость

Нужны:

- structured logs;
- request IDs;
- conversation IDs;
- route decision IDs;
- context handoff IDs;
- provider/model audit trail;
- usage audit trail.

### 9.4 Безопасность

- Не хранить секреты в коде.
- Все ключи только в `.env`.
- Админские Telegram ID в allowlist.
- Не логировать API keys и raw bearer tokens.
- Не возвращать технические stack trace пользователю.
- Default deny на опасные admin actions.

## 10. Архитектурная модель

### 10.1 Предлагаемый стек

- Python 3.12
- FastAPI для локального gateway/API layer
- aiogram для Telegram bot layer
- SQLite для основной локальной persistence
- SQLAlchemy + Alembic для ORM и migrations
- httpx для внешних API
- Pydantic для схем
- Docker Compose для runtime
- pytest для тестов

### 10.2 Целевое дерево проекта

```text
app/
bot/
core/
providers/
routing/
accounting/
quotas/
context/
storage/
admin/
tests/
scripts/
docs/
docker/
examples/
.env.example
docker-compose.yml
Makefile
README.md
```

### 10.3 Модульное разделение

#### `bot/`

Telegram handlers, command routing, response formatting, access control.

#### `app/`

FastAPI application, health endpoints, internal API facade, optional OpenAI-compatible interface.

#### `core/`

Shared schemas, config loader, enums, common exceptions, request ID utilities.

#### `providers/`

AI provider adapters и HTX read-only adapter. HTX adapter должен быть выделен в отдельный подмодуль, отличающийся от AI providers.

#### `routing/`

Model registry, routing policies, fallback engine, route decision trace.

#### `accounting/`

Token usage service, cost estimation, usage reporting.

#### `quotas/`

Quota rules, thresholds, warning policies, hard-stop policies, premium gate.

#### `context/`

Conversation state, rolling memory, context compression, handoff package builder, restore pipeline.

#### `storage/`

DB models, repositories, migrations, SQLite setup.

#### `admin/`

Admin services, diagnostics, usage exports, runtime control actions.

#### `tests/`

Unit, integration, contract, smoke, e2e.

## 11. Внутренние сервисы и их контракты

### 11.1 HTX Market Data Service

Обязан:

- читать данные из HTX API;
- валидировать response shape;
- нормализовать market data;
- кэшировать краткоживущие ответы;
- не содержать торговых методов;
- возвращать диагностически понятные ошибки.

### 11.2 AI Provider Adapter Interface

Каждый AI adapter обязан предоставлять:

- provider ID;
- model list;
- supports streaming: true/false;
- supports system prompt: true/false;
- token usage semantics;
- invoke(request) -> normalized response;
- healthcheck() -> normalized health result;
- estimate_cost(usage) -> cost result.

### 11.3 Routing Service

Обязан:

- принимать normalized AI request;
- получать policy mode;
- проверять quota state;
- проверять provider health;
- строить ordered candidate chain;
- выполнять попытку на первой подходящей модели;
- при ошибке переводить на следующий кандидат;
- записывать reason codes.

### 11.4 Context State Service

Обязан:

- хранить conversation state;
- хранить последние user intents;
- хранить важные HTX market facts;
- хранить активные ограничения и выбранный режим;
- формировать compact summary;
- формировать handoff package для новой модели;
- уметь восстанавливать состояние между запросами.

### 11.5 Token Accounting Service

Обязан:

- писать usage record на каждый AI request;
- агрегировать usage по окнам времени;
- считать warning threshold events;
- считать fallback-after-quota events;
- хранить uncertain token flags, если usage неполный.

### 11.6 Quota Policy Engine

Обязан:

- проверять quota before request;
- проверять warning thresholds;
- блокировать запрос при hard stop;
- предлагать downgrade или switch;
- поддерживать per-model и per-provider quotas.

## 12. Сценарий бесшовного переключения модели

### 12.1 Триггеры переключения

- квота модели исчерпана;
- квота провайдера исчерпана;
- провайдер вернул quota/rate limit error;
- модель недоступна;
- модель вернула invalid response;
- пользователь вручную выбрал другую модель;
- текущий режим запрещает дальнейшее использование дорогой модели.

### 12.2 Алгоритм переключения

1. Зафиксировать причину переключения.
2. Достать текущий conversation state.
3. Построить `handoff summary`.
4. Выделить:
   - текущий аналитический вопрос;
   - последние выводы модели;
   - последние данные HTX, еще актуальные на момент handoff;
   - ограничения по формату ответа;
   - risk notes;
   - user preferences.
5. Выбрать новую модель согласно policy.
6. Передать новой модели:
   - system rules;
   - handoff summary;
   - last user request;
   - optional delta update по рынку, если данные устарели.
7. Продолжить обработку.
8. Сообщить пользователю:
   - была ли замена;
   - какая модель была до и после;
   - была ли сохранена и передана память;
   - были ли потери детализации.

### 12.3 Требования к качеству handoff

- handoff не должен быть просто raw transcript dump;
- handoff должен быть компактным и целевым;
- handoff должен содержать только полезный рабочий контекст;
- handoff должен быть versioned;
- handoff должен логироваться как отдельное событие.

## 13. Routing policy

### 13.1 Режимы

- `free_only`: использовать только бесплатные или условно бесплатные модели;
- `balanced`: сначала недорогие и надежные модели, затем fallback;
- `premium`: допускаются лучшие модели, но только если policy разрешает и budget не превышен.

### 13.2 Поля выбора маршрута

- task type;
- message size;
- context size;
- current mode;
- available quota;
- provider health;
- estimated latency;
- recent failure history;
- premium lock state;
- context handoff requirement.

### 13.3 Обязательные причины fallback

- timeout
- quota_exceeded
- rate_limit
- invalid_response
- provider_unavailable
- policy_rejection

### 13.4 Пример route decision

```text
request_type=market_analysis
mode=balanced
current_model=glm-5.1
reason_current_model_excluded=quota_warning_then_hard_stop
candidate_chain=deepseek-chat -> qwen-coder-plus -> fallback-small
selected_model=deepseek-chat
handoff_required=true
handoff_summary_version=v1
```

## 14. Учет токенов и затрат

### 14.1 Что хранить в usage record

- request_id
- conversation_id
- telegram_user_id
- provider_id
- model_id
- input_tokens
- output_tokens
- total_tokens
- reasoning_tokens
- cached_tokens
- latency_ms
- status
- error_code
- cost_estimate
- cost_currency
- quota_scope
- warning_triggered
- fallback_triggered
- context_handoff_triggered
- uncertain_usage
- created_at

### 14.2 Thresholds

Нужно поддержать минимум три порога:

- `warning_threshold_pct`
- `critical_threshold_pct`
- `hard_stop_threshold_pct`

### 14.3 Поведение порогов

#### Warning

Бот предупреждает пользователя и предлагает краткий ответ или смену модели.

#### Critical

Бот перед отправкой длинного запроса обязан предупредить, что ответ может прерваться или быть обрезан.

#### Hard stop

Бот не должен отправлять запрос в модель. Должен инициировать policy fallback или предложить switch.

## 15. Persistence

### 15.1 База данных

Использовать SQLite как primary local storage. Схема должна быть подготовлена для будущей миграции в PostgreSQL.

### 15.2 Обязательные таблицы

- `users`
- `telegram_chats`
- `conversation_sessions`
- `conversation_messages`
- `context_snapshots`
- `context_handoffs`
- `provider_registry`
- `model_registry`
- `provider_health_events`
- `usage_records`
- `quota_states`
- `route_decisions`
- `htx_market_snapshots`
- `admin_events`

### 15.3 Требования к context snapshots

Каждый snapshot должен хранить:

- session ID;
- snapshot version;
- summary text;
- last market facts;
- active mode;
- active model;
- token budget state;
- created_at;
- compression method.

## 16. API и локальный gateway layer

### 16.1 Внутренние endpoints

Минимум:

- `GET /health`
- `GET /health/deep`
- `GET /providers`
- `GET /models`
- `GET /usage/summary`
- `POST /internal/ask`
- `POST /internal/switch-model`
- `POST /internal/context/handoff`

### 16.2 Опциональный OpenAI-compatible facade

Если реализуется в первой итерации, то минимум:

- `POST /v1/chat/completions`
- `GET /v1/models`

Этот facade не обязан быть публичным. Его задача: дать единый локальный интерфейс для внутренних сервисов и будущего расширения.

## 17. Docker runtime

### 17.1 Обязательные сервисы

- `bot`
- `gateway`
- `db-init` или встроенная миграционная инициализация

### 17.2 Допустимые дополнительные сервисы

- `watcher` или `scheduler` для HTX refresh tasks;
- `test-runner` для smoke tests;
- `admin-cli` как отдельный utility container, если это упростит эксплуатацию.

### 17.3 Требования к docker-compose

Для каждого сервиса должны быть явно заданы:

- image/build;
- command;
- env file;
- volumes;
- ports;
- healthcheck;
- restart policy;
- dependency conditions.

## 18. Тестовая стратегия

### 18.1 Unit tests

Обязательные unit-тесты:

- route selection;
- fallback logic;
- quota warning logic;
- hard stop logic;
- usage aggregation;
- context compression;
- handoff package building;
- HTX response normalization.

### 18.2 Integration tests

Обязательные integration-тесты:

- Telegram command flow;
- `/ask` happy path;
- `/switch_model` with context handoff;
- quota warning path;
- provider fallback path;
- HTX API read-only analysis path.

### 18.3 Contract tests

- HTX adapter contract;
- AI provider adapter contract;
- local gateway contract.

### 18.4 Smoke tests

- docker compose up;
- health endpoints;
- bot readiness;
- one analysis request through Telegram or internal API;
- one forced fallback case;
- one manual switch case.

### 18.5 E2E сценарий приемки

Обязательный сценарий:

1. Пользователь задает вопрос по рынку HTX.
2. Активна модель `GLM 5.1`.
3. Система фиксирует низкий остаток квоты.
4. Бот предупреждает пользователя.
5. Пользователь подтверждает переключение.
6. Система сохраняет контекст.
7. Новая модель получает handoff.
8. Ответ продолжается без потери логики.
9. В БД отражены usage, route_decision и context_handoff events.

## 19. Документация, которую обязан произвести QwenCode

После реализации в репозитории должны существовать:

- `README.md`
- `docs/01-product-overview.md`
- `docs/02-architecture.md`
- `docs/03-local-setup.md`
- `docs/04-env-vars.md`
- `docs/05-provider-registry.md`
- `docs/06-routing-policy.md`
- `docs/07-token-accounting.md`
- `docs/08-quota-management.md`
- `docs/09-telegram-commands.md`
- `docs/10-testing.md`
- `docs/11-troubleshooting.md`
- `docs/12-operations-runbook.md`
- `docs/13-weekly-refresh.md`
- `docs/14-security.md`
- `docs/15-acceptance-checklist.md`

## 20. Этапы реализации

### Phase 0. Discovery

QwenCode обязан:

- извлечь требования из этого ТЗ;
- выписать assumptions;
- выписать unknowns;
- предложить стек;
- зафиксировать acceptance criteria.

### Phase 1. Architecture RFC

QwenCode обязан подготовить:

- system context diagram;
- module diagram;
- sequence diagrams:
  - normal analysis flow;
  - fallback flow;
  - quota-exhausted flow;
  - manual switch flow;
- storage schema;
- env var registry;
- error taxonomy;
- security model;
- test strategy.

### Phase 2. Repository Blueprint

QwenCode обязан создать точное дерево репозитория и зафиксировать назначение каждого каталога.

### Phase 3. Minimal Vertical Slice

Минимальный вертикальный срез обязан включать:

- Telegram receive/send;
- HTX read-only market fetch;
- один AI provider adapter;
- routing policy;
- token logging;
- quota checks;
- health command;
- smoke tests.

### Phase 4. Multi-Provider Expansion

Добавить:

- несколько AI adapters;
- fallback chain;
- mode switching;
- context handoff;
- admin usage commands;
- provider health tracking.

### Phase 5. Hardening

Добавить:

- retries with jitter;
- timeout policies;
- circuit breaker semantics;
- malformed response protection;
- config reload safety;
- stronger fixtures and mocks.

### Phase 6. Final Documentation

Разрешена только после green tests.

## 21. Критерии приемки

Работа считается принятой только если:

- проект запускается локально через Docker Compose;
- Telegram bot отвечает на команды;
- HTX integration работает в read-only режиме;
- минимум две AI модели доступны через routing layer;
- есть fallback между моделями;
- есть предупреждение о низком остатке токенов;
- есть hard stop при исчерпании;
- есть ручное и автоматическое переключение модели;
- есть сохранение и восстановление контекста;
- usage и route decision сохраняются в БД;
- health checks зеленые;
- тесты проходят;
- документация синхронизирована с кодом.

## 22. Явные запреты для QwenCode

- не реализовывать торговые операции;
- не пропускать RFC перед core architecture;
- не смешивать Telegram handlers с routing/accounting logic;
- не хардкодить модели и ключи в код;
- не объявлять задачу завершенной без test evidence;
- не оставлять неопределенные конфиги и скрытые шаги;
- не использовать продакшн-деплой как обязательную часть первой версии.

## 23. Команды, которые должен использовать QwenCode при реализации

Ниже перечислены ожидаемые команды. Они являются обязательными к документированию после появления соответствующих файлов, но сейчас служат как baseline contract:

```powershell
docker compose up --build -d
docker compose ps
docker compose logs bot
docker compose logs gateway
docker compose down
pytest
pytest -m smoke
alembic upgrade head
```

## 24. Ожидаемый формат отчетности от QwenCode

Каждый отчет по этапу должен содержать:

1. Goal restatement
2. Assumptions and unknowns
3. Architecture decision summary
4. File tree
5. Full file contents for current critical batch
6. Commands run
7. Tests run
8. Expected outcomes
9. Documentation updates
10. Remaining risks and next step

## 25. Residual risks, которые надо контролировать в ходе реализации

- нестабильность внешних AI providers;
- неполные usage metrics от некоторых моделей;
- устаревание рыночного контекста между handoff и продолжением запроса;
- неожиданные rate limits HTX;
- избыточный размер context snapshot;
- деградация качества ответа при сжатии контекста;
- избыточная связанность между routing и context services.

## 26. Следующий шаг для QwenCode

Немедленно после получения этого документа QwenCode должен начать с `Phase 0` и затем подготовить `Phase 1 Architecture RFC` без перехода к широкой реализации до утверждения архитектуры.

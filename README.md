# WORED

WORED — это local-first Telegram-бот для мониторинга крипторынка и AI-аналитики по данным HTX. Активный runtime в этом репозитории — корневой стек из `docker-compose.yml` с пятью сервисами: `chatbot`, `collector`, `webui`, `postgres` и `redis`.

В репозитории также есть `D:\WORED\hypercube` — отдельный подпроект AI gateway со своей архитектурой и документацией. Он не входит в корневой compose-стек, описанный в этом README.

## Что уже работает

- `collector` держит живое подключение к HTX WebSocket, хранит актуальные тикеры в Redis, проверяет алерты каждые 5 минут и пишет AI-журнал каждые 15 минут.
- `chatbot` работает на `aiogram 3`, читает состояние рынка из Redis/Postgres, поддерживает меню, свободный текст и Telegram Forecast Lab, а также маршрутизирует AI-запросы через fallback-цепочку.
- `webui` поднимает браузерную панель управления на FastAPI, даёт детальный просмотр свечей, объёмов, RSI, MACD, AI journal и последних alert-событий, а также поддерживает session auth и admin actions.
- `postgres` хранит историю алертов и снимки AI-журнала.
- `redis` хранит горячий кэш тикеров и pub/sub-события для админских алертов.

## Текущие слабые места

- В проекте всё ещё есть legacy и placeholder-модули, которые не лежат на живом runtime path.
- `market_tickers` и `ai_usage_log` описаны в схеме БД, но не являются полностью задействованными runtime-подсистемами.

## Актуальный AI-стек WORED

Решение владельца от 2026-09-25: активны **только** модели подписки Ollama PRO
(cloud-first) и локальная модель Bonsai как failover. Реестр и цепочки заданы в
`chatbot/ai/models.py`; цепочки идут cloud→local (быстро и качественно — затем
бесплатный локальный отказоустойчивый fallback).

Primary — **Ollama Cloud** (OpenAI-совместимый endpoint `https://ollama.com/v1`,
ключ `OLLAMA_CLOUD_API_KEY`):

| Роль | Модель по умолчанию (chain: cloud → local failover) |
|---|---|
| Premium / strategist | `glm-5.3:cloud` → `bonsai-27b:lmstudio-q1` |
| Analyst / reasoning | `glm-5.3:cloud` → `bonsai-27b:lmstudio-q1` |
| Worker / быстрые задачи | `deepseek-v4.1-flash:cloud` → `bonsai-27b:lmstudio-q1` |

Local failover — **Bonsai-27B** на рабочей станции (`provider="local_ollama"`,
endpoint `LOCAL_LLM_BASE_URL` по умолчанию `http://127.0.0.1:8088/v1`, без авторизации).

**Законсервировано (NOT активный runtime):** вся бесплатная gateway-подсистема
роутинга — OmniRoute, NVIDIA NIM / Nemotron, TokenRouter (`kimi-k3-free`),
DashScope/Qwen, minimax-oracle, вместе со шлюзовым стеком
(`provider_gateway`, `provider_adapters`, `usage_ledger`, `budget_policy`,
`reflector`/`strategy_learner`) и реестром `provider_registry.json` — перенесена в
top-level `free_routing_archive/` и не собирается pytest'ом (`norecursedirs`).
Она упоминается здесь только как архив, а не как рабочий fallback-tier.

Переменные окружения для управления моделями (реальные имена из `models.py`):
- `OLLAMA_CHATBOT_WORKER_MODEL=deepseek-v4.1-flash:cloud`
- `OLLAMA_CHATBOT_ANALYST_MODEL=glm-5.3:cloud`
- `OLLAMA_CHATBOT_PREMIUM_MODEL=glm-5.3:cloud`
- `LOCAL_LLM_MODEL=bonsai-27b:lmstudio-q1`

## Архитектура runtime

```text
Пользователь Telegram
    ->
chatbot (aiogram 3)
    -> Redis: кэш тикеров, market_alerts pub/sub
    -> Postgres: история alerts, история ai_journal
    -> AI providers: Ollama Cloud (primary) → local Bonsai (failover)

Браузер
    ->
webui (FastAPI + Lightweight Charts)
    -> Redis: live watchlist snapshot
    -> Postgres: alerts, ai_journal
    -> HTX REST: исторические свечи и индикаторы

HTX WebSocket / HTX REST
    ->
collector
    -> Redis: ключи ticker:*, события market_alerts
    -> Postgres: строки alerts, строки ai_journal
```

## Структура репозитория

```text
D:\WORED
├── chatbot/              Telegram UI и AI routing
├── collector/            HTX ingestion, индикаторы, scheduler
├── webui/                Браузерная панель управления и API для графиков
├── db/                   Схема и bootstrap Postgres
├── docs/                 Документация корневого проекта
├── hypercube/            Отдельный gateway-подпроект
├── docker-compose.yml    Активный локальный runtime
├── .env.example          Безопасный шаблон конфигурации
└── README.md
```

## Быстрый старт

PowerShell:

```powershell
Set-Location D:\WORED
Copy-Item .env.example .env
# Отредактируй .env: добавь TELEGRAM_TOKEN, OLLAMA_CLOUD_API_KEY, пароли Postgres/WebUI
docker-compose up --build -d
docker-compose ps
docker-compose logs --tail 50 collector
docker-compose logs --tail 50 chatbot
Invoke-WebRequest http://localhost:8080/api/health
```

Bash:

```bash
cd /d/WORED
cp .env.example .env
# nano .env — добавь ключи
docker-compose up --build -d
docker-compose ps
docker-compose logs --tail 50 collector
docker-compose logs --tail 50 chatbot
curl -fsS http://localhost:8080/api/health
```

## Hermes Agent

Hermes работает как host-level инженерный агент для WORED. Профиль `wored` находится в `C:\Users\<user>\AppData\Local\hermes\profiles\wored\` и содержит `SOUL.md`, `MEMORY.md`, `USER.md`, `config.yaml`.

См. `AGENTS.md` для полного контекста и guardrails.

## Безопасность

- Не коммитьте `.env`, `.env.postgres` и файлы с ключами.
- API-ключи храните с правами 600/700.
- Разрушительные команды (`docker compose down -v`, `rm -rf`, `git push`) выполняйте только с явным подтверждением.

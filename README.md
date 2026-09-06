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

Primary-провайдер — **Ollama Cloud** (OpenAI-совместимый endpoint `https://ollama.com/v1`):

| Роль | Модель по умолчанию |
|---|---|
| Premium / сложные задачи | `glm-5.2` |
| Analyst / reasoning | `deepseek-v4-pro` |
| Worker / быстрые задачи | `deepseek-v4-flash` |
| Reviewer / second opinion | `minimax-m3` |
| Oracle / fallback | `kimi-k2.6`, `kimi-k2:1t` |

Дешёвый utility-tier: **TokenRouter** (`moonshotai/kimi-k3-free`).

Fallback-tier: **NVIDIA NIM** (`mistralai/mistral-nemotron`, `minimaxai/minimax-m3`) и **OpenRouter**.

**Qwen/DashScope исключены** из активного стека.

Переменные окружения для управления моделями:
- `OLLAMA_WORKER_MODEL=deepseek-v4-flash`
- `OLLAMA_ANALYST_MODEL=deepseek-v4-pro`
- `OLLAMA_PREMIUM_MODEL=glm-5.2`

## Архитектура runtime

```text
Пользователь Telegram
    ->
chatbot (aiogram 3)
    -> Redis: кэш тикеров, market_alerts pub/sub
    -> Postgres: история alerts, история ai_journal
    -> AI providers: Ollama Cloud primary, NVIDIA/OpenRouter fallback, Kimi utility

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

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
- `MiniMax` переведён на fail-fast fallback и не блокирует UX, но его качество всё ещё зависит от внешнего NVIDIA NIM endpoint.
- `market_tickers` и `ai_usage_log` описаны в схеме БД, но не являются полностью задействованными runtime-подсистемами.

## Архитектура runtime

```text
Пользователь Telegram
    ->
chatbot (aiogram 3)
    -> Redis: кэш тикеров, market_alerts pub/sub
    -> Postgres: история alerts, история ai_journal
    -> AI providers: Qwen auto-switch worker, Qwen reasoning analyst chain with GLM fallback, Qwen strategist chain with GLM fallback, MiniMax reviewer

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
docker-compose up --build -d
docker-compose ps
docker-compose logs --tail 50 collector
docker-compose logs --tail 50 chatbot
curl http://localhost:8080/api/health
```

После старта браузерная панель доступна по адресу `http://localhost:8080`.

Дополнительные страницы:

- `http://localhost:8080/alerts` — полная history-страница алертов с ack/reopen.
- `http://localhost:8080/predictions` — Prediction Lab с почасовым multi-model forecast, fact-vs-forecast drill-down и success/miss scorecard по каждому часу.
- `http://localhost:8080/journal` — список AI journal и drill-down по отдельным entry.
- `http://localhost:8080/login` — login page, если включён `WEBUI_AUTH_ENABLED=true`.

## Карта документации

- [Обзор проекта](docs/01-overview.md)
- [Архитектура](docs/02-architecture.md)
- [Установка и запуск](docs/03-setup.md)
- [Конфигурация](docs/04-configuration.md)
- [Эксплуатация и проверка](docs/05-operations.md)
- [Сильные и слабые стороны, технический аудит](docs/06-audit.md)
- [Безопасность](docs/07-security.md)

## Вариант веб-интерфейса

Для этого репозитория выбран вариант `FastAPI + custom dashboard shell + TradingView Lightweight Charts`, а не тяжёлый отдельный React-admin стек. Причина простая: текущий runtime уже Python/Docker-first, а главная ценность новой морды — детальные рыночные графики и операционный обзор без лишней Node-инфраструктуры.

Официальные источники, на которые опирался выбор:

- [TradingView Lightweight Charts Docs](https://tradingview.github.io/lightweight-charts/docs)
- [Tabler](https://tabler.io/)
- [Tremor](https://www.tremor.so/docs/getting-started/introduction)

## WebUI auth

По умолчанию auth отключён, чтобы не ломать уже существующий локальный runtime. Чтобы включить защиту браузерного контура, задай в `.env`:

```env
WEBUI_AUTH_ENABLED=true
WEBUI_ADMIN_USERNAME=admin
WEBUI_ADMIN_PASSWORD=replace_with_strong_password
WEBUI_SESSION_SECRET=replace_with_random_secret
```

После этого `dashboard`, `alerts`, `journal` и data API будут требовать login-сессию, а healthcheck `GET /api/health` останется публичным для контейнерного probe.

## Проверенные команды

Во время этого прохода валидируются:

```powershell
docker-compose config
docker-compose run --rm chatbot pytest tests -q
docker-compose run --rm collector pytest tests -q
docker-compose run --rm webui pytest tests -q
python scripts/qwen_live_check.py --json
python -m compileall D:\WORED\chatbot D:\WORED\collector D:\WORED\webui
docker-compose up -d --build webui
docker-compose ps
Invoke-WebRequest http://localhost:8080/api/health
```

## Prediction Lab

`webui` теперь отдаёт `/predictions`.

Что делает новый контур:

- берёт символ из `WATCHLIST` и горизонт `1, 2, 3, 4, 8, 16, 24` часов;
- просит каждую доступную модель построить прогноз по каждому часу от общей базовой цены;
- сохраняет запросы в `forecast_requests`, `forecast_model_runs`, `forecast_points`;
- поручает `collector` каждые 5 минут оценивать наступившие forecast points по фактической цене.

Правило оценки:

- неверное направление даёт `0% success / 100% miss`;
- верное направление даёт success-процент по близости прогнозного `change_pct` к фактическому `change_pct`;
- точное совпадение изменения даёт `100% success`.

Важно:

- отдельные forecast-only ключи не нужны, используются обычные `DASHSCOPE_API_KEY`, `GLM_API_KEY`, `GOOGLE_API_KEY`, `MINIMAX_API_KEY`;
- если ключа нет, модель помечается на странице как offline;
- в этом rollout smoke-check ограничен нестоимостными HTTP-проверками и тестами, без автоматического запуска живого прогноза через провайдеров.

## Примечания по границам проекта

- `chatbot/loader.py` — это legacy-файл под aiogram 2, он не используется активным runtime.
- `chatbot/context/*`, `chatbot/ui/*`, `collector/alerts/detector.py` и `collector/scheduler/briefing.py` сейчас содержат заглушки или не лежат на основном runtime path.
- `DASHSCOPE_API_KEY` теперь участвует в активном worker-path и в Prediction Lab worker auto-switch chain `qwen3.6-flash -> qwen3.5-flash -> qwen-flash -> glm-4-flash`.

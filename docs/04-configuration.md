# Конфигурация

## Назначение

Этот документ перечисляет переменные окружения, которые реально участвуют в активном runtime `D:\WORED`, и объясняет, какие сервисы их используют.

## Источники конфигурации

- `.env`
- `.env.example`
- `docker-compose.yml`
- `chatbot/ai/models.py`
- `collector/main.py`
- `webui/app.py`

## Обязательные переменные

| Переменная | Пример | Где используется | Комментарий |
| --- | --- | --- | --- |
| `TELEGRAM_TOKEN` | `1234567890:token` | `chatbot` | нужен для запуска polling-бота |
| `TELEGRAM_ADMIN_ID` | `123456789` | `chatbot` | получает push-алерты и admin-функции |
| `DASHSCOPE_API_KEY` | `sk-...` | `chatbot`, `webui` | включает Qwen worker chain, analyst chain и strategist chain |
| `GLM_API_KEY` | `replace_me` | `chatbot`, `webui` | резервный fallback для analyst/strategist chain и части worker-path |
| `POSTGRES_USER` | `bot` | `postgres`, compose | пользователь БД при bootstrap |
| `POSTGRES_PASSWORD` | `change_me` | `postgres`, compose | пароль БД |
| `POSTGRES_DB` | `trading` | `postgres`, compose | имя основной БД |
| `DATABASE_URL` | `postgresql+asyncpg://bot:...@postgres:5432/trading` | `chatbot`, `collector`, `webui` | строка подключения к Postgres |
| `REDIS_URL` | `redis://redis:6379/0` | `chatbot`, `collector`, `webui` | строка подключения к Redis |

## Необязательные runtime-переменные

| Переменная | По умолчанию | Где используется | Комментарий |
| --- | --- | --- | --- |
| `GLM_MODEL` | `glm-5.1` | `chatbot`, `webui` | базовый GLM model id для fallback-path |
| `GOOGLE_API_KEY` | пусто | `chatbot`, `webui` | включает Gemini flash worker fallback |
| `MINIMAX_API_KEY` | пусто | `chatbot`, `webui` | включает Oracle path через NVIDIA NIM |
| `WATCHLIST` | `btcusdt,ethusdt` | `chatbot`, `collector`, `webui` | активный список торговых пар |
| `ALERT_SPIKE_THRESHOLD` | `3.0` | `chatbot` | порог показа в настройках и Telegram UX |
| `LOG_LEVEL` | `INFO` | частично | уровень логирования |
| `WEBUI_PORT` | `8080` | compose | внешний порт `webui` |
| `HTX_REST_URL` | `https://api.huobi.pro` | `collector`, `webui` | базовый REST endpoint HTX |
| `WEBUI_AUTH_ENABLED` | `false` | `webui` | включает browser auth |
| `WEBUI_ADMIN_USERNAME` | `admin` | `webui` | логин webui |
| `WEBUI_ADMIN_PASSWORD` | пусто | `webui` | пароль webui |
| `WEBUI_SESSION_SECRET` | пусто | `webui` | явный session secret |
| `WEBUI_INTERNAL_URL` | `http://webui:8000` | `chatbot` | внутренний base URL для запуска forecast через webui API |
| `WEBUI_PUBLIC_BASE_URL` | `http://localhost:8080` | `chatbot` | публичный base URL для кнопки `Matrix` в Telegram |
| `WEBUI_INTERNAL_TOKEN` | пусто | `chatbot`, `webui` | общий токен для internal prediction API; если пусто, вычисляется автоматически |

## Модельные цепочки

| Переменная | По умолчанию | Где используется | Комментарий |
| --- | --- | --- | --- |
| `WORKER_QWEN_MODEL` | `qwen3.6-flash` | `chatbot`, `webui` | primary worker model |
| `WORKER_QWEN_FALLBACKS` | `qwen3.5-flash,qwen-flash` | `chatbot`, `webui` | worker fallback chain before GLM |
| `WORKER_GLM_FALLBACK_MODEL` | `glm-4-flash` | `chatbot`, `webui` | worker fallback after Qwen |
| `WORKER_GEMINI_FALLBACK_MODEL` | `gemini-3-flash-preview` | `chatbot`, `webui` | final flash-grade worker fallback |
| `ANALYST_QWEN_MODEL` | `qwen3.6-35b-a3b` | `chatbot`, `webui` | primary analyst reasoning model |
| `ANALYST_QWEN_FALLBACKS` | `qwen3.6-27b` | `chatbot`, `webui` | analyst fallback chain before GLM |
| `ANALYST_GLM_FALLBACK_MODEL` | `glm-5.1` | `chatbot`, `webui` | final analyst fallback |
| `PREMIUM_QWEN_MODEL` | `qwen3.6-27b` | `chatbot`, `webui` | primary strategist reasoning model |
| `PREMIUM_QWEN_FALLBACKS` | `qwen3.6-35b-a3b` | `chatbot`, `webui` | strategist fallback chain before GLM |
| `PREMIUM_GLM_FALLBACK_MODEL` | `glm-5.1` | `chatbot`, `webui` | final strategist fallback |

## Legacy и исследовательские переменные

Эти переменные могут жить в локальном `.env`, но не относятся к активному root runtime path:

- `PERPLEXITY_API_KEY`
- `DEFAULT_AI_MODEL`
- `BRIEFING_HOUR_UTC`
- `TIMEZONE_OFFSET_HOURS`
- `HTX_ACCESS_KEY`
- `HTX_SECRET_KEY`
- `HTX_PASSPHRASE`

## Матрица по сервисам

### Postgres

Использует при bootstrap:

- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_DB`

### Collector

Реально использует:

- `DATABASE_URL`
- `REDIS_URL`
- `WATCHLIST`
- `HTX_REST_URL`

### Chatbot

Реально использует:

- `TELEGRAM_TOKEN`
- `TELEGRAM_ADMIN_ID`
- `DASHSCOPE_API_KEY`
- `GLM_API_KEY`
- `GLM_MODEL`
- `GOOGLE_API_KEY`
- `MINIMAX_API_KEY`
- `WEBUI_INTERNAL_URL`
- `WEBUI_PUBLIC_BASE_URL`
- `WEBUI_INTERNAL_TOKEN`
- `DATABASE_URL`
- `REDIS_URL`
- `WATCHLIST`
- `ALERT_SPIKE_THRESHOLD`

### Webui

Реально использует:

- `WATCHLIST`
- `DATABASE_URL`
- `REDIS_URL`
- `HTX_REST_URL`
- `DASHSCOPE_API_KEY`
- `GLM_API_KEY`
- `GOOGLE_API_KEY`
- `MINIMAX_API_KEY`
- `WEBUI_INTERNAL_URL`
- `WEBUI_PUBLIC_BASE_URL`
- `WEBUI_INTERNAL_TOKEN`
- `WEBUI_AUTH_ENABLED`
- `WEBUI_ADMIN_USERNAME`
- `WEBUI_ADMIN_PASSWORD`
- `WEBUI_SESSION_SECRET`

## Prediction Lab provider keys

`/predictions` не вводит отдельные ключи. Он использует тот же набор provider keys, что уже живёт в root runtime:

- `DASHSCOPE_API_KEY`
- `GLM_API_KEY`
- `GLM_MODEL`
- `GOOGLE_API_KEY`
- `MINIMAX_API_KEY`

Особенности:

- `worker` доступен при `DASHSCOPE_API_KEY` и умеет переключаться на `GLM_API_KEY`, а затем на `GOOGLE_API_KEY` flash fallback;
- `analyst` доступен при `DASHSCOPE_API_KEY` и умеет переключаться по reasoning-chain `qwen3.6-35b-a3b -> qwen3.6-27b -> glm-5.1`;
- `premium` доступен при `DASHSCOPE_API_KEY` и умеет переключаться по reasoning-chain `qwen3.6-27b -> qwen3.6-35b-a3b -> glm-5.1`;
- `minimax` доступен только если `MINIMAX_API_KEY` задан и начинается с `nvapi-`.

## Команды валидации

Проверить, что Compose читает переменные:

```powershell
docker-compose config
```

Проверить, что `chatbot` видит DashScope key:

```powershell
docker-compose exec chatbot python -c "import os; print(bool(os.getenv('DASHSCOPE_API_KEY')))"
```

Проверить, что `webui` видит strategist defaults:

```powershell
docker-compose exec webui python -c "import os; print(os.getenv('PREMIUM_QWEN_MODEL', 'qwen3.6-27b'))"
```

Проверить, что auth включён именно так, как ожидается:

```powershell
docker-compose exec webui python -c "import os; print(os.getenv('WEBUI_AUTH_ENABLED'), bool(os.getenv('WEBUI_ADMIN_PASSWORD')))"
```

## Рекомендации по секретам

- реальные ключи хранить только в `.env`;
- `.env.example` держать полностью обезличенным;
- не вставлять provider keys в docs, логи и тикеты;
- считать вывод `docker-compose config` чувствительным, потому что он разворачивает `.env` в открытый текст.

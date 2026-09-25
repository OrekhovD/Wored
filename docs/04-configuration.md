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
| `OLLAMA_CLOUD_API_KEY` | `...` | `chatbot` | ключ Ollama Cloud Pro для chatbot-цепочек (`ai/models.py`) |
| `OLLAMA_API_KEY` | `...` | `webui` | ключ Ollama Cloud Pro для forecast-движка (`prediction_engine.py`) |
| `POSTGRES_USER` | `bot` | `postgres`, compose | пользователь БД при bootstrap |
| `POSTGRES_PASSWORD` | `change_me` | `postgres`, compose | пароль БД |
| `POSTGRES_DB` | `trading` | `postgres`, compose | имя основной БД |
| `DATABASE_URL` | `postgresql+asyncpg://bot:...@postgres:5432/trading` | `chatbot`, `collector`, `webui` | строка подключения к Postgres |
| `REDIS_URL` | `redis://redis:6379/0` | `chatbot`, `collector`, `webui` | строка подключения к Redis |

> **Архив бесплатного роутинга.** `DASHSCOPE_API_KEY`, `GLM_API_KEY`,
> `GOOGLE_API_KEY`, `MINIMAX_API_KEY` и все `*_QWEN_*` / `GLM_FALLBACK` цепочки
> относились к замороженной gateway-подсистеме (`free_routing_archive/`). Они
> **не читаются** активным runtime (`chatbot/ai/models.py`,
> `webui/prediction_engine.py`, `collector/`) и приведены ниже только как legacy.

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
| `HTX_LINEAR_SWAP_BASE_URL` | `https://api.hbdm.com` | `collector` | публичный REST endpoint USDT-M perpetual; producer не использует торговые ключи |
| `HTX_PERPETUAL_POLL_SECONDS` | `2` | `collector` | интервал обновления проверенного perpetual snapshot |
| `PAPER_CONTRACTS` | `BTC-USDT` | `collector` | контракты симулятора через запятую |
| `PAPER_MARKET_MODE` | `live` | `webui` | `demo` использует явно обозначенную тестовую цену; `live` требует проверенный perpetual-снимок в Redis без fallback |
| `PAPER_MARKET_MAX_AGE_SECONDS` | `5` | `webui` | максимальный возраст исходной рыночной метки времени; допустимо значение больше 0 и не больше 60 секунд |
| `WEBUI_AUTH_ENABLED` | `false` | `webui` | включает browser auth |
| `WEBUI_ADMIN_USERNAME` | `admin` | `webui` | логин webui |
| `WEBUI_ADMIN_PASSWORD` | пусто | `webui` | пароль webui |
| `WEBUI_SESSION_SECRET` | пусто | `webui` | явный session secret |
| `WEBUI_INTERNAL_URL` | `http://webui:8000` | `chatbot` | внутренний base URL для запуска forecast через webui API |
| `WEBUI_PUBLIC_BASE_URL` | `http://localhost:8080` | `chatbot` | публичный base URL для кнопки `Matrix` в Telegram |
| `WEBUI_INTERNAL_TOKEN` | пусто | `chatbot`, `webui` | общий токен для internal prediction API; если пусто, вычисляется автоматически |

## Модельные цепочки (активные: Ollama Cloud Pro → локальный Bonsai)

Chatbot (`chatbot/ai/models.py`) — облачный первичный кандидат, затем локальный
Bonsai как failover. Порядок ролей: `worker → analyst → premium`, внутри роли —
`*_ollama → *_bonsai`.

| Переменная | По умолчанию | Где используется | Комментарий |
| --- | --- | --- | --- |
| `OLLAMA_CHATBOT_WORKER_MODEL` | `deepseek-v4.1-flash:cloud` | `chatbot` | worker cloud model |
| `OLLAMA_CHATBOT_ANALYST_MODEL` | `glm-5.3:cloud` | `chatbot` | analyst cloud model |
| `OLLAMA_CHATBOT_PREMIUM_MODEL` | `glm-5.3:cloud` | `chatbot` | strategist cloud model |
| `LOCAL_LLM_MODEL` | `bonsai-27b:lmstudio-q1` | `chatbot`, `webui` | локальный failover для всех тиров |
| `LOCAL_LLM_BASE_URL` | `http://127.0.0.1:8088` | `chatbot`, `webui` | локальный ollama serve |

WebUI forecast (`webui/prediction_engine.py`) — **свои имена переменных и свой
порядок кандидатов** (роль `oracle` есть только здесь):

| Переменная | По умолчанию | Где используется | Комментарий |
| --- | --- | --- | --- |
| `OLLAMA_WORKER_MODEL` | `deepseek-v4.1-flash:cloud` | `webui` | worker cloud model |
| `OLLAMA_ANALYST_MODEL` | `glm-5.3:cloud` | `webui` | analyst cloud model |
| `OLLAMA_PREMIUM_MODEL` | `glm-5.3:cloud` | `webui` | strategist cloud model |
| `OLLAMA_ORACLE_MODEL` | `glm-5.3-flash:cloud` | `webui` | oracle cloud model |
| `OLLAMA_*_FALLBACK_MODEL` | (напр. `gemma4:31b:cloud`) | `webui` | облачный fallback каждой роли |
| `LOCAL_LLM_ROLES` | пусто (выключено) | `webui` | роли, для которых локальный кандидат идёт первым |

### Legacy (архив бесплатного роутинга — НЕ активны)

| Переменная | По умолчанию | Комментарий |
| --- | --- | --- |
| `GLM_MODEL` | `glm-5.1` | legacy GLM fallback id (gateway archived) |
| `WORKER_QWEN_MODEL` / `ANALYST_QWEN_MODEL` / `PREMIUM_QWEN_MODEL` и `*_FALLBACKS` | `qwen*` | замороженная Qwen/DashScope-цепочка |
| `WORKER_GLM_FALLBACK_MODEL` / `ANALYST_GLM_FALLBACK_MODEL` / `PREMIUM_GLM_FALLBACK_MODEL` | `glm-*` | замороженный GLM-tail |
| `WORKER_GEMINI_FALLBACK_MODEL` | `gemini-3-flash-preview` | замороженный Gemini fallback |

## Legacy и исследовательские переменные

Эти переменные могут жить в локальном `.env`, но не относятся к активному root runtime path:

- `DASHSCOPE_API_KEY` (архив бесплатного роутинга Qwen)
- `GLM_API_KEY` / `GLM_MODEL` (архив GLM-tail)
- `GOOGLE_API_KEY` (архив Gemini fallback)
- `MINIMAX_API_KEY` (архив NVIDIA NIM oracle)
- `LLM_ROUTING_MODE` / `LLM_PAID_ENABLED` / `LLM_REGISTRY_PATH` (архив шлюза)
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
- `OLLAMA_CLOUD_API_KEY`
- `OLLAMA_CLOUD_BASE_URL`
- `OLLAMA_CHATBOT_WORKER_MODEL` / `OLLAMA_CHATBOT_ANALYST_MODEL` / `OLLAMA_CHATBOT_PREMIUM_MODEL`
- `LOCAL_LLM_MODEL` / `LOCAL_LLM_BASE_URL`
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
- `OLLAMA_API_KEY` / `OLLAMA_BASE_URL`
- `OLLAMA_WORKER_MODEL` / `OLLAMA_ANALYST_MODEL` / `OLLAMA_PREMIUM_MODEL` / `OLLAMA_ORACLE_MODEL` (+ `*_FALLBACK_MODEL`)
- `LOCAL_LLM_ROLES` / `LOCAL_LLM_MODEL` / `LOCAL_LLM_BASE_URL` / `LOCAL_LLM_TIMEOUT`
- `WEBUI_INTERNAL_URL`
- `WEBUI_PUBLIC_BASE_URL`
- `WEBUI_INTERNAL_TOKEN`
- `WEBUI_AUTH_ENABLED`
- `WEBUI_ADMIN_USERNAME`
- `WEBUI_ADMIN_PASSWORD`
- `WEBUI_SESSION_SECRET`

## Prediction Lab provider keys

`/predictions` не вводит отдельные ключи. Он использует набор Ollama Cloud Pro,
который уже живёт в root runtime (`webui/prediction_engine.py`):

- `OLLAMA_API_KEY` (обязателен для облачных кандидатов)
- `OLLAMA_BASE_URL` (по умолчанию `https://ollama.com/v1`)
- `OLLAMA_WORKER_MODEL` / `OLLAMA_ANALYST_MODEL` / `OLLAMA_PREMIUM_MODEL` / `OLLAMA_ORACLE_MODEL` (+ `*_FALLBACK_MODEL`)
- `LOCAL_LLM_ROLES` / `LOCAL_LLM_MODEL` / `LOCAL_LLM_BASE_URL` (локальный failover)

Особенности:

- каждая роль (`worker`/`analyst`/`premium`/`oracle`) идёт облачным Pro-кандидатом,
  затем его `*_FALLBACK_MODEL`; NVIDIA NIM tail удалён из движка;
- если роль перечислена в `LOCAL_LLM_ROLES` (или `all`), локальный Bonsai идёт
  первым кандидатом, а облако — позади;
- ключевые слова `DASHSCOPE` / `nvapi-` / Qwen-цепочки больше не участвуют — они
  относятся к замороженному шлюзу в `free_routing_archive/`.

## Команды валидации

Проверить, что Compose читает переменные:

```powershell
docker-compose config
```

Проверить, что `chatbot` видит Ollama Cloud key:

```powershell
docker-compose exec chatbot python -c "import os; print(bool(os.getenv('OLLAMA_CLOUD_API_KEY')))"
```

Проверить, что `webui` видит strategist defaults:

```powershell
docker-compose exec webui python -c "import os; print(os.getenv('OLLAMA_PREMIUM_MODEL', 'glm-5.3:cloud'))"
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

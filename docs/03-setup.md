# Установка и запуск

## Назначение

Этот документ объясняет, как поднять корневой стек `D:\WORED` локально и как проверить, что он действительно жив.

## Предварительные требования

- Docker Desktop с поддержкой Compose
- доступ в интернет к:
  - Telegram Bot API
  - `api.huobi.pro`
  - настроенным AI provider endpoints
- Telegram bot token
- хотя бы один рабочий AI API key для корневого runtime

Рекомендуемая локальная среда:

- Windows с PowerShell
- Docker Desktop запущен до любого вызова `docker-compose`

## Файлы, которые участвуют в запуске

- `D:\WORED\.env.example`
- `D:\WORED\docker-compose.yml`
- `D:\WORED\db\init.sql`
- `D:\WORED\webui\Dockerfile`

## Шаг 1: Подготовить `.env`

PowerShell:

```powershell
Set-Location D:\WORED
Copy-Item .env.example .env
notepad .env
```

Bash:

```bash
cd /d/WORED
cp .env.example .env
nano .env
```

Нужно обязательно заменить:

- `TELEGRAM_TOKEN`
- `TELEGRAM_ADMIN_ID`
- `GLM_API_KEY`
- `POSTGRES_PASSWORD`
- `DATABASE_URL`

Можно дополнительно настроить:

- `GOOGLE_API_KEY`
- `MINIMAX_API_KEY`
- `WATCHLIST`
- `ALERT_SPIKE_THRESHOLD`
- `WEBUI_PORT`
- `HTX_REST_URL`
- `WEBUI_AUTH_ENABLED`
- `WEBUI_ADMIN_USERNAME`
- `WEBUI_ADMIN_PASSWORD`
- `WEBUI_SESSION_SECRET`

## Шаг 2: Поднять стек

```powershell
Set-Location D:\WORED
docker-compose up --build -d
```

Ожидаемый результат:

- создаются или переиспользуются пять контейнеров,
- `postgres` переходит в `healthy`,
- `collector`, `chatbot` и `webui` остаются в `Up`, а не уходят в циклический restart.

## Шаг 3: Проверить состояние контейнеров

```powershell
docker-compose ps
```

Ожидаемый результат:

- `htx_trading_bot_postgres` имеет статус `healthy`,
- `htx_trading_bot_redis` имеет статус `Up`,
- `htx_trading_bot_collector` имеет статус `Up`,
- `htx_trading_bot_chatbot` имеет статус `Up`,
- `htx_trading_bot_webui` имеет статус `healthy` или `Up (healthy)`.

## Шаг 4: Проверить collector

```powershell
docker-compose logs --tail 80 collector
```

Признаки здоровой работы:

- `Connected to HTX WebSocket`
- `Job "check_alerts" ... executed successfully`
- `AI journal entry written`
- `Recorded 15m AI journal entry`

Предупреждение, которое может самовосстанавливаться:

- `WebSocket disconnected ... Reconnecting in 5s`

## Шаг 5: Проверить chatbot

```powershell
docker-compose logs --tail 80 chatbot
```

Признаки здоровой работы:

- `Chatbot started polling`
- `AI 'analyst' responded in ...`
- временные reconnect к Telegram автоматически восстанавливаются

Известные runtime-проблемы:

- `MiniMax` reviewer может деградировать во fallback, если NVIDIA NIM отвечает слишком медленно
  - теперь этот path подтверждает callback сразу и быстро переключается на другие tiers вместо долгого зависания

## Шаг 6: Проверить webui

```powershell
Invoke-WebRequest http://localhost:8080/api/health
```

Ожидаемый результат:

- HTTP `200`,
- JSON содержит поля `redis`, `postgres`, `collector_feed`, `last_journal_at`.

После этого открой в браузере:

```text
http://localhost:8080
```

Признаки здоровой работы:

- открывается экран `WORED Local Control Room`,
- в правой колонке видны статусы Redis/Postgres/Collector,
- на основном графике отображаются свечи по `BTCUSDT`,
- секции `Spike Alerts` и `AI Journal` заполняются без ошибок фронтенда.

Если включён `WEBUI_AUTH_ENABLED=true`, сначала откроется `/login`, а после успешного входа будут доступны:

- `/`
- `/alerts`
- `/predictions`
- `/journal`

## Шаг 7: Functional smoke test

В Telegram:

1. Открой бота.
2. Отправь `/start`.
3. Нажми `📊 Рынок`.
4. Нажми `📈 Аналитика`.
5. Задай свободный текстовый вопрос по рынку.

Ожидаемый результат:

- `/start` показывает главную клавиатуру,
- `📊 Рынок` показывает цены из Redis,
- `📈 Аналитика` показывает кнопки по символам,
- свободный текст вызывает AI routing и возвращает ответ.

## Остановка и перезапуск

Остановить стек:

```powershell
docker-compose down
```

Перезапустить:

```powershell
docker-compose up -d
```

Пересобрать после изменения зависимостей или Dockerfile:

```powershell
docker-compose up --build -d
```

## Частые сбои

### Docker не запущен

Симптом:

- `error during connect`

Что делать:

- запустить Docker Desktop,
- повторить `docker-compose up --build -d`.

### Бот не отвечает в Telegram

Симптом:

- контейнер `chatbot` поднят, но ответы не приходят

Что делать:

- проверить `TELEGRAM_TOKEN`,
- открыть `docker-compose logs --tail 80 chatbot`,
- убедиться, что в `.env` не остался placeholder-токен.

### Нет рыночных данных

Симптом:

- `📊 Рынок` показывает `данные не загружены`

Что делать:

- посмотреть логи `collector`,
- проверить доступность HTX,
- проверить Redis командами из `docs/05-operations.md`.

### Web UI не открывается

Симптом:

- `http://localhost:8080` не отвечает или страница пустая

Что делать:

- проверить `docker-compose logs --tail 80 webui`,
- проверить `Invoke-WebRequest http://localhost:8080/api/health`,
- убедиться, что `WEBUI_PORT` не занят другим локальным сервисом,
- если `/api/candles` отвечает `502`, проверить доступность `HTX_REST_URL`.

### Prediction Lab quick check

После старта открой:

- `http://localhost:8080/predictions`

Ожидаемое поведение:

- страница открывается без frontend error;
- видны model cards со статусами `ready` или `offline`;
- форма даёт только горизонты `1, 2, 3, 4, 8, 16, 24`.

### В логах Postgres всё ещё есть ошибки подключения

Симптом:

- в Postgres регулярно идут FATAL-строки

Что изменилось:

- root healthcheck исправлен и теперь использует `POSTGRES_DB`

Если новые FATAL-строки всё же появляются:

- проверь реальные клиентские подключения, а не healthcheck,
- смотри `docker-compose logs --tail 40 postgres`.

# Архитектура

## Топология сервисов

```text
HTX WebSocket ----\
                   \
                    -> collector -> Redis -> chatbot -> Telegram Bot API
HTX REST ---------/               \-> Postgres -/
                                     ^
                                     |
                        webui --------/

webui -> HTX REST
webui -> Redis
webui -> Postgres
webui -> AI providers for Prediction Lab

chatbot -> AI providers
  - worker: Qwen and flash-worker auto-switch chain (qwen3.6-flash -> qwen3.5-flash -> qwen-flash -> glm-4-flash -> gemini-3-flash-preview)
  - analyst: Qwen reasoning chain (qwen3.6-35b-a3b -> qwen3.6-27b -> glm-5.1)
  - premium fallback: Qwen reasoning chain (qwen3.6-27b -> qwen3.6-35b-a3b -> glm-5.1)
  - reviewer fallback: MiniMax через NVIDIA-совместимый endpoint
```

## Ответственность сервисов

| Сервис | Путь | Роль |
| --- | --- | --- |
| `postgres` | корневой compose-service | персистентное хранение истории алертов и снимков AI-журнала |
| `redis` | корневой compose-service | горячий кэш тикеров и pub/sub-транспорт для админских алертов |
| `collector` | `D:\WORED\collector` | ingestion HTX, расчёт индикаторов, scheduler, генерация алертов |
| `chatbot` | `D:\WORED\chatbot` | Telegram UX, рыночный вывод, AI-классификация, routing и delivery |
| `webui` | `D:\WORED\webui` | браузерная панель, REST API для графиков, чтение live snapshot и исторических событий |

## Внутреннее устройство collector

### Точка входа

- Файл: `D:\WORED\collector\main.py`

### Что делает collector

- Инициализирует клиенты Postgres и Redis.
- Запускает фоновый HTX WebSocket listener.
- Регистрирует плановые задачи:
  - `record_ai_journal` каждые 15 минут
  - `check_alerts` каждые 5 минут
  - `cleanup_old_tickers` каждые 6 часов

### Что collector пишет наружу

- Redis keys: `ticker:{symbol}`
- Redis channel: `market_alerts`
- Postgres rows:
  - `alerts`
  - `ai_journal`

### Важное ограничение

Таблица `market_tickers` есть в схеме, и `save_tickers()` есть в коде, но активный runtime path сейчас её не вызывает. Поэтому историческое хранение по рынку фактически завязано на `ai_journal`, а не на потоковую запись всех тикеров.

## Внутреннее устройство chatbot

### Точка входа

- Файл: `D:\WORED\chatbot\main.py`

### Подключённые роутеры

- `handlers.start`
- `handlers.menu`
- `handlers.callbacks`
- `handlers.market`
- `handlers.alerts`
- `handlers.analytics`
- `handlers.portfolio`
- `handlers.settings`
- `handlers.chat`

### Путь AI-запроса

1. В `chatbot` приходит Telegram-сообщение.
2. Намерение определяется через `ai.dispatcher.classify()`.
3. Контекст собирается через `ai.context_builder` и `ai.knowledge_base`.
4. `ai.router.route_request()` выбирает предпочтительный tier.
5. `ai.resilience` применяет timeout, circuit breaker и retry.
6. При падении провайдера включается fallback.
7. Финальный ответ отправляется обратно в Telegram.

## Внутреннее устройство webui

### Точка входа

- Файл: `D:\WORED\webui\app.py`

### Что делает webui

- Поднимает FastAPI-приложение и шаблонный frontend.
- Читает `WATCHLIST`, `REDIS_URL`, `DATABASE_URL` и `HTX_REST_URL`.
- Отдаёт обзорный API:
  - `/api/health`
  - `/api/overview`
  - `/api/alerts`
  - `/api/journal`
  - `/api/candles`
- Строит индикаторные payload внутри backend:
  - `SMA20`
  - `SMA50`
  - `RSI14`
  - `MACD`

### Зачем webui вынесен в отдельный сервис

- не перегружает `chatbot` браузерной разметкой и HTTP-маршрутами,
- не требует отдельного Node build chain,
- может развиваться как операционный dashboard независимо от Telegram UX.

## Маршрутизация моделей

### Активная карта моделей

| Tier | Источник | Модель по умолчанию | Назначение |
| --- | --- | --- | --- |
| `worker` | `chatbot/ai/models.py` | `qwen3.6-flash` primary with fallback to `qwen3.5-flash`, `qwen-flash`, `glm-4-flash` | классификация и быстрые ответы |
| `analyst` | `chatbot/ai/models.py` | `qwen3.6-35b-a3b` primary with fallback to `qwen3.6-27b`, `glm-5.1` | основной рыночный анализ |
| `premium` | `chatbot/ai/models.py` | `qwen3.6-27b` primary with fallback to `qwen3.6-35b-a3b`, `glm-5.1` | тяжёлый fallback для глубокого анализа |
| `minimax` | `chatbot/ai/models.py` | `minimaxai/minimax-m2.7` | второе мнение |

### Как routing работает сейчас

- Ценовые запросы идут в Redis и обходят AI.
- Обычный чат и простые криптовопросы стартуют с `worker`.
- Стандартный анализ стартует с `analyst`.
- Глубокий анализ стартует с `premium`.
- Второе мнение стартует с `minimax`.
- Fail-fast fallback не держит callback path слишком долго и быстро переключает tiers.

## Схема хранения

### Таблицы из `db/init.sql`

| Таблица | Назначение | Текущее использование |
| --- | --- | --- |
| `trades` | будущая торговая история | не используется корневым runtime |
| `alerts` | история spike-алертов | используется |
| `market_tickers` | история тикеров | схема есть, запись не подключена |
| `ai_usage_log` | токен-аккаунтинг | схема есть, не используется |
| `ai_journal` | периодические снимки рынка и индикаторов | используется |

## Реальные runtime sequence

### Снимок рынка и AI-журнал

```text
collector получает тикер по websocket
-> парсит HTX payload
-> сохраняет ticker:{symbol} в Redis с TTL 5 минут
-> плановая задача забирает историю свечей через HTX REST
-> рассчитываются индикаторы
-> в Postgres пишется ai_journal row
```

### Telegram AI-анализ

```text
пользователь задаёт вопрос
-> chatbot классифицирует intent
-> chatbot читает Redis и свежие записи ai_journal
-> chatbot собирает системный контекст
-> chatbot вызывает предпочтительный AI-tier
-> resilience-слой обрабатывает timeout/retry/circuit breaker
-> при ошибке используется fallback tier
-> ответ возвращается в Telegram
```

### Browser control room

```text
оператор открывает webui
-> FastAPI отдаёт HTML/CSS/JS shell
-> frontend вызывает /api/overview
-> backend читает Redis и Postgres
-> frontend вызывает /api/candles?symbol=...&period=...
-> backend тянет HTX REST /market/history/kline
-> backend рассчитывает SMA/RSI/MACD
-> frontend рисует графики через Lightweight Charts
```

### Prediction Lab

```text
оператор открывает /predictions
-> webui читает Redis spot snapshot, HTX hourly candles и recent ai_journal
-> webui вызывает все доступные AI models напрямую
-> Postgres получает forecast_requests, forecast_model_runs, forecast_points
-> collector каждые 5 минут оценивает due points по фактической цене
-> webui показывает per-model scorecard и fact-vs-forecast drill-down
```

## Что не считать production-архитектурой

Следующие файлы не являются надёжным описанием живого runtime:

- `D:\WORED\chatbot\loader.py`
- `D:\WORED\chatbot\context\builder.py`
- `D:\WORED\chatbot\ui\formatter.py`
- `D:\WORED\chatbot\ui\keyboards.py`
- `D:\WORED\chatbot\ui\onboarding.py`
- `D:\WORED\collector\alerts\detector.py`
- `D:\WORED\collector\scheduler\briefing.py`

## Вывод по архитектуре

У корневого проекта уже есть полноценный рабочий вертикальный срез:

- live market ingestion,
- hot cache,
- durable alert/journal storage,
- Telegram UI,
- browser dashboard,
- contextual AI routing.

Главный архитектурный долг сейчас не в отсутствии системы, а в расхождении между:

- активным runtime-кодом,
- legacy и placeholder-модулями,
- схемой БД, которая частично не подключена,
- и переменными окружения, которые существуют за пределами реально используемого path.

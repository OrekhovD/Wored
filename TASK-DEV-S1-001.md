# TASK-DEV-S1-001 — HTX Trading Bot: Полная реализация рабочего кода
[STATUS: READY_FOR_IMPLEMENTATION]
Агент-исполнитель: QwenCode (qwen2.5-coder-32b-instruct / qwen3-max)
Оркестратор: Qwen3.6-Plus (ты читаешь это ТЗ и делегируешь QwenCode)
Дата: 18.04.2026
Приоритет: КРИТИЧЕСКИЙ — 0 из 40+ файлов содержат рабочий код

---

## 0. ПРАВИЛА РАБОТЫ ДЛЯ АГЕНТА

```
ОБЯЗАТЕЛЬНО:
1. Пиши ВЕСЬ код файла целиком — никаких "# остальное без изменений"
2. Файлы создавай через инструмент write_file или bash в терминале VS Code
3. После каждого файла — проверяй синтаксис: python -m py_compile <file>
4. Двигайся строго по порядку блоков: Блок A → Блок B → ... → Блок F
5. После каждого блока — запускай проверку из секции "Validation" этого блока
6. НИКОГДА не создавай файл с pass, TODO, placeholder, "# реализация здесь"

ЗАПРЕЩЕНО:
- Синхронный код (requests, psycopg2, redis.Redis, time.sleep)
- aiogram 2.x (executor, Dispatcher(bot=...), contrib)
- Именовать файлы init.py вместо __init__.py
- Оставлять пустые requirements.txt
```

---

## 1. Context

**Project:** HTX Trading Bot — Telegram бот для трейдинга на бирже HTX
**Stack:** Python 3.12, aiogram 3.14, asyncpg, redis.asyncio, aiohttp, Docker Compose
**Проблема:** Предыдущий агент создал структуру папок с файлами-заглушками.
Все ключевые файлы содержат только `pass`, `print()` или вообще пустые.
Система не запускается. Нужно написать полный рабочий код.

**Корневые документы:**
- `TZ.md` — полная архитектура проекта (ГЛАВНЫЙ ДОКУМЕНТ, читать первым)
- `MODEL_REGISTRY.md` — актуальные модели и ключи
- `chatbot/ai/router.py` — уже исправленный роутер (НЕ ТРОГАТЬ)

---

## 2. Goal & Success Criteria

**Goal:** После выполнения ТЗ команда `make build && make up` запускает
все 4 контейнера без ошибок, бот отвечает в Telegram на /start.

**Критерии успеха:**
- `docker compose ps` показывает все 4 сервиса Up
- `make logs-collector` показывает "HTX WebSocket connected"
- `make logs-chatbot` показывает "Bot @username started"
- `/start` в Telegram возвращает главное меню с кнопками
- `/price btcusdt` возвращает карточку с ценой из Redis

---

## 3. Структура проекта (целевое состояние)

```
htx-trading-bot/
├── .env                          ← создать из .env.example (заполнит пользователь)
├── .env.example                  ← уже есть ✅
├── .gitignore                    ← создать
├── docker-compose.yml            ← переписать полностью
├── Makefile                      ← переписать полностью
├── validate_models.py            ← уже есть ✅
├── MODEL_REGISTRY.md             ← уже есть ✅
│
├── db/
│   └── init.sql                  ← переписать (все 9 таблиц)
│
├── collector/
│   ├── Dockerfile                ← создать
│   ├── requirements.txt          ← создать с зависимостями
│   ├── __init__.py               ← пустой файл (именно __init__.py!)
│   ├── main.py                   ← переписать полностью
│   ├── htx/
│   │   ├── __init__.py
│   │   ├── websocket.py          ← переписать полностью
│   │   └── rest.py               ← переписать полностью
│   ├── indicators/
│   │   ├── __init__.py
│   │   └── calculator.py         ← переписать полностью
│   ├── alerts/
│   │   ├── __init__.py
│   │   └── detector.py           ← переписать полностью
│   ├── journal/
│   │   ├── __init__.py
│   │   └── writer.py             ← переписать полностью
│   ├── scheduler/
│   │   ├── __init__.py
│   │   ├── alert_checker.py      ← переписать полностью
│   │   └── briefing.py           ← переписать полностью
│   └── storage/
│       ├── __init__.py
│       ├── redis_client.py       ← переписать (asyncio)
│       └── postgres_client.py    ← переписать (asyncpg)
│
└── chatbot/
    ├── Dockerfile                ← создать
    ├── requirements.txt          ← создать с зависимостями
    ├── __init__.py
    ├── main.py                   ← переписать полностью (aiogram 3.14!)
    ├── handlers/
    │   ├── __init__.py
    │   ├── start.py              ← переписать
    │   ├── market.py             ← переписать
    │   ├── chat.py               ← переписать
    │   ├── alerts.py             ← переписать
    │   ├── portfolio.py          ← переписать
    │   ├── analytics.py          ← переписать
    │   └── settings.py           ← переписать
    ├── ai/
    │   ├── __init__.py
    │   ├── router.py             ← уже исправлен ✅ НЕ ТРОГАТЬ
    │   ├── prompts.py            ← переписать полностью
    │   └── knowledge_base.py     ← переписать полностью
    ├── context/
    │   ├── __init__.py
    │   └── builder.py            ← переписать
    ├── ui/
    │   ├── __init__.py
    │   ├── formatter.py          ← переписать
    │   └── keyboards.py          ← переписать
    └── storage/
        ├── __init__.py
        ├── redis_client.py       ← переписать (asyncio)
        └── postgres_client.py    ← переписать (asyncpg)
```

---

## 4. БЛОК A — Инфраструктура (начинать здесь)

### A1. `.gitignore`

```
.env
__pycache__/
*.pyc
*.pyo
.DS_Store
logs/
*.log
backups/
.pytest_cache/
```

### A2. `docker-compose.yml` — ПОЛНАЯ ВЕРСИЯ

Требования:
- 4 сервиса: postgres, redis, collector, chatbot
- `healthcheck` для postgres и redis
- `depends_on` с `condition: service_healthy`
- `env_file: .env` для collector и chatbot
- volumes: pgdata, redisdata, collector_logs, chatbot_logs
- network: trading_net (bridge) — все сервисы в одной сети
- restart: unless-stopped для всех сервисов
- logging driver: json-file, max-size 50m, max-file 3

**Правильные healthcheck'и:**

```yaml
# postgres:
test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-bot} -d ${POSTGRES_DB:-trading}"]
interval: 10s
timeout: 5s
retries: 5

# redis:
test: ["CMD", "redis-cli", "ping"]
interval: 10s
timeout: 3s
retries: 5
```

### A3. `Makefile` — ПОЛНАЯ ВЕРСИЯ

Требования к синтаксису:
- КАЖДАЯ цель `.PHONY` на отдельной строке
- Команды с TAB отступом (не пробелами!)
- Переменные через `$$(...)` для shell подстановки внутри рецептов

Обязательные цели:
```
build, up, down, restart, ps, logs, logs-collector, logs-chatbot,
logs-db, db-shell, redis-cli, collector-shell, chatbot-shell,
db-stats, db-candles, db-alerts, redis-tickers, redis-journal,
backup, restore, clean, clean-logs, update, lint
```

Критическая ошибка для исправления:
```makefile
# НЕПРАВИЛЬНО (было):
up: down:

# ПРАВИЛЬНО:
.PHONY: up
up:
	docker compose up -d

.PHONY: down
down:
	docker compose down
```

### A4. `db/init.sql` — ВСЕ 9 ТАБЛИЦ

Создать таблицы (если не существуют):

1. **candles** — OHLCV свечи
   - id BIGSERIAL PK
   - symbol VARCHAR(20) NOT NULL
   - interval VARCHAR(10) NOT NULL (1min, 5min, 60min, 4hour, 1day)
   - open_time TIMESTAMPTZ NOT NULL
   - open/high/low/close NUMERIC(20,8) NOT NULL
   - volume NUMERIC(30,8) NOT NULL
   - UNIQUE(symbol, interval, open_time)
   - INDEX на (symbol, interval, open_time DESC)

2. **indicators** — технические индикаторы
   - id BIGSERIAL PK
   - symbol VARCHAR(20) NOT NULL
   - timestamp TIMESTAMPTZ DEFAULT NOW()
   - rsi_14, macd, macd_signal, macd_hist — NUMERIC(20,8)
   - ema_20, ema_50 — NUMERIC(20,8)
   - bb_upper, bb_middle, bb_lower — NUMERIC(20,8)
   - INDEX на (symbol, timestamp DESC)

3. **alerts** — системные алерты от детектора
   - id BIGSERIAL PK
   - symbol VARCHAR(20) NOT NULL
   - alert_type VARCHAR(50) NOT NULL
   - severity VARCHAR(20) NOT NULL (LOW/MEDIUM/HIGH/CRITICAL)
   - price NUMERIC(20,8), change_pct NUMERIC(10,4)
   - detail TEXT
   - sent BOOLEAN DEFAULT FALSE
   - created_at TIMESTAMPTZ DEFAULT NOW()
   - INDEX на (created_at DESC)

4. **ai_journal** — почасовые рыночные снапшоты для AI
   - id BIGSERIAL PK
   - snapshot JSONB NOT NULL
   - journal_type VARCHAR(20) DEFAULT 'hourly'
   - created_at TIMESTAMPTZ DEFAULT NOW()
   - INDEX на (journal_type, created_at DESC)

5. **chat_history** — история чата пользователей с AI
   - id BIGSERIAL PK
   - user_id BIGINT NOT NULL
   - role VARCHAR(20) NOT NULL (user/assistant)
   - content TEXT NOT NULL
   - model_used VARCHAR(50)
   - created_at TIMESTAMPTZ DEFAULT NOW()
   - INDEX на (user_id, created_at DESC)

6. **user_alerts** — пользовательские ценовые алерты
   - id BIGSERIAL PK
   - user_id BIGINT NOT NULL
   - symbol VARCHAR(20) NOT NULL
   - price NUMERIC(20,8) NOT NULL
   - direction VARCHAR(10) NOT NULL (above/below)
   - active BOOLEAN DEFAULT TRUE
   - triggered_at TIMESTAMPTZ
   - created_at TIMESTAMPTZ DEFAULT NOW()
   - UNIQUE(user_id, symbol, price, direction)
   - INDEX на (active, symbol)

7. **positions** — торговые позиции (портфель)
   - id BIGSERIAL PK
   - user_id BIGINT NOT NULL
   - symbol VARCHAR(20) NOT NULL
   - direction VARCHAR(10) NOT NULL (long/short)
   - entry_price NUMERIC(20,8) NOT NULL
   - amount_usdt NUMERIC(20,4) NOT NULL
   - stop_loss NUMERIC(20,8), take_profit NUMERIC(20,8)
   - status VARCHAR(10) DEFAULT 'open' (open/closed)
   - close_price NUMERIC(20,8)
   - realized_pnl NUMERIC(20,4)
   - opened_at TIMESTAMPTZ DEFAULT NOW()
   - closed_at TIMESTAMPTZ
   - INDEX на (user_id, status)

8. **user_settings** — настройки пользователей
   - id BIGSERIAL PK
   - user_id BIGINT NOT NULL
   - key VARCHAR(50) NOT NULL
   - value TEXT NOT NULL
   - updated_at TIMESTAMPTZ DEFAULT NOW()
   - UNIQUE(user_id, key)

9. **users** — реестр пользователей
   - id BIGINT PK
   - username VARCHAR(100)
   - first_seen TIMESTAMPTZ DEFAULT NOW()
   - last_seen TIMESTAMPTZ DEFAULT NOW()

### Validation Блока A:
```bash
# После создания файлов:
docker compose config --quiet && echo "✅ compose valid"
make db-shell
# В psql:
\dt   # должно показать 9 таблиц
\q
```

---

## 5. БЛОК B — Collector: Storage Layer

### B1. `collector/requirements.txt`

```
websockets==13.1
aiohttp==3.10.11
asyncpg==0.30.0
redis[hiredis]==5.2.1
pandas==2.2.3
pandas-ta==0.3.14b
numpy==1.26.4
python-dotenv==1.0.1
loguru==0.7.2
```

### B2. `collector/Dockerfile`

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y gcc libpq-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "-u", "main.py"]
```

### B3. `collector/storage/redis_client.py`

**КРИТИЧЕСКОЕ ТРЕБОВАНИЕ:** Только `redis.asyncio` — НЕ `redis.Redis`!

Методы (все async):
- `connect()` / `close()`
- `set_ticker(symbol, data, ttl=60)` → setex с JSON
- `get_ticker(symbol)` → dict | None
- `push_candle(symbol, interval, candle, max_length=500)` → rpush + ltrim
- `get_candles(symbol, interval, limit=200)` → list[dict]
- `set_indicators(symbol, data, interval="1min", ttl=120)`
- `get_indicators(symbol, interval="1min")` → dict | None
- `set_depth(symbol, data, ttl=30)`
- `get_depth(symbol)` → dict | None
- `store_price_history(symbol, price)` → rpush + ltrim(200) + expire(3600)
- `get_price_15min_ago(symbol)` → float | None (ищем запись ±120с от now-900)
- `save_alert(symbol, type, severity, price, change_pct, detail)`
- `get_recent_alerts(limit=10)` → list[dict]
- `set_json(key, data, ttl=300)`
- `get_json(key)` → dict | None

Подключение:
```python
self._redis = aioredis.from_url(self._url, decode_responses=True)
```

### B4. `collector/storage/postgres_client.py`

**КРИТИЧЕСКОЕ ТРЕБОВАНИЕ:** Только `asyncpg.create_pool` — НЕ `psycopg2`!

```python
self._pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=2, max_size=10)
```

Методы (все async):
- `connect()` / `close()`
- `save_candles(symbol, interval, candles: list[dict])` → executemany с ON CONFLICT DO NOTHING
- `upsert_candle(symbol, interval, candle)` → ON CONFLICT DO UPDATE (GREATEST/LEAST для high/low)
- `get_candles(symbol, interval, limit=500)` → list[dict], order ASC
- `get_candles_range(symbol, interval, start_ts, end_ts)` → list[dict]
- `upsert_indicators(symbol, indicators)` → INSERT с ON CONFLICT DO NOTHING
- `save_alert(symbol, alert_type, severity, price, change_pct, detail)`
- `get_recent_alerts(limit=50, symbol=None)` → list[dict]
- `save_journal_snapshot(snapshot: dict, journal_type="hourly")`
- `get_journal_snapshots(limit=24, journal_type="hourly")` → list[dict]
- `get_active_users(since_hours=24)` → list[int] (из chat_history)
- `get_all_active_alerts()` → list[dict] (из user_alerts WHERE active=TRUE)
- `trigger_alert(alert_id)` → UPDATE active=FALSE, triggered_at=NOW()

**Важно для upsert_candle:**
```sql
ON CONFLICT (symbol, interval, open_time)
DO UPDATE SET
    high   = GREATEST(candles.high, EXCLUDED.high),
    low    = LEAST(candles.low, EXCLUDED.low),
    close  = EXCLUDED.close,
    volume = EXCLUDED.volume
```

### Validation Блока B:
```bash
cd collector
python -m py_compile storage/redis_client.py && echo "✅ redis"
python -m py_compile storage/postgres_client.py && echo "✅ postgres"
```

---

## 6. БЛОК C — Collector: Core Logic

### C1. `collector/htx/rest.py`

**КРИТИЧЕСКОЕ ТРЕБОВАНИЕ:** Только `aiohttp` — НЕ `requests`!

```python
BASE_URL = "https://api.huobi.pro"
```

Методы (все async):
- `get_klines(symbol, interval, size=300)` → list[dict] с полями {open_time, open, high, low, close, volume}
  - GET `/market/history/kline?symbol=...&period=...&size=...`
  - ⚠️ HTX возвращает newest first — делать `reversed(data["data"])`
  - Проверять `data["status"] == "ok"`, иначе raise ValueError
- `get_ticker(symbol)` → dict {price, bid, ask, high_24h, low_24h, volume_24h}
  - GET `/market/detail/merged`
- `get_all_tickers()` → list[dict]
  - GET `/market/tickers`
- `get_symbols()` → list[str] (только `state == "online"`)
  - GET `/v1/common/symbols`
- `close()` — закрыть aiohttp.ClientSession

Interval mapping (WS period → REST period):
```python
INTERVAL_MAP = {
    "1min": "1min", "5min": "5min", "60min": "60min",
    "4hour": "4hour", "1day": "1day"
}
```

### C2. `collector/htx/websocket.py`

⚠️ **КРИТИЧЕСКИЕ ОСОБЕННОСТИ HTX WebSocket:**
1. URL: `wss://api.huobi.pro/ws`
2. Все данные сжаты **GZIP** → `gzip.decompress(raw_msg).decode("utf-8")`
3. HTX шлёт `{"ping": timestamp}` → обязательно отвечать `{"pong": timestamp}`
4. Без ответа на ping — соединение закрывается через ~10 сек

Подписки для каждого символа:
```python
{"sub": f"market.{symbol}.detail", "id": f"detail_{symbol}"}     # тикер
{"sub": f"market.{symbol}.kline.1min", "id": f"kline_1min_{symbol}"}
{"sub": f"market.{symbol}.kline.5min", "id": f"kline_5min_{symbol}"}
{"sub": f"market.{symbol}.depth.step0", "id": f"depth_{symbol}"}  # стакан
```

Структура класса `HTXWebSocketClient`:
```python
class HTXWebSocketClient:
    def __init__(self, symbols, redis, pg, indicator_calc, alert_detector, journal)
    async def run(self)                    # основной цикл с reconnect
    async def _connect_and_subscribe(self) # подключение + подписка
    async def _subscribe_all(self)         # отправка подписок
    async def _listen(self, ws)            # цикл приёма сообщений
    async def _handle_message(self, msg)   # роутинг по типу
    async def _handle_ticker(self, channel, tick)
    async def _handle_kline(self, channel, tick)
    async def _handle_depth(self, channel, tick)
```

Reconnect delays: `[1, 2, 4, 8, 16, 30]` секунд (exponential backoff).

websockets.connect параметры:
```python
async with websockets.connect(
    HTX_WS_URL,
    ping_interval=None,        # HTX использует свой ping
    max_size=10 * 1024 * 1024, # 10MB буфер
    compression=None           # GZIP делаем сами
) as ws:
```

### C3. `collector/indicators/calculator.py`

Библиотека: `pandas_ta`

```python
class IndicatorCalculator:
    def calculate(self, candles: list[dict]) -> dict:
        # Возвращает dict с ключами:
        # rsi, macd, macd_signal, macd_hist, macd_signal_type
        # ema20, ema50, ema_trend (bullish/bearish)
        # bb_upper, bb_middle, bb_lower, bb_position (0-100%)
        # atr14, atr_pct
```

Расчёты:
- RSI(14): `pandas_ta.rsi(df["close"], length=14)`
- MACD(12,26,9): `pandas_ta.macd(df["close"], fast=12, slow=26, signal=9)`
  - macd_signal_type: если MACDh сменил знак с - на + → "bullish_cross", иначе "bearish_cross" или "neutral"
- BB(20,2): `pandas_ta.bbands(df["close"], length=20, std=2)`
  - bb_position = (close - bb_lower) / (bb_upper - bb_lower) * 100
- EMA(20), EMA(50): `pandas_ta.ema(...)`
  - ema_trend = "bullish" if ema20 > ema50 else "bearish"
- ATR(14): `pandas_ta.atr(high, low, close, length=14)`

Если candles < 50 → вернуть {} с warning логом.

```python
    async def calculate_and_store(self, symbol, interval, candles):
        indicators = self.calculate(candles)
        if indicators:
            await self.redis.set_indicators(symbol, indicators, interval)
            await self.pg.upsert_indicators(symbol, indicators)
```

### C4. `collector/alerts/detector.py`

```python
class AlertDetector:
    ALERT_COOLDOWN = {
        "SPIKE": 300, "RSI_EXTREME": 600,
        "MACD_CROSS": 300, "BB_BREAK": 180
    }

    async def check_price_spike(self, symbol, current_price, ticker_data)
    # threshold из env ALERT_SPIKE_THRESHOLD (default 3.0%)
    # сравниваем с ценой 15 минут назад из Redis

    async def check_indicator_alerts(self, symbol, indicators)
    # RSI >= 75 → RSI_OB; RSI <= 25 → RSI_OS
    # MACD bullish_cross / bearish_cross
    # BB position >= 95 → BB_UPPER; <= 5 → BB_LOWER

    async def _send_alert(self, text)
    # POST https://api.telegram.org/bot{TOKEN}/sendMessage
    # parse_mode HTML, к TELEGRAM_ADMIN_ID из env
```

Cooldown через `self._alert_cache: dict[str, float]` (key → last_sent_timestamp).

### C5. `collector/journal/writer.py`

```python
class JournalWriter:
    async def build_snapshot(self) -> dict:
        # Собирает из Redis: ticker, indicators для каждого символа
        # Последние 10 алертов
        # market_summary (текстовая строка)
        # Возвращает {"timestamp": iso, "snapshot": {...}, "indicators": {...}, "alerts": [...], "market_summary": "..."}

    async def run_realtime_snapshot(self)
    # asyncio.sleep(30), записывает в Redis: ai:journal:latest, ttl=120

    async def run_hourly_snapshot(self)
    # asyncio.sleep(3600), записывает в PostgreSQL ai_journal
```

### C6. `collector/scheduler/alert_checker.py`

```python
class AlertChecker:
    async def run(self)        # asyncio.sleep(30) цикл
    async def _check_all(self) # читает get_all_active_alerts() из pg
    async def _fire_alert(self, alert, current_price) # Telegram API
```

Логика срабатывания:
```python
if alert["direction"] == "above" and current_price >= float(alert["price"]):
    triggered = True
elif alert["direction"] == "below" and current_price <= float(alert["price"]):
    triggered = True
```

После срабатывания: `pg.trigger_alert(alert_id)` + отправить сообщение в `alert["user_id"]`.

### C7. `collector/main.py` — ГЛАВНЫЙ ФАЙЛ COLLECTOR

```python
import asyncio, signal
from dotenv import load_dotenv
load_dotenv()

async def main():
    # 1. Инициализация Redis и PostgreSQL
    # 2. Загрузка исторических свечей через REST (300 свечей для каждой пары × 4 интервала)
    # 3. Создание asyncio tasks:
    tasks = [
        asyncio.create_task(ws_client.run(), name="ws_stream"),
        asyncio.create_task(periodic_rest_sync(...), name="rest_sync"),
        asyncio.create_task(journal.run_hourly_snapshot(), name="journal_hourly"),
        asyncio.create_task(journal.run_realtime_snapshot(), name="journal_realtime"),
        asyncio.create_task(alert_checker.run(), name="alert_checker"),
        asyncio.create_task(briefing_sched.run(), name="briefing_sched"),
    ]
    # 4. Graceful shutdown через signal handlers (SIGTERM, SIGINT)
    # 5. asyncio.gather(*tasks) с обработкой CancelledError
```

`periodic_rest_sync` — каждые 5 минут синхронизирует 60min и 1day свечи через REST.

### Validation Блока C:
```bash
cd collector
python -m py_compile main.py && echo "✅ main"
python -m py_compile htx/websocket.py && echo "✅ ws"
python -m py_compile htx/rest.py && echo "✅ rest"
python -m py_compile indicators/calculator.py && echo "✅ calc"
python -m py_compile alerts/detector.py && echo "✅ detector"
python -m py_compile journal/writer.py && echo "✅ journal"
python -m py_compile scheduler/alert_checker.py && echo "✅ checker"
python -m py_compile storage/redis_client.py && echo "✅ redis"
python -m py_compile storage/postgres_client.py && echo "✅ pg"
```

---

## 7. БЛОК D — Chatbot: Infrastructure

### D1. `chatbot/requirements.txt`

```
aiogram==3.14.0
asyncpg==0.30.0
redis[hiredis]==5.2.1
aiohttp==3.10.11
python-dotenv==1.0.1
loguru==0.7.2
```

### D2. `chatbot/Dockerfile`

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y gcc libpq-dev && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "-u", "main.py"]
```

### D3. `chatbot/storage/redis_client.py`

**ChatRedisClient** — читает данные написанные collector'ом.

```python
import redis.asyncio as aioredis   # ← НЕ import redis!

class ChatRedisClient:
    async def connect(self) / close(self)
    async def get_ticker(symbol) → dict | None
    async def get_indicators(symbol, interval="1min") → dict | None
    async def get_depth(symbol) → dict | None
    async def get_json(key) → dict | None
    async def get_recent_alerts(limit=10) → list
```

### D4. `chatbot/storage/postgres_client.py`

**ChatPostgresClient** — chatbot-специфичные операции.

В методе `connect()` — вызывать `await self._ensure_tables()` для создания
chat_history, user_alerts, positions, user_settings если не существуют.

Методы:
- `save_chat_message(user_id, role, content, model_used)`
- `get_chat_history(user_id, limit=20)` → `[{"role": ..., "content": ...}]` (для AI API)
- `get_chat_history_full(user_id, limit=50)` → с метаданными
- `clear_chat_history(user_id)`
- `get_active_users(since_hours=24)` → `list[int]`
- `save_user_alert(user_id, symbol, price, direction)`
- `get_user_alerts(user_id, active_only=True)` → `list[dict]`
- `get_all_active_alerts()` → `list[dict]`
- `trigger_alert(alert_id)`
- `clear_user_alerts(user_id)`
- `open_position(user_id, symbol, direction, entry_price, amount_usdt, stop_loss, take_profit)` → `int` (id)
- `get_open_positions(user_id)` → `list[dict]`
- `close_position(position_id, close_price, realized_pnl)`
- `get_closed_positions(user_id, limit=20)` → `list[dict]`
- `get_candles_for_analysis(symbol, interval="60min", limit=200)` → `list[dict]`
- `get_journal_for_ai(limit=24)` → `list[dict]`
- `get_alert_stats(symbol=None, days=7)` → `list[dict]`
- `get_user_settings(user_id)` → `dict[str, str]`
- `save_user_setting(user_id, key, value)` → upsert
- `is_new_user(user_id)` → `bool`
- `register_user(user_id, username=None)`
- `get_unread_alerts_count(user_id)` → `int`

---

## 8. БЛОК E — Chatbot: AI Layer

### E1. `chatbot/ai/prompts.py`

Функции:
- `get_current_datetime() → str` → UTC строка
- `get_perplexity_messages(user_query, market_snapshot) → list[dict]`
- `get_glm_messages(user_query, market_snapshot, chat_history, thinking=True) → dict` (полный payload)
- `get_minimax_messages(user_query, market_snapshot, chat_history, reasoning=False) → dict`
- `get_qwen_messages(user_query, market_snapshot, use_code_model=False) → dict`
- `get_intent_classifier_prompt(user_message) → str`
- `_format_snapshot_for_prompt(snapshot) → str` (приватная утилита)

Системные промты для каждой модели (взять из `TZ.md`, секция `chatbot/ai/prompts.py`).

**Ключевые детали промтов:**
- BASE_TRADING_CONTEXT включает: биржа HTX, не финансовый советник, стиль с эмодзи
- PERPLEXITY_SYSTEM: роль новостного аналитика, формат с нарративом и источниками
- GLM_SYSTEM: роль стратегического аналитика, RSI/MACD/BB методология, thinking mode
- MINIMAX_SYSTEM: быстрый ответчик + маркеры делегирования `[НУЖЕН_ГЛУБОКИЙ_АНАЛИЗ]` и `[НУЖНЫ_НОВОСТИ]`
- QWEN_SYSTEM: квантовый аналитик, генерация Python кода стратегий

Параметры моделей:
```python
# GLM payload:
{"model": "glm-4-plus", "temperature": 0.3, "max_tokens": 4096,
 "extra_body": {"thinking": {"type": "enabled"}}}  # только если thinking=True

# MiniMax payload:
{"model": "MiniMax-M2.7", "temperature": 0.5, "max_tokens": 2048}

# Qwen payload:
{"model": "qwen3.6-plus", "temperature": 0.1, "max_tokens": 8192,
 "extra_body": {"enable_thinking": True}}
```

### E2. `chatbot/ai/knowledge_base.py`

5 блоков знаний (взять из `TZ.md`):
- `INDICATORS_KNOWLEDGE` — RSI, MACD, BB, EMA, объём с интерпретацией
- `RISK_MANAGEMENT_KNOWLEDGE` — правила риска, формулы позиции, SL/TP
- `PATTERNS_KNOWLEDGE` — свечные паттерны, графические паттерны, уровни
- `HTX_KNOWLEDGE` — типы рынков, funding rate, OI, торговые сессии, комиссии
- `MARKET_CYCLES_KNOWLEDGE` — BTC доминирование, Fear&Greed, халвинг

```python
def get_knowledge_for_query(intent: str, query: str) -> str:
    # Возвращает релевантные блоки на основе интента + ключевых слов
    # Не грузить всё сразу — только нужное
```

### E3. `chatbot/context/builder.py`

```python
class ContextBuilder:
    async def build_for_ai(self, focus_symbol=None) -> dict:
        # Читает ai:journal:latest из Redis
        # Если focus_symbol — добавляет расширенные данные по паре
        # Возвращает {"timestamp", "snapshot", "indicators", "alerts", "market_summary"}
```

---

## 9. БЛОК F — Chatbot: Handlers + Main

### F1. `chatbot/main.py` — AIOGRAM 3.14 (КРИТИЧНО!)

```python
# ПРАВИЛЬНЫЙ импорт aiogram 3.x:
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

# НЕПРАВИЛЬНО (aiogram 2.x — НЕ ИСПОЛЬЗОВАТЬ!):
# from aiogram.utils import executor   ← ЗАПРЕЩЕНО
# from aiogram.contrib import ...      ← ЗАПРЕЩЕНО

async def main():
    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    storage = RedisStorage.from_url(REDIS_URL)
    dp = Dispatcher(storage=storage)

    # Внедрение зависимостей:
    dp["redis"] = redis_client
    dp["pg"] = pg_client
    dp["context_builder"] = context_builder

    # Подключение роутеров (порядок важен!):
    dp.include_router(start_router)
    dp.include_router(market_router)
    dp.include_router(alerts_router)
    dp.include_router(portfolio_router)
    dp.include_router(analytics_router)
    dp.include_router(settings_router)
    dp.include_router(chat_router)   # ПОСЛЕДНИМ — ловит всё остальное

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
```

### F2. `chatbot/handlers/start.py`

- `cmd_start(message, pg, state)` — проверить `is_new_user`, запустить онбординг или показать главное меню
- `cmd_help(message)` — список команд
- `cmd_menu(message)` — главное меню
- `cb_main_menu(callback)` — edit_text с меню

Главное меню — 8 кнопок, 2 в строке:
```
📊 Рынок | 💬 Спросить AI
🔍 Анализ | 📐 Расчёт
💼 Портфель | ⚠️ Алерты
📊 Аналитика | ⚙️ Настройки
```

### F3. `chatbot/handlers/market.py`

FSM States:
- `AnalysisState`: waiting_symbol, waiting_timeframe
- `PositionState`: waiting_symbol, waiting_deposit, waiting_risk, waiting_entry, waiting_stoploss, waiting_takeprofit

Handlers:
- `cmd_price(message)` — `/price [symbol]`
- `cb_quick_price(callback)` — quickprice:{symbol}
- `cmd_analysis(event)` — `/analysis` + callback `market:analyze`
- `cb_analysis_symbol(callback)` — выбор символа
- `cb_analysis_run(callback)` — запуск GLM анализа
- `cmd_indicators(event)` — сводка по всем парам
- `cmd_position_start(event)` — старт FSM расчёта позиции
- `cb_market_snapshot(callback)` — читает ai:journal:latest

### F4-F7: `handlers/chat.py`, `handlers/alerts.py`, `handlers/portfolio.py`, `handlers/analytics.py`, `handlers/settings.py`

Реализовать согласно `TZ.md` — все FSM-цепочки, inline keyboards, логика из секций handlers.

### F8. `chatbot/ui/formatter.py` и `chatbot/ui/keyboards.py`

Реализовать полностью по `TZ.md`, секция `chatbot/ui/`.

### Validation Блока F:
```bash
cd chatbot
python -m py_compile main.py && echo "✅ main"
python -m py_compile handlers/start.py && echo "✅ start"
python -m py_compile ai/prompts.py && echo "✅ prompts"
python -m py_compile ai/knowledge_base.py && echo "✅ kb"
python -m py_compile storage/redis_client.py && echo "✅ redis"
python -m py_compile storage/postgres_client.py && echo "✅ pg"
```

---

## 10. Inputs (файлы для чтения)

```
ОБЯЗАТЕЛЬНО прочитать перед началом:
- TZ.md (в корне проекта или /mnt/project/TZ.md) — ГЛАВНЫЙ ДОКУМЕНТ
- MODEL_REGISTRY.md — актуальные model strings
- chatbot/ai/router.py — уже исправлен, НЕ перезаписывать
- .env.example — структура переменных окружения
```

**Ключевые переменные окружения:**
```
TELEGRAM_TOKEN, TELEGRAM_ADMIN_ID
HTX_ACCESS_KEY, HTX_SECRET_KEY
DASHSCOPE_API_KEY (Qwen)
GLM_API_KEY (ZhipuAI)
MINIMAX_API_KEY (nvapi-... → NVIDIA NIM endpoint)
GOOGLE_API_KEY (Gemini)
POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
DATABASE_URL
REDIS_URL
WATCHLIST (default: btcusdt,ethusdt,solusdt,bnbusdt)
ALERT_SPIKE_THRESHOLD (default: 3.0)
LOG_LEVEL (default: INFO)
```

---

## 11. Acceptance Criteria

### Минимальные (MVP — перед деплоем):
- [ ] `python -m py_compile` проходит для всех 40+ .py файлов без ошибок
- [ ] `docker compose config --quiet` не выдаёт ошибок
- [ ] `make build` собирает оба образа без ошибок
- [ ] `make up` поднимает все 4 контейнера (Up статус)
- [ ] `make logs-collector` содержит "HTX WebSocket connected" через 30 сек
- [ ] `make logs-chatbot` содержит "Bot @username started"
- [ ] `/start` в Telegram возвращает главное меню с кнопками
- [ ] `/price btcusdt` возвращает карточку с ценой

### Полные (production-ready):
- [ ] `make db-candles` показывает накопленные свечи после 5 мин работы
- [ ] `make redis-journal` возвращает валидный JSON снапшот
- [ ] Нажатие "🔍 Анализ → BTC → 1ч" возвращает ответ от AI модели
- [ ] Настройка алерта через FSM работает без ошибок
- [ ] Открытие позиции через FSM работает без ошибок
- [ ] `validate_models.py` показывает ✅ для всех сконфигурированных ключей

---

## 12. Constraints

- **Python:** 3.12+
- **aiogram:** строго 3.14 — никакого 2.x кода
- **async:** весь I/O асинхронный (asyncio, aiohttp, asyncpg, redis.asyncio)
- **No sync calls:** requests, psycopg2, redis.Redis — запрещены
- **Docker:** все сервисы в одной bridge сети trading_net
- **Logging:** loguru для всех модулей, уровень из env LOG_LEVEL
- **Error handling:** try/except во всех методах работающих с сетью или БД

---

## 13. Порядок выполнения для QwenCode

```
Шаг 1: Прочитать TZ.md полностью
Шаг 2: Блок A (docker-compose, Makefile, db/init.sql, .gitignore)
Шаг 3: Validation Блока A
Шаг 4: Блок B (collector storage)
Шаг 5: Validation Блока B
Шаг 6: Блок C (collector core: htx, indicators, alerts, journal, scheduler, main)
Шаг 7: Validation Блока C
Шаг 8: Блок D (chatbot storage)
Шаг 9: Блок E (chatbot AI layer)
Шаг 10: Блок F (chatbot handlers + main)
Шаг 11: Validation Блока F
Шаг 12: make build && make up
Шаг 13: Финальная проверка по Acceptance Criteria
```

---

## 14. Follow-ups (после выполнения)

- TASK-DEV-S1-002: Интеграция Gemini как 5-й модели в router
- TASK-DEV-S1-003: Онбординг (chatbot/ui/onboarding.py)
- TASK-DEV-S1-004: Scheduler брифинга (briefing.py) — утренние рассылки
- TASK-DEV-S2-001: Бэктестинг через QwenCode (analytics.py полная версия)

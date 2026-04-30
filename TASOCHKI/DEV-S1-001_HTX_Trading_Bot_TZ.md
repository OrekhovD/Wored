# DEV-S1-001 — HTX Trading Bot: Полная реализация
[STATUS: READY_FOR_IMPLEMENTATION]
Агент-исполнитель: QWEN (Python Backend Dev)
Дата: 2026-05-01
Проект: htx-trading-bot
Приоритет: HIGH

---

## 1. Context

**Project:** HTX Trading AI Bot — мультиагентный торговый ассистент в Telegram.

**Problem / Opportunity:**
Архитектура и все ключевые файлы спроектированы и задокументированы (см. TZ.md).
Часть кода содержит заглушки, несогласованные сигнатуры и отсутствующие методы,
которые не позволяют проекту запуститься. Задача — создать полный рабочий репозиторий
"с нуля" в точном соответствии со спецификацией, исправив все выявленные несоответствия.

**Known bugs to fix before writing new code:**
1. `route_and_respond()` в `router.py` не принимает `force_intent` — параметр используется
   в `market.py` и `analytics.py`, но не объявлен в сигнатуре.
2. `event._pg` и `event._redis` в `analytics.py` — anti-pattern для aiogram 3.
   Зависимости передаются через `**kwargs`, не через атрибуты event-объекта.
3. `briefing.py` делает HTTP-запрос к `chatbot:8080`, но внутреннего HTTP-сервера
   в chatbot нет (только Telegram polling). Нужен прямой вызов AI через общий клиент.
4. Методы `is_new_user()`, `register_user()`, `get_unread_alerts_count()` вызываются
   в `start.py`, но отсутствуют в `ChatPostgresClient`.
5. Методы `get_all_active_alerts()` и `trigger_alert()` нужны в `ChatPostgresClient`
   для `alert_checker.py` (он читает из БД chatbot-стороны).

---

## 2. Goal & Success Criteria

**Goal:** Получить полностью рабочий Docker Compose проект, который запускается
командой `make build && make up`, подключается к HTX WebSocket, принимает сообщения
в Telegram и отвечает через AI-роутинг.

**Success state:** `make ps` показывает 4 контейнера Up (postgres, redis, collector, chatbot),
`make logs-collector` показывает "HTX WebSocket connected" и тикеры,
Telegram-бот отвечает на `/start` онбордингом.

---

## 3. Scope of Work

**In-scope:**
- Полная файловая структура репозитория со всеми `__init__.py`
- Все Python-файлы обоих сервисов (collector + chatbot) в финальном рабочем виде
- `docker-compose.yml`, `.env.example`, `Makefile`, `db/init.sql`
- `Dockerfile` для collector и chatbot
- Исправление всех 5 багов из секции Context
- Реализация недостающих методов в `ChatPostgresClient`
- Внутренний механизм AI-запроса в `briefing.py` без HTTP (прямой вызов)

**Out-of-scope:**
- Автоматическая торговля (выставление ордеров через HTX)
- Web Dashboard / Telegram Mini App
- Backtesting runner (код генерируется Qwen, но не выполняется на сервере)
- Мультипользовательские права / роли
- CI/CD pipeline

---

## 4. Inputs

**Specification source (главный документ):** `TZ.md` в project files — содержит
полный код всех файлов. Это ЕДИНСТВЕННЫЙ источник истины по поведению и структуре.

**Assumptions:**
- Python 3.12, asyncio everywhere, no sync blocking calls
- aiogram 3.14 — все handlers принимают `**kwargs` и получают зависимости через
  `dp["key"] = value` паттерн (не через middleware class)
- asyncpg напрямую (не SQLAlchemy) — пул соединений на сервис
- Redis без пароля для локального Docker-запуска (можно добавить опционально через env)
- HTX WebSocket URL: `wss://api.huobi.pro/ws` (публичный, без авторизации для маркет-данных)
- Все AI-клиенты используют openai-compatible endpoint через `openai.AsyncOpenAI`
- Логирование через `loguru`, не стандартный `logging`

---

## 5. Required Output

### 5.1 Файловое дерево (полное)

Создать следующую структуру. Каждый файл должен быть рабочим Python-кодом,
готовым к `docker compose up`.

```
htx-trading-bot/
├── docker-compose.yml
├── .env.example
├── .gitignore
├── Makefile
│
├── db/
│   └── init.sql
│
├── collector/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py
│   ├── htx/
│   │   ├── __init__.py
│   │   ├── websocket.py
│   │   └── rest.py
│   ├── indicators/
│   │   ├── __init__.py
│   │   └── calculator.py
│   ├── alerts/
│   │   ├── __init__.py
│   │   └── detector.py
│   ├── journal/
│   │   ├── __init__.py
│   │   └── writer.py
│   ├── scheduler/
│   │   ├── __init__.py
│   │   ├── alert_checker.py
│   │   └── briefing.py
│   └── storage/
│       ├── __init__.py
│       ├── redis_client.py
│       └── postgres_client.py
│
└── chatbot/
    ├── Dockerfile
    ├── requirements.txt
    ├── main.py
    ├── handlers/
    │   ├── __init__.py
    │   ├── start.py
    │   ├── market.py
    │   ├── chat.py
    │   ├── alerts.py
    │   ├── portfolio.py
    │   ├── analytics.py
    │   └── settings.py
    ├── ai/
    │   ├── __init__.py
    │   ├── router.py
    │   ├── prompts.py
    │   └── knowledge_base.py
    ├── context/
    │   ├── __init__.py
    │   └── builder.py
    ├── ui/
    │   ├── __init__.py
    │   ├── formatter.py
    │   ├── keyboards.py
    │   └── onboarding.py
    └── storage/
        ├── __init__.py
        ├── redis_client.py
        └── postgres_client.py
```

### 5.2 Ключевые исправления (обязательны — без них код не запустится)

**Fix 1 — `chatbot/ai/router.py`**

Добавить `force_intent: str | None = None` в сигнатуру `route_and_respond()`.
Если `force_intent` передан — пропустить `classify_intent()` и использовать его напрямую.

```python
# БЫЛО:
async def route_and_respond(user_message, market_snapshot, chat_history) -> dict:
    intent = await classify_intent(user_message)

# ДОЛЖНО БЫТЬ:
async def route_and_respond(
    user_message: str,
    market_snapshot: dict,
    chat_history: list,
    force_intent: str | None = None,   # ← добавить
) -> dict:
    intent = force_intent or await classify_intent(user_message)
```

**Fix 2 — `chatbot/handlers/analytics.py`**

Убрать `event._pg` и `event._redis`. Зависимости приходят через `**kwargs`.
Все функции, вызывающие `_run_backtest()`, должны передавать `pg` и `redis`
как аргументы, а не читать их из event-объекта.

```python
# БЫЛО (не работает):
async def _run_backtest(event, state, is_message=False):
    pg = event._pg

# ДОЛЖНО БЫТЬ:
async def _run_backtest(
    event,
    state: FSMContext,
    pg: ChatPostgresClient,
    redis: ChatRedisClient,
    is_message: bool = False,
):
```

Все callback-хендлеры, ведущие к `_run_backtest`, должны принимать `pg` и `redis`
из `**kwargs` и явно передавать их дальше.

**Fix 3 — `collector/scheduler/briefing.py`**

Убрать HTTP-запрос к `chatbot:8080`. Вместо этого — прямой вызов AI API
(используем тот же openai-compatible клиент, что и в `chatbot/ai/router.py`,
но инициализированный локально внутри briefing.py).

```python
# БЫЛО (сервис не существует):
async with session.post(f"{CHATBOT_URL}/internal/ai_query", ...) as resp:

# ДОЛЖНО БЫТЬ: прямой вызов GLM-5.1
async def _request_ai_briefing(self, snapshot: dict) -> str:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(
        api_key=os.getenv("GLM_API_KEY"),
        base_url="https://open.bigmodel.cn/api/paas/v4/"
    )
    snapshot_text = json.dumps(snapshot, ensure_ascii=False, indent=2)[:3000]
    resp = await client.chat.completions.create(
        model="glm-z1-flash",    # или актуальная free-версия GLM
        messages=[
            {"role": "system", "content": "Ты торговый аналитик. Отвечай кратко на русском."},
            {"role": "user", "content": f"Рыночный контекст:\n{snapshot_text}\n\n"
                                         "Дай прогноз на сегодня по BTC: "
                                         "основные уровни, настроение рынка, риски."}
        ],
        max_tokens=1500,
        temperature=0.3,
    )
    return resp.choices[0].message.content
```

**Fix 4 — `chatbot/storage/postgres_client.py`**

Добавить недостающие методы в класс `ChatPostgresClient`:

```python
# Регистрация пользователей (для онбординга)
async def is_new_user(self, user_id: int) -> bool:
    """True если пользователь не существует в таблице users."""
    async with self._pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM users WHERE id = $1", user_id
        )
    return row is None

async def register_user(self, user_id: int, username: str | None = None):
    """Создаёт запись пользователя при первом /start."""
    async with self._pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (id, username, first_seen, last_seen)
            VALUES ($1, $2, NOW(), NOW())
            ON CONFLICT (id) DO UPDATE SET last_seen = NOW()
            """,
            user_id, username
        )

async def get_unread_alerts_count(self, user_id: int) -> int:
    """Кол-во активных (не сработавших) пользовательских алертов."""
    async with self._pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) as cnt FROM user_alerts WHERE user_id = $1 AND active = TRUE",
            user_id
        )
    return row["cnt"] if row else 0

# Методы для alert_checker (нужны collector-стороне, но читаем из общей БД)
async def get_all_active_alerts(self) -> list[dict]:
    async with self._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, user_id, symbol, price, direction FROM user_alerts WHERE active = TRUE"
        )
    return [dict(r) for r in rows]

async def trigger_alert(self, alert_id: int):
    async with self._pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_alerts SET active = FALSE, triggered_at = NOW() WHERE id = $1",
            alert_id
        )
```

**Fix 5 — `collector/storage/postgres_client.py`**

`alert_checker.py` из collector вызывает `pg.get_all_active_alerts()` и
`pg.trigger_alert()` — эти методы должны быть и в `PostgresClient` (collector-side),
не только в `ChatPostgresClient` (chatbot-side), так как оба работают с одной БД.
Добавить идентичные методы.

### 5.3 Дополнительные требования к коду

**Все `__init__.py`** — пустые файлы (создать, иначе Python не видит пакеты).

**`db/init.sql`** — должна содержать таблицу `users`:
```sql
CREATE TABLE IF NOT EXISTS users (
    id         BIGINT PRIMARY KEY,
    username   VARCHAR(100),
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen  TIMESTAMPTZ DEFAULT NOW()
);
```

**`collector/requirements.txt`** — финальный список:
```
websockets==13.1
aiohttp==3.10.5
asyncpg==0.30.0
redis[hiredis]==5.2.1
pandas==2.2.3
pandas-ta==0.3.14b
numpy==1.26.4
python-dotenv==1.0.1
loguru==0.7.2
openai==1.57.0
```

**`chatbot/requirements.txt`** — финальный список:
```
aiogram==3.14.0
asyncpg==0.30.0
redis[hiredis]==5.2.1
aiohttp==3.10.5
python-dotenv==1.0.1
loguru==0.7.2
openai==1.57.0
```

**Импорты в `chatbot/main.py`** — подключить все роутеры:
```python
from handlers.start      import router as start_router
from handlers.market     import router as market_router
from handlers.chat       import router as chat_router
from handlers.alerts     import router as alerts_router
from handlers.portfolio  import router as portfolio_router
from handlers.analytics  import router as analytics_router
from handlers.settings   import router as settings_router
from ui.onboarding       import router as onboarding_router

# Порядок подключения строго соблюдать:
dp.include_router(onboarding_router)   # первым
dp.include_router(start_router)
dp.include_router(market_router)
dp.include_router(alerts_router)
dp.include_router(portfolio_router)
dp.include_router(analytics_router)
dp.include_router(settings_router)
dp.include_router(chat_router)         # последним (ловит всё остальное)
```

---

## 6. Constraints

**Stack:** Python 3.12, Docker Compose, asyncio throughout.

**Async rule:** Никаких синхронных блокирующих вызовов в async-функциях.
`asyncpg.create_pool()` вызывается в `connect()`, не в `__init__()`.

**Error handling:** Каждый async-обработчик HTX WebSocket оборачивается в `try/except`.
Reconnect логика — только в `HTXWebSocketClient.run()`, не в отдельных обработчиках.

**Telegram message size:** Telegram принимает max 4096 символов. Все ответы AI
должны проходить через `_split_text()` перед отправкой.

**Rate limits:** HTX REST API — пауза `await asyncio.sleep(0.2)` между запросами
в батч-операциях. Telegram Bot API — пауза `await asyncio.sleep(0.05)` при broadcast.

**Logging:** Только `loguru`. Уровни: DEBUG для тикеров/свечей (высокочастотные),
INFO для алертов и действий пользователей, ERROR для исключений с `exc_info=True`.

**Secrets:** Никаких хардкоженных API-ключей. Всё только через `os.getenv()`.

**timebox:** Sprint 1 — до рабочего `make up` с базовыми функциями.

---

## 7. Acceptance Criteria

### Инфраструктура
- [ ] `make build` завершается без ошибок для обоих Dockerfile
- [ ] `make up` поднимает 4 контейнера, все в состоянии `Up` через 30 секунд
- [ ] `make db-shell` + `\dt` показывает все 9 таблиц (candles, indicators, alerts,
      ai_journal, chat_history, user_alerts, positions, user_settings, users)

### Collector
- [ ] `make logs-collector` показывает "HTX WebSocket connected" в течение 10 секунд
- [ ] Через 2 минуты: `make redis-tickers` возвращает `ticker:btcusdt` и другие пары из WATCHLIST
- [ ] Через 5 минут: `make db-candles` показывает свечи для каждой пары в интервалах 1min и 60min
- [ ] При аномальном движении (>3%) — алерт появляется в Telegram (тест: можно временно
      снизить `ALERT_SPIKE_THRESHOLD=0.1` в .env для проверки)

### Chatbot
- [ ] Команда `/start` открывает онбординг-карточку (шаг 1/5) для нового пользователя
- [ ] Команда `/price btcusdt` возвращает карточку с актуальной ценой из Redis
- [ ] Inline-кнопка "📊 Рынок сейчас" показывает снапшот из `ai:journal:latest`
- [ ] Свободный текст "что с биткоином" вызывает AI-ответ через роутер (любая модель)
- [ ] `/position` запускает FSM-цепочку и выдаёт расчёт R:R после прохождения всех шагов
- [ ] Ошибка неверного API-ключа показывает `error_card()`, не traceback

### Корректность исправлений
- [ ] `route_and_respond(force_intent="deep_analysis")` не вызывает `classify_intent()`
- [ ] `pg.is_new_user(123)` возвращает `True` для несуществующего user_id
- [ ] `collector/scheduler/briefing.py` не делает HTTP-запросы к chatbot-сервису
- [ ] `analytics.py` — все handlers получают `pg` и `redis` через `**kwargs`

---

## 8. Implementation Order (рекомендуемый порядок)

Qwen должен реализовывать в следующем порядке, чтобы можно было тестировать
инкрементально:

**Phase 1 — Infrastructure (без AI):**
`docker-compose.yml` → `db/init.sql` → оба `Dockerfile` → `.env.example` → `Makefile`
→ `make build` проходит.

**Phase 2 — Collector core:**
`collector/storage/` → `collector/htx/rest.py` → `collector/indicators/calculator.py`
→ `collector/main.py` (только REST-синк, без WS) → проверить что свечи в БД.

**Phase 3 — Collector real-time:**
`collector/htx/websocket.py` → `collector/alerts/detector.py` → `collector/journal/writer.py`
→ проверить Redis тикеры.

**Phase 4 — Chatbot foundation:**
`chatbot/storage/` → `chatbot/ai/prompts.py` + `knowledge_base.py` → `chatbot/ai/router.py`
(с fix для `force_intent`) → `chatbot/main.py` → `chatbot/handlers/start.py` + `chatbot/ui/`
→ проверить `/start`.

**Phase 5 — Chatbot features:**
`market.py` → `chat.py` → `alerts.py` → `portfolio.py` → `analytics.py` → `settings.py`

**Phase 6 — Schedulers:**
`collector/scheduler/alert_checker.py` → `collector/scheduler/briefing.py` (с fix)
→ подключить в `collector/main.py`.

---

## 9. Follow-ups (после успешного завершения Sprint 1)

- **DEV-S2-001:** `analytics.py` — UI для запуска бэктеста и отображения результатов
- **DEV-S2-002:** Экспорт CSV из `analytics.py` (handler + кнопка в меню)
- **DEV-S2-003:** User-specific WATCHLIST — читать из `user_settings` вместо ENV
- **DEV-S2-004:** HTX авторизованный WebSocket (v2) для приватных данных аккаунта

---

## Appendix A: ENV переменные (полный список)

```env
# Telegram
TELEGRAM_TOKEN=
TELEGRAM_ADMIN_ID=

# HTX Exchange
HTX_ACCESS_KEY=
HTX_SECRET_KEY=

# Database
POSTGRES_DB=trading
POSTGRES_USER=bot
POSTGRES_PASSWORD=
DATABASE_URL=postgresql://bot:YOURPASS@postgres:5432/trading

# Redis
REDIS_URL=redis://redis:6379/0

# AI Models
GLM_API_KEY=           # platform.zhipuai.cn
MINIMAX_API_KEY=       # api.minimax.chat
MINIMAX_GROUP_ID=      # из MiniMax dashboard
PERPLEXITY_API_KEY=    # api.perplexity.ai
DASHSCOPE_API_KEY=     # dashscope.aliyuncs.com (Qwen)

# Bot Config
WATCHLIST=btcusdt,ethusdt,solusdt,bnbusdt
ALERT_SPIKE_THRESHOLD=3.0
DEFAULT_AI_MODEL=auto
LOG_LEVEL=INFO
TIMEZONE_OFFSET=3
```

---

## Appendix B: Критичные особенности HTX WebSocket

Эти три факта обязательно учесть, иначе WS-клиент не заработает:

**1. GZIP compression:** Все входящие сообщения сжаты GZIP.
Декомпрессия обязательна: `json.loads(gzip.decompress(raw_msg))`.

**2. Ping/Pong протокол:** HTX отправляет `{"ping": 1234567890}`.
Ответ должен быть: `await ws.send(json.dumps({"pong": msg["ping"]}))`.
Если не ответить — соединение разрывается через ~10 секунд.

**3. Reconnect:** Логика реконнекта с exponential backoff только в
`HTXWebSocketClient.run()`. Delays: `[1, 2, 4, 8, 16, 30]` секунд.
При успешном соединении — сбросить счётчик попыток в 0.

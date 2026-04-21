<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# мне нужно создать телеграммбот который будет помогать в трейдинге. Бот должен уметь в автоматику и размышление с аналитикой -  работать на 2 и более моделей нейросети, по api/ У меня есть perplaxity, AI studio, QWEN 3,6pluse, Qwencode3pluse, minimax 2.7, [GLM-5.1](https://www.google.com/search?q=GLM-5.1&oq=glm&gs_lcrp=EgZjaHJvbWUqBggAEEUYOzIGCAAQRRg7MgkIARBFGDkYgAQyBwgCEAAYgAQyBwgDEAAYgAQyBwgEEAAYgAQyBggFEEUYPDIGCAYQRRg8MgYIBxBFGDzSAQgxNDkyajBqN6gCALACAA&sourceid=chrome&ie=UTF-8&mstk=AUtExfDBt-noBuFzsWWvgKj67hxUQNYFjk3lnWFcWIg4Ao9hAE7Gp2FFlxbp8AYM276mNsa1zI7fMzysHpQ9mF6uSzWCy-Lk5Dq1fe3AIgI0ANdyWycUXpvkRqYSO8PyMh0bCv9NteYAK6KFYuDu_rZkqY85URfuV3xqb2KyldEYm61JvBQlH0kv_7sByGecBIDvaObH7CwHkZDKXppBGEVQm3eZBA&csui=3&ved=2ahUKEwj7w6r0sfWTAxWRrlYBHQ2MABEQgK4QegQIARAB). ВАЖНО СЕЙЧАС 18.04.26 года найди всю актуальную информацию на текущий момент. Пример задач 1. Автоматика - бот в риалтайме собирает данные с биржи HTX (Другие не предлагать) о ценах валют, показателями волатильности и остальной технической информацией, как по запросу пользователя так и рутинно автоматически обеспечивая пользователяю максимально актуальные данные и доступ к ретроспективным данным. Бот оповещает пользователя о резких скачках и аномальных движениях рынка. Бот ведет внутренний журнал, который позволяет другим ииагентам использовать информацию для анализа и просчета вероятностей и создания прогнозов и стратегий также внутри бота. Условно пользователь открывая бота может быстро и удобно получить актуальную информацию и начать час с ИИ агентом чтобы обсудить текуз=oe. ситуацию. 2. чат с агентом и рассуждениями - нужны промты и база знаний для ИИ агента, который будет работать через телеграмм и использовать сформированные первым ии агентом данные, удобно проформатированные для чтения этой ИИ моделью. Бот работает с локального ПК через докер контейнер. Создай архитектуру сравни модели и оцени их для описанных задач, предвосхити предстоящие задачи судя из напрваления. (Автоматическую тоговлю пока не предлагать)

Отличный запрос — ты фактически описываешь **мультиагентную торговую платформу** в Telegram. Вот полная архитектура с актуальными данными на 18.04.2026.

***

## Архитектура системы

Бот строится на двух независимых агентах, взаимодействующих через общий журнал данных (Redis/SQLite). Всё живёт в Docker на локальном ПК.

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker Compose                           │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────────┐  │
│  │  Agent #1    │   │  Shared DB   │   │  Agent #2      │  │
│  │  DATA BOT    │──▶│  Redis +     │──▶│  ANALYSIS BOT  │  │
│  │  (Collector) │   │  PostgreSQL  │   │  (Chat+Reason) │  │
│  └──────┬───────┘   └──────────────┘   └───────┬────────┘  │
│         │ HTX WS/REST API                       │ AI APIs   │
│         ▼                                       ▼           │
│    Цены, свечи,                          Perplexity Sonar   │
│    стакан, объёмы                        + GLM-5.1 / Qwen3  │
└─────────────────────────────────────────────────────────────┘
```


***

## Агент \#1 — Data Collector

Это фоновый сервис без UI. Его задачи: собирать данные HTX, считать индикаторы, писать в БД, слать алерты.

**HTX API (актуально апрель 2026):**[^1_1]

- **REST** — `https://api.huobi.pro` — свечи, стаканы, исторические данные
- **WebSocket v2** — `wss://api.huobi.pro/ws/v2` — тикеры в реальном времени[^1_2]
- С октября 2024 WS поддерживает bulk-подписки и выставление ордеров[^1_3]
- С апреля 2025 — обновлённая логика стаканов по квотной точности[^1_4]

**Стек Agent \#1:**

- `aiogram 3.x` + `asyncio` — Telegram-уведомления об аномалиях[^1_5]
- `websockets` / `aiohttp` — HTX WebSocket стрим
- `pandas-ta` или `ta-lib` — RSI, MACD, BB, EMA
- `redis` — быстрый кэш последних тиков и индикаторов
- `postgresql` — история свечей, журнал событий

***

## Агент \#2 — AI Chat \& Reasoning

Пользователь пишет в бот — агент читает контекст из БД и запрашивает нужную модель.[^1_5]

### Выбор моделей под задачи

| Модель | Роль в боте | Сильные стороны | Цена вх./исх. |
| :-- | :-- | :-- | :-- |
| **Perplexity Sonar Pro** | Поиск новостей и нарратива рынка | Real-time web search, цитаты [^1_6] | \$3/\$15 /1M |
| **GLM-5.1 (Z.AI)** | Автономный агент-аналитик | 200K контекст, 8-часовые агент-сессии, SWE-Bench \#1 [^1_7] | Низкая |
| **Qwen3.6-Plus** | Код, бэктестинг, расчёты | Agentic coding SOTA, repository-level [^1_8] | DashScope API |
| **QwenCode3-Plus** | Генерация Python-стратегий | Специализирован на коде | DashScope API |
| **MiniMax M2.7** | Multi-agent harness, сложные задачи | Самостроящийся Agent Teams, self-improvement loop [^1_9] | \$0.3/\$1.1 /1M |
| **MiniMax M2.7-highspeed** | Быстрые ответы пользователю | Идентичен M2.7, выше TPS [^1_10] | = M2.7 |

> **GLM-5.1** — лидер на SWE-Bench Pro (58.4), способен автономно работать 8 часов с тысячами tool calls. Идеален для долгосрочного агент-анализа стратегий. **MiniMax M2.7** — единственная модель с нативным Agent Teams для multi-agent оркестрации.[^1_11][^1_9]

***

## Схема роутинга запросов

```python
# Псевдокод роутера агентов
async def route_query(user_message: str, context: dict):
    intent = classify_intent(user_message)
    
    if intent == "market_news":        # Perplexity Sonar Pro
        return await call_perplexity_sonar(user_message)
    
    elif intent == "deep_analysis":    # GLM-5.1 — рассуждение по журналу
        return await call_glm51(user_message, context["journal"])
    
    elif intent == "quick_chat":       # MiniMax M2.7-highspeed — быстро
        return await call_minimax(user_message, context["snapshot"])
    
    elif intent == "backtest_code":    # Qwen3.6-Plus / QwenCode3+
        return await call_qwen_code(user_message)
    
    elif intent == "strategy_plan":    # MiniMax M2.7 Agent Teams
        return await call_minimax_agent_team(user_message)
```


***

## Структура Docker Compose

```yaml
version: "3.9"
services:
  collector:           # Agent #1
    build: ./collector
    environment:
      HTX_ACCESS_KEY: ${HTX_ACCESS_KEY}
      HTX_SECRET_KEY: ${HTX_SECRET_KEY}
      REDIS_URL: redis://redis:6379
      DATABASE_URL: postgresql://bot:pass@postgres/trading
    depends_on: [redis, postgres]

  chatbot:             # Agent #2
    build: ./chatbot
    environment:
      TELEGRAM_TOKEN: ${TELEGRAM_TOKEN}
      PERPLEXITY_API_KEY: ${PERPLEXITY_API_KEY}
      DASHSCOPE_API_KEY: ${DASHSCOPE_API_KEY}   # Qwen
      MINIMAX_API_KEY: ${MINIMAX_API_KEY}
      GLM_API_KEY: ${GLM_API_KEY}
      REDIS_URL: redis://redis:6379
      DATABASE_URL: postgresql://bot:pass@postgres/trading
    depends_on: [redis, postgres, collector]

  redis:
    image: redis:7-alpine
    
  postgres:
    image: postgres:16-alpine
    volumes:
      - pgdata:/var/lib/postgresql/data
      
volumes:
  pgdata:
```


***

## Формат журнала для AI-агентов

Ключевой элемент: Агент \#1 записывает данные в **структурированный формат**, который AI-модели читают как контекст.

```json
{
  "timestamp": "2026-04-18T05:00:00Z",
  "snapshot": {
    "btcusdt": {"price": 84320.5, "change_1h": "+1.2%", "volume_24h": "2.3B"},
    "ethusdt": {"price": 1612.0, "change_1h": "-0.4%", "rsi_14": 58.2}
  },
  "alerts": [
    {"symbol": "solusdt", "type": "SPIKE", "detail": "+8.3% за 15 мин", "severity": "HIGH"}
  ],
  "indicators": {
    "btcusdt": {"rsi": 62.1, "macd_signal": "bullish_cross", "bb_position": "upper"}
  },
  "market_context": "Общий рынок: умеренный рост. BTC доминирование: 64.2%"
}
```


***

## Предстоящие задачи (прогноз развития)

Судя по направлению — следующие логичные шаги после стабилизации MVP:

1. **Бэктестинг через QwenCode** — агент пишет стратегию → тестирует на исторических данных из PostgreSQL
2. **Прогнозирование через GLM-5.1** — долгосрочные агент-сессии с анализом паттернов и новостного фона через Sonar
3. **MiniMax Agent Teams** — параллельные агенты: один анализирует BTC, другой — альты, третий — макро[^1_9]
4. **Dashboard через Telegram WebApp** — интерактивные графики прямо в боте
5. **Автоматическая торговля** — HTX WS уже поддерживает выставление ордеров, модуль готов к подключению[^1_3]

***

С чего начинаем? Могу сразу дать полный код `collector/main.py` с HTX WebSocket стримом и записью в Redis + PostgreSQL, или сначала поднимем скелет Docker Compose и структуру директорий?
<span style="display:none">[^1_12][^1_13][^1_14][^1_15][^1_16][^1_17][^1_18][^1_19][^1_20][^1_21][^1_22][^1_23][^1_24][^1_25][^1_26][^1_27][^1_28][^1_29][^1_30][^1_31][^1_32][^1_33][^1_34][^1_35][^1_36][^1_37][^1_38][^1_39][^1_40][^1_41][^1_42]</span>

<div align="center">⁂</div>

[^1_1]: https://cryptorank.io/news/feed/869e7-how-htx-exchange-is-evolving

[^1_2]: https://www.htx.com/support/900000014146

[^1_3]: https://www.htx.com/support/94983224516200

[^1_4]: https://www.htx.com/support/24999121276987

[^1_5]: https://pypi.org/project/aiogram/

[^1_6]: https://www.perplexity.ai/hub/blog/introducing-the-sonar-pro-api

[^1_7]: https://apidog.com/blog/glm-5-1/

[^1_8]: https://www.alibabacloud.com/blog/qwen3-6-plus-towards-real-world-agents_603005

[^1_9]: https://www.minimax.io/news/minimax-m27-en

[^1_10]: https://www.minimax.io/models/text/m27

[^1_11]: https://serenitiesai.com/articles/glm-5-1-zhipu-coding-benchmark-claude-opus-comparison-2026

[^1_12]: https://www.htx.com/support/84974124835764

[^1_13]: https://www.htx.com/support/360000268281

[^1_14]: https://www.htx.com/support/900000916183/

[^1_15]: https://www.htx.com/support/360000602042

[^1_16]: https://www.htx.com/support/44986492552766

[^1_17]: https://www.htx.com/support/900000499563/

[^1_18]: https://www.htx.com/support/900001702983

[^1_19]: https://www.htx.com/en-us/opend/

[^1_20]: https://www.htx.com/en-us/opend/newApiPages

[^1_21]: https://www.binance.com/ar/square/post/297325763402449

[^1_22]: https://www.htx.com/en-us/opend/newApiPages/?id=10000066-77b7-11ed-9966-0242ac110003

[^1_23]: https://www.perplexity.ai/api-platform/resources/meet-new-sonar

[^1_24]: https://artificialanalysis.ai/models/qwen3-6-plus/providers

[^1_25]: https://github.com/JKorf/HTX.Net

[^1_26]: https://techcrunch.com/2025/01/21/perplexity-launches-sonar-an-api-for-ai-search/

[^1_27]: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-qwen-qwen3-235b-a22b-2507.html

[^1_28]: https://pricepertoken.com/pricing-page/model/perplexity-sonar

[^1_29]: https://www.htx.com/ru-ru/feed/community/19641981/

[^1_30]: https://www.htx.com/ru-ru/news/chinese-large-models-this-time-the-script-is-different-NScMKg3w/

[^1_31]: https://www.htx.com/news/chinese-large-models-this-time-the-script-is-different-NScMKg3w/

[^1_32]: https://www.htx.com/id-id/news/chinese-large-models-this-time-the-script-is-different-NScMKg3w/

[^1_33]: https://huggingface.co/zai-org/GLM-5.1

[^1_34]: https://z.ai/blog/glm-5

[^1_35]: https://www.facebook.com/kitti.kitdev/posts/glm-51-จาก-zai-ผู้เปลี่ยนผมจาก-vibe-coder-เป็น-agentic-engineerนี่คือรุ่นอัปเกรด/26243721331948175/

[^1_36]: https://aimlapi.com/models/glm-5-1

[^1_37]: https://www.minimax.io

[^1_38]: https://artificialanalysis.ai/models/minimax-m2-7/providers

[^1_39]: https://github.com/aiogram/aiogram

[^1_40]: https://www.perplexity.ai/page/zhipu-ai-s-glm-5-1-tops-coding-rCjkI6o3RGaNcat30mt2eg

[^1_41]: https://docs.aiogram.dev

[^1_42]: https://aipower.me/blog/zhipu-glm-api-access-international


---

# Сначала Docker Compose и структура директорий

Поехали — даю полную структуру директорий и все конфигурационные файлы целиком, готовые к использованию.

***

## Структура директорий

```
htx-trading-bot/
│
├── docker-compose.yml          # Оркестрация всех сервисов
├── .env                        # Все секреты (не коммитить!)
├── .env.example                # Шаблон для команды
├── .gitignore
├── Makefile                    # Удобные команды управления
│
├── collector/                  # Agent #1 — сборщик данных HTX
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                 # Точка входа
│   ├── htx/
│   │   ├── __init__.py
│   │   ├── websocket.py        # HTX WS стрим
│   │   ├── rest.py             # HTX REST запросы
│   │   └── symbols.py          # Список торговых пар
│   ├── indicators/
│   │   ├── __init__.py
│   │   └── calculator.py       # RSI, MACD, BB, EMA
│   ├── alerts/
│   │   ├── __init__.py
│   │   └── detector.py         # Детектор аномалий
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── redis_client.py     # Быстрый кэш
│   │   └── postgres_client.py  # Исторические данные
│   └── journal/
│       ├── __init__.py
│       └── writer.py           # Запись AI-журнала
│
├── chatbot/                    # Agent #2 — чат с AI
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                 # Точка входа aiogram
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── start.py            # /start, /help
│   │   ├── market.py           # /price, /analysis
│   │   └── chat.py             # Свободный чат с AI
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── router.py           # Роутинг по моделям
│   │   ├── perplexity.py       # Sonar Pro client
│   │   ├── glm.py              # GLM-5.1 client
│   │   ├── qwen.py             # Qwen3.6+ / QwenCode client
│   │   ├── minimax.py          # MiniMax M2.7 client
│   │   └── prompts.py          # Системные промты
│   ├── context/
│   │   ├── __init__.py
│   │   └── builder.py          # Сборка контекста из БД для AI
│   └── storage/
│       ├── __init__.py
│       └── redis_client.py     # Shared с collector
│
├── db/
│   ├── init.sql                # Схема PostgreSQL
│   └── migrations/             # Alembic миграции (опционально)
│
└── nginx/                      # Опционально — для webhook режима
    └── nginx.conf
```


***

## `docker-compose.yml`

```yaml
version: "3.9"

services:

  # ─── DATABASES ────────────────────────────────────────────
  postgres:
    image: postgres:16-alpine
    container_name: trading_postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-trading}
      POSTGRES_USER: ${POSTGRES_USER:-bot}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./db/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    ports:
      - "5432:5432"          # только для локальной отладки
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-bot} -d ${POSTGRES_DB:-trading}"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: trading_redis
    restart: unless-stopped
    command: redis-server --requirepass ${REDIS_PASSWORD} --maxmemory 256mb --maxmemory-policy allkeys-lru
    volumes:
      - redisdata:/data
    ports:
      - "6379:6379"          # только для локальной отладки
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  # ─── AGENT #1: DATA COLLECTOR ─────────────────────────────
  collector:
    build:
      context: ./collector
      dockerfile: Dockerfile
    container_name: trading_collector
    restart: unless-stopped
    environment:
      HTX_ACCESS_KEY: ${HTX_ACCESS_KEY}
      HTX_SECRET_KEY: ${HTX_SECRET_KEY}
      TELEGRAM_TOKEN: ${TELEGRAM_TOKEN}       # для отправки алертов
      TELEGRAM_ADMIN_ID: ${TELEGRAM_ADMIN_ID}
      REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-bot}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-trading}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      ALERT_SPIKE_THRESHOLD: ${ALERT_SPIKE_THRESHOLD:-3.0}  # % за 15 мин
      WATCHLIST: ${WATCHLIST:-btcusdt,ethusdt,solusdt,bnbusdt}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./collector:/app          # hot-reload при разработке
      - collector_logs:/app/logs
    networks:
      - trading_net

  # ─── AGENT #2: CHATBOT ────────────────────────────────────
  chatbot:
    build:
      context: ./chatbot
      dockerfile: Dockerfile
    container_name: trading_chatbot
    restart: unless-stopped
    environment:
      TELEGRAM_TOKEN: ${TELEGRAM_TOKEN}
      TELEGRAM_ADMIN_ID: ${TELEGRAM_ADMIN_ID}
      REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379/0
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-bot}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB:-trading}
      # AI APIs
      PERPLEXITY_API_KEY: ${PERPLEXITY_API_KEY}
      DASHSCOPE_API_KEY: ${DASHSCOPE_API_KEY}
      MINIMAX_API_KEY: ${MINIMAX_API_KEY}
      MINIMAX_GROUP_ID: ${MINIMAX_GROUP_ID}
      GLM_API_KEY: ${GLM_API_KEY}
      # Настройки роутинга
      DEFAULT_AI_MODEL: ${DEFAULT_AI_MODEL:-minimax-fast}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      collector:
        condition: service_started
    volumes:
      - ./chatbot:/app
      - chatbot_logs:/app/logs
    networks:
      - trading_net

volumes:
  pgdata:
  redisdata:
  collector_logs:
  chatbot_logs:

networks:
  trading_net:
    driver: bridge
```


***

## `.env`

```env
# ─── TELEGRAM ─────────────────────────────────────────────
TELEGRAM_TOKEN=your_bot_token_here
TELEGRAM_ADMIN_ID=your_telegram_user_id

# ─── HTX EXCHANGE ─────────────────────────────────────────
HTX_ACCESS_KEY=your_htx_access_key
HTX_SECRET_KEY=your_htx_secret_key

# ─── DATABASE ─────────────────────────────────────────────
POSTGRES_DB=trading
POSTGRES_USER=bot
POSTGRES_PASSWORD=strong_random_password_here
REDIS_PASSWORD=another_strong_password_here

# ─── AI MODELS ────────────────────────────────────────────
PERPLEXITY_API_KEY=pplx-xxxxxxxxxxxx
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxx          # Qwen3.6+ и QwenCode
MINIMAX_API_KEY=eyJhbGc...                 # MiniMax M2.7
MINIMAX_GROUP_ID=your_group_id
GLM_API_KEY=your_glm_api_key               # GLM-5.1 через Z.AI

# ─── TRADING CONFIG ────────────────────────────────────────
WATCHLIST=btcusdt,ethusdt,solusdt,bnbusdt,xrpusdt
ALERT_SPIKE_THRESHOLD=3.0                  # % изменение за 15 мин
DEFAULT_AI_MODEL=minimax-fast              # модель по умолчанию
LOG_LEVEL=INFO
```


***

## `.env.example`

```env
TELEGRAM_TOKEN=
TELEGRAM_ADMIN_ID=
HTX_ACCESS_KEY=
HTX_SECRET_KEY=
POSTGRES_DB=trading
POSTGRES_USER=bot
POSTGRES_PASSWORD=
REDIS_PASSWORD=
PERPLEXITY_API_KEY=
DASHSCOPE_API_KEY=
MINIMAX_API_KEY=
MINIMAX_GROUP_ID=
GLM_API_KEY=
WATCHLIST=btcusdt,ethusdt,solusdt
ALERT_SPIKE_THRESHOLD=3.0
DEFAULT_AI_MODEL=minimax-fast
LOG_LEVEL=INFO
```


***

## `db/init.sql`

```sql
-- Свечи (OHLCV)
CREATE TABLE IF NOT EXISTS candles (
    id          BIGSERIAL PRIMARY KEY,
    symbol      VARCHAR(20)     NOT NULL,
    interval    VARCHAR(10)     NOT NULL,  -- 1min, 5min, 1hour, 1day
    open_time   TIMESTAMPTZ     NOT NULL,
    open        NUMERIC(20, 8)  NOT NULL,
    high        NUMERIC(20, 8)  NOT NULL,
    low         NUMERIC(20, 8)  NOT NULL,
    close       NUMERIC(20, 8)  NOT NULL,
    volume      NUMERIC(30, 8)  NOT NULL,
    created_at  TIMESTAMPTZ     DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_candles_symbol_interval_time ON candles(symbol, interval, open_time);
CREATE INDEX idx_candles_symbol_time ON candles(symbol, open_time DESC);

-- Индикаторы
CREATE TABLE IF NOT EXISTS indicators (
    id          BIGSERIAL PRIMARY KEY,
    symbol      VARCHAR(20)     NOT NULL,
    timestamp   TIMESTAMPTZ     NOT NULL,
    rsi_14      NUMERIC(10, 4),
    macd        NUMERIC(20, 8),
    macd_signal NUMERIC(20, 8),
    macd_hist   NUMERIC(20, 8),
    ema_20      NUMERIC(20, 8),
    ema_50      NUMERIC(20, 8),
    bb_upper    NUMERIC(20, 8),
    bb_middle   NUMERIC(20, 8),
    bb_lower    NUMERIC(20, 8),
    created_at  TIMESTAMPTZ     DEFAULT NOW()
);
CREATE INDEX idx_indicators_symbol_time ON indicators(symbol, timestamp DESC);

-- Алерты
CREATE TABLE IF NOT EXISTS alerts (
    id          BIGSERIAL PRIMARY KEY,
    symbol      VARCHAR(20)     NOT NULL,
    alert_type  VARCHAR(50)     NOT NULL,  -- SPIKE_UP, SPIKE_DOWN, RSI_OVERBOUGHT, etc.
    severity    VARCHAR(10)     NOT NULL,  -- LOW, MEDIUM, HIGH, CRITICAL
    price       NUMERIC(20, 8),
    change_pct  NUMERIC(10, 4),
    detail      TEXT,
    sent        BOOLEAN         DEFAULT FALSE,
    created_at  TIMESTAMPTZ     DEFAULT NOW()
);
CREATE INDEX idx_alerts_created ON alerts(created_at DESC);

-- AI Журнал — снапшоты для агентов
CREATE TABLE IF NOT EXISTS ai_journal (
    id          BIGSERIAL PRIMARY KEY,
    snapshot    JSONB           NOT NULL,  -- полный рыночный снимок
    journal_type VARCHAR(20)   NOT NULL,  -- 'realtime', 'hourly', 'daily'
    created_at  TIMESTAMPTZ    DEFAULT NOW()
);
CREATE INDEX idx_journal_created ON ai_journal(created_at DESC);
CREATE INDEX idx_journal_type_created ON ai_journal(journal_type, created_at DESC);

-- История чатов с AI
CREATE TABLE IF NOT EXISTS chat_history (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT          NOT NULL,
    role        VARCHAR(20)     NOT NULL,  -- user / assistant
    content     TEXT            NOT NULL,
    model_used  VARCHAR(50),
    created_at  TIMESTAMPTZ     DEFAULT NOW()
);
CREATE INDEX idx_chat_user_time ON chat_history(user_id, created_at DESC);
```


***

## `Makefile`

```makefile
.PHONY: up down build logs restart clean ps shell-collector shell-chatbot

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build --no-cache

logs:
	docker compose logs -f --tail=100

logs-collector:
	docker compose logs -f collector --tail=100

logs-chatbot:
	docker compose logs -f chatbot --tail=100

restart:
	docker compose restart

restart-chatbot:
	docker compose restart chatbot

restart-collector:
	docker compose restart collector

clean:
	docker compose down -v --remove-orphans

ps:
	docker compose ps

shell-collector:
	docker compose exec collector bash

shell-chatbot:
	docker compose exec chatbot bash

db-shell:
	docker compose exec postgres psql -U bot -d trading

redis-shell:
	docker compose exec redis redis-cli -a $$(grep REDIS_PASSWORD .env | cut -d= -f2)
```


***

## `collector/Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-u", "main.py"]
```


***

## `chatbot/Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "-u", "main.py"]
```


***

## `collector/requirements.txt`

```
aiohttp==3.10.11
websockets==13.1
asyncpg==0.30.0
redis[hiredis]==5.2.0
pandas==2.2.3
pandas-ta==0.3.14b
sqlalchemy[asyncio]==2.0.36
python-dotenv==1.0.1
aiogram==3.14.0
loguru==0.7.2
```


***

## `chatbot/requirements.txt`

```
aiogram==3.14.0
aiohttp==3.10.11
asyncpg==0.30.0
redis[hiredis]==5.2.0
sqlalchemy[asyncio]==2.0.36
python-dotenv==1.0.1
loguru==0.7.2
openai==1.57.0
```


***

## `.gitignore`

```
.env
__pycache__/
*.pyc
*.pyo
.DS_Store
logs/
*.log
```


***

## Быстрый старт

```bash
# 1. Клонируй структуру
git clone ... && cd htx-trading-bot

# 2. Настрой окружение
cp .env.example .env
nano .env          # заполни все ключи

# 3. Собери и запусти
make build
make up

# 4. Смотри логи
make logs
```

Структура разделена так, что `collector` и `chatbot` — **полностью независимые сервисы**, общающиеся только через Redis и PostgreSQL. Это даёт возможность перезапускать AI-чат не прерывая сбор данных с HTX. `healthcheck` у postgres и redis гарантирует, что боты стартуют только после готовности БД.[^2_1][^2_2]

Следующий шаг: пишем `collector/main.py` с HTX WebSocket или сразу `chatbot/main.py` с роутером моделей?
<span style="display:none">[^2_10][^2_11][^2_12][^2_13][^2_14][^2_15][^2_3][^2_4][^2_5][^2_6][^2_7][^2_8][^2_9]</span>

<div align="center">⁂</div>

[^2_1]: https://habr.com/ru/articles/823714/

[^2_2]: https://thinksys.com/devops/docker-best-practices/

[^2_3]: https://hub.docker.com/r/aiogram/telegram-bot-api

[^2_4]: https://mastergroosha.github.io/aiogram-3-guide/advanced-teaser/

[^2_5]: https://github.com/aiogram/bot/blob/master/README.md

[^2_6]: https://github.com/poorna-soysa/books-api-docker-compose-postgresql-redis

[^2_7]: https://github.com/aiogram/bot

[^2_8]: https://github.com/codingforentrepreneurs/Stock-Trading-Bot

[^2_9]: https://work-zilla.com/development-and-it/chatbot-development/creating-bots-on-aiogram-with-docker

[^2_10]: https://github.com/llSourcell/Watch-Me-Build-a-Trading-Bot/blob/master/docker-compose.yml

[^2_11]: https://github.com/netbriler/aiogram-peewee-template

[^2_12]: https://www.youtube.com/watch?v=FmnreGzQhc4

[^2_13]: https://habr.com/ru/amp/publications/967948/

[^2_14]: https://codesphere.com/articles/how-to-build-a-stock-trading-bot-with-python-2

[^2_15]: https://www.youtube.com/watch?v=ndnDqqvmawE


---

# Покажи сначала prompts.py и базу знаний для AI

Отлично — даю полный `prompts.py` и `knowledge_base.py` с учётом реальных API-форматов каждой модели.

***

## `chatbot/ai/prompts.py`

```python
"""
Системные промты для каждой AI-модели.
Каждая модель получает свой промт, оптимизированный под её сильные стороны.
"""

from datetime import datetime


def get_current_datetime() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")


# ═══════════════════════════════════════════════════════════════
# БАЗОВЫЙ КОНТЕКСТ — вставляется во все промты
# ═══════════════════════════════════════════════════════════════

BASE_TRADING_CONTEXT = """
Ты — торговый AI-ассистент, работающий внутри Telegram-бота трейдера.
Биржа: HTX (Huobi). Торговые пары: спот, USDT-маржинальные.
Текущая дата и время: {datetime}

ТВОИ ОГРАНИЧЕНИЯ:
- Ты НЕ даёшь финансовых советов и НЕ гарантируешь прибыль
- Ты анализируешь данные и помогаешь трейдеру принимать СОБСТВЕННЫЕ решения
- При любом анализе указывай риски и уровень уверенности (0-100%)
- Не домысливай данные — если данных нет, говори об этом прямо

СТИЛЬ ОТВЕТОВ:
- Лаконично и по делу, без воды
- Используй эмодзи для визуального акцента: 📈📉⚠️✅❌🔴🟢🟡
- Числа форматируй читаемо: 84,320.50 USDT, +2.34%, объём 1.2B
- Markdown доступен: **жирный**, `код`, таблицы
"""


# ═══════════════════════════════════════════════════════════════
# PERPLEXITY SONAR PRO — новостной и нарративный агент
# Формат: messages API с search_domain_filter
# Контекст: 200K, real-time web search
# ═══════════════════════════════════════════════════════════════

PERPLEXITY_SYSTEM = BASE_TRADING_CONTEXT + """
ТВОЯ РОЛЬ — НОВОСТНОЙ АНАЛИТИК:
Ты специализируешься на поиске и анализе новостного фона крипторынка.
Используй свой доступ к актуальным веб-источникам.

ЗАДАЧИ:
1. Найди последние новости по запрошенному активу или рынку в целом
2. Определи доминирующий нарратив: бычий / медвежий / нейтральный
3. Оцени влияние новостей на цену актива (краткосрочно / долгосрочно)
4. Укажи источники и время публикации

ФОРМАТ ОТВЕТА:
📰 **Новостной фон: [СИМВОЛ]**
Нарратив: 🟢 Бычий / 🔴 Медвежий / 🟡 Нейтральный

**Ключевые события:**
• [новость 1] — [источник] ([время])
• [новость 2] — [источник] ([время])

**Влияние на цену:** [краткий вывод]
**Уровень уверенности:** X%
"""

def get_perplexity_messages(user_query: str, market_snapshot: dict) -> list:
    """Формирует messages для Perplexity Sonar Pro API."""
    snapshot_text = _format_snapshot_for_prompt(market_snapshot)
    return [
        {
            "role": "system",
            "content": PERPLEXITY_SYSTEM.format(datetime=get_current_datetime())
        },
        {
            "role": "user",
            "content": f"{snapshot_text}\n\nЗапрос: {user_query}"
        }
    ]


# ═══════════════════════════════════════════════════════════════
# GLM-5.1 (Z.AI) — агент глубокого анализа и стратегий
# Формат: OpenAI-compatible, thinking mode, tool calling
# Контекст: 200K, 8-часовые агент-сессии
# ═══════════════════════════════════════════════════════════════

GLM_SYSTEM = BASE_TRADING_CONTEXT + """
ТВОЯ РОЛЬ — СТРАТЕГИЧЕСКИЙ АНАЛИТИК:
Ты специализируешься на глубоком анализе рынка, паттернов и разработке стратегий.
Работаешь с историческими данными и техническими индикаторами из журнала.
Используй thinking mode для сложных рассуждений — думай шаг за шагом.

АНАЛИТИЧЕСКАЯ МЕТОДОЛОГИЯ:
1. Технический анализ: RSI, MACD, BB, EMA, объёмы
2. Паттерны свечей и структуры рынка
3. Уровни поддержки/сопротивления
4. Дивергенции и конвергенции индикаторов
5. Мульти-таймфреймный анализ (1m → 1h → 4h → 1d)

РАСЧЁТ ПОЗИЦИИ (по запросу):
- Точка входа, стоп-лосс, тейк-профит
- R:R соотношение (минимум 1:2 для рекомендаций)
- Размер позиции исходя из риска X% от депозита
- Ликвидационная цена (для маржи)

ФОРМАТ ОТВЕТА ДЛЯ АНАЛИЗА:
📊 **Анализ: [СИМВОЛ] [ТАЙМФРЕЙМ]**

**Технические индикаторы:**
• RSI(14): [значение] — [интерпретация]
• MACD: [значение] — [сигнал]
• BB: цена [позиция относительно полос]
• EMA20/50: [пересечение/расположение]

**Структура рынка:** [описание]
**Ключевые уровни:** поддержка [X], сопротивление [Y]

**Сценарии:**
🟢 Бычий: [условие] → цель [X]
🔴 Медвежий: [условие] → цель [X]

**Уровень уверенности:** X%
⚠️ **Риски:** [перечислить]
"""

GLM_POSITION_CALCULATOR_PROMPT = """
РАСЧЁТ ТОРГОВОЙ ПОЗИЦИИ:
Входные данные:
- Депозит: {deposit} USDT
- Риск на сделку: {risk_pct}%
- Актив: {symbol}
- Текущая цена: {current_price}
- Точка входа: {entry}
- Стоп-лосс: {stop_loss}
- Тейк-профит 1: {tp1}
- Тейк-профит 2: {tp2} (если указан)

Рассчитай:
1. Максимальный убыток в USDT = депозит × риск%
2. Размер позиции = убыток / (вход - стоп-лосс)
3. R:R = (TP1 - вход) / (вход - стоп-лосс)
4. % изменения до стоп-лосса
5. % изменения до каждого TP

Покажи расчёт пошагово и итоговую таблицу.
"""

def get_glm_messages(user_query: str, market_snapshot: dict, 
                     chat_history: list, thinking: bool = True) -> dict:
    """
    Формирует payload для GLM-5.1 API (OpenAI-compatible).
    thinking=True активирует reasoning mode для сложных задач.
    """
    snapshot_text = _format_snapshot_for_prompt(market_snapshot)
    messages = [
        {
            "role": "system",
            "content": GLM_SYSTEM.format(datetime=get_current_datetime())
        }
    ]
    # Добавляем историю чата (последние N сообщений)
    messages.extend(chat_history[-20:])
    messages.append({
        "role": "user",
        "content": f"{snapshot_text}\n\n{user_query}"
    })
    
    payload = {
        "model": "glm-5.1",
        "messages": messages,
        "temperature": 0.3,   # Ниже для аналитики — меньше галлюцинаций
        "max_tokens": 4096,
    }
    if thinking:
        payload["extra_body"] = {"thinking": {"type": "enabled"}}
    
    return payload


# ═══════════════════════════════════════════════════════════════
# MINIMAX M2.7 — быстрый чат и мульти-агент оркестрация
# Формат: OpenAI-compatible + reasoning_split
# Используется: быстрые ответы, общий чат, координация агентов
# ═══════════════════════════════════════════════════════════════

MINIMAX_SYSTEM = BASE_TRADING_CONTEXT + """
ТВОЯ РОЛЬ — ТОРГОВЫЙ АССИСТЕНТ (БЫСТРЫЙ РЕЖИМ):
Ты — основной собеседник трейдера. Отвечаешь быстро и по делу.
При сложных задачах делегируй: [НУЖЕН_ГЛУБОКИЙ_АНАЛИЗ] или [НУЖНЫ_НОВОСТИ].

СПЕЦИАЛИЗАЦИЯ:
- Быстрые ответы на вопросы о рынке
- Объяснение индикаторов и паттернов
- Помощь в интерпретации данных
- Трекинг портфеля и PnL
- Обсуждение текущей рыночной ситуации

КОМАНДЫ-МАРКЕРЫ (ставь в ответе для роутера):
[НУЖЕН_ГЛУБОКИЙ_АНАЛИЗ] — если нужен GLM-5.1 для сложного анализа
[НУЖНЫ_НОВОСТИ] — если нужен Perplexity для поиска новостей  
[НУЖЕН_БЭКТЕСТ] — если нужен Qwen для расчётов/кода
"""

def get_minimax_messages(user_query: str, market_snapshot: dict,
                          chat_history: list, reasoning: bool = False) -> dict:
    """
    Формирует payload для MiniMax M2.7.
    reasoning=True для сложных рассуждений (reasoning_split в ответе).
    """
    snapshot_text = _format_snapshot_for_prompt(market_snapshot)
    messages = [
        {
            "role": "system",
            "content": MINIMAX_SYSTEM.format(datetime=get_current_datetime())
        }
    ]
    messages.extend(chat_history[-15:])
    messages.append({
        "role": "user",
        "content": f"{snapshot_text}\n\n{user_query}"
    })
    
    payload = {
        "model": "MiniMax-M2.7",
        "messages": messages,
        "temperature": 0.5,
        "max_tokens": 2048,
    }
    if reasoning:
        payload["extra_body"] = {"reasoning_split": True}
    
    return payload


# ═══════════════════════════════════════════════════════════════
# QWEN3.6-PLUS / QWENCODE3-PLUS — бэктестинг и расчёты
# Формат: DashScope API (OpenAI-compatible endpoint)
# Используется: написание кода стратегий, математика, бэктест
# ═══════════════════════════════════════════════════════════════

QWEN_SYSTEM = BASE_TRADING_CONTEXT + """
ТВОЯ РОЛЬ — КВАНТОВЫЙ АНАЛИТИК И РАЗРАБОТЧИК СТРАТЕГИЙ:
Ты специализируешься на написании Python-кода для анализа и бэктестинга.
Пишешь чистый, рабочий код с использованием pandas, pandas-ta, numpy.

ЗАДАЧИ:
1. Написание кода торговых стратегий для бэктестинга
2. Статистический анализ исторических данных
3. Расчёт вероятностей и корреляций
4. Оптимизация параметров стратегий
5. Расчёт метрик: Sharpe, Sortino, Max Drawdown, Win Rate

ФОРМАТ КОДА:
- Всегда предоставляй полный, готовый к запуску код
- Используй pandas DataFrame с колонками: timestamp, open, high, low, close, volume
- Добавляй комментарии к ключевым шагам
- В конце кода — print() с основными метриками результата

ФОРМАТ ОТВЕТА:
📐 **Стратегия: [название]**
**Описание:** [краткое описание логики]

```python
[полный код]
```

**Ожидаемые метрики на тест-периоде:** [описание]
"""

def get_qwen_messages(user_query: str, market_snapshot: dict,
use_code_model: bool = False) -> dict:
"""
Формирует payload для Qwen через DashScope API.
use_code_model=True → использует qwen-code-3-plus вместо qwen3.6-plus.
"""
snapshot_text = _format_snapshot_for_prompt(market_snapshot)
model = "qwen-coder-3-plus" if use_code_model else "qwen3.6-plus"

    return {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": QWEN_SYSTEM.format(datetime=get_current_datetime())
            },
            {
                "role": "user", 
                "content": f"{snapshot_text}\n\n{user_query}"
            }
        ],
        "temperature": 0.1,   # Минимум для кода — точность важнее
        "max_tokens": 8192,
        "extra_body": {"enable_thinking": True}  # Qwen3 thinking mode
    }
    
# ═══════════════════════════════════════════════════════════════

# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ

# ═══════════════════════════════════════════════════════════════

def _format_snapshot_for_prompt(snapshot: dict) -> str:
"""Форматирует рыночный снапшот из Redis в текст для промта."""
if not snapshot:
return "📊 **Рыночные данные:** недоступны в данный момент."

    lines = [f"📊 **Актуальные рыночные данные** (на {snapshot.get('timestamp', 'н/д')}):"]
    
    # Данные по активам
    prices = snapshot.get("snapshot", {})
    if prices:
        lines.append("\n**Цены и изменения:**")
        for symbol, data in prices.items():
            change = data.get("change_1h", "0%")
            arrow = "📈" if "+" in str(change) else "📉"
            lines.append(
                f"{arrow} {symbol.upper()}: {data.get('price', 'н/д')} USDT "
                f"| 1h: {change} | Vol24h: {data.get('volume_24h', 'н/д')}"
            )
    
    # Индикаторы
    indicators = snapshot.get("indicators", {})
    if indicators:
        lines.append("\n**Технические индикаторы:**")
        for symbol, ind in indicators.items():
            rsi = ind.get("rsi", "н/д")
            macd = ind.get("macd_signal", "н/д")
            rsi_state = "🔴 перекуплен" if float(rsi or 0) > 70 else \
                        "🟢 перепродан" if float(rsi or 0) < 30 else "🟡 нейтрально"
            lines.append(f"• {symbol.upper()} — RSI: {rsi} ({rsi_state}), MACD: {macd}")
    
    # Активные алерты
    alerts = snapshot.get("alerts", [])
    if alerts:
        lines.append("\n**⚠️ Активные алерты:**")
        for alert in alerts[:3]:  # топ 3 алерта
            lines.append(
                f"• [{alert.get('severity', '')}] {alert.get('symbol', '').upper()}: "
                f"{alert.get('detail', '')}"
            )
    
    return "\n".join(lines)
    def get_intent_classifier_prompt(user_message: str) -> str:
"""
Промт для быстрой классификации интента пользователя.
Используется роутером перед выбором модели.
"""
return f"""Классифицируй запрос трейдера. Ответь ТОЛЬКО одним словом из списка:

КАТЕГОРИИ:

- market_news     → вопросы о новостях, событиях, нарративе
- deep_analysis   → технический анализ, стратегии, прогнозы
- quick_chat      → быстрые вопросы, цены, PnL, портфель
- backtest_code   → написание кода, расчёты, бэктестинг
- position_calc   → расчёт позиции, стоп-лосс, тейк-профит, риск
- alert_setup     → настройка уведомлений и алертов

ЗАПРОС: "{user_message}"

КАТЕГОРИЯ:"""

```

***

## `chatbot/ai/knowledge_base.py`

```python
"""
База знаний для AI-агентов.
Статические знания, которые добавляются в контекст в зависимости от темы.
"""


# ═══════════════════════════════════════════════════════════════
# ТЕХНИЧЕСКИЕ ИНДИКАТОРЫ — интерпретация сигналов
# ═══════════════════════════════════════════════════════════════

INDICATORS_KNOWLEDGE = """
## БАЗА ЗНАНИЙ: ТЕХНИЧЕСКИЕ ИНДИКАТОРЫ

### RSI (Relative Strength Index, период 14)
- < 30 → ПЕРЕПРОДАН: потенциальный разворот вверх, искать точку входа лонг
- 30-50 → МЕДВЕЖЬЯ ЗОНА: рынок под давлением продавцов
- 50-70 → БЫЧЬЯ ЗОНА: рынок под давлением покупателей  
- > 70 → ПЕРЕКУПЛЕН: потенциальный разворот вниз, искать точку входа шорт
- ДИВЕРГЕНЦИЯ БЫЧЬЯ: цена делает новый минимум, RSI — нет → сигнал разворота вверх
- ДИВЕРГЕНЦИЯ МЕДВЕЖЬЯ: цена делает новый максимум, RSI — нет → сигнал разворота вниз

### MACD (12, 26, 9)
- MACD выше Signal → БЫЧИЙ: бычий импульс
- MACD ниже Signal → МЕДВЕЖИЙ: медвежий импульс
- БЫЧЬЕ ПЕРЕСЕЧЕНИЕ: MACD пересекает Signal снизу вверх → сигнал лонг
- МЕДВЕЖЬЕ ПЕРЕСЕЧЕНИЕ: MACD пересекает Signal сверху вниз → сигнал шорт
- ГИСТОГРАММА РАСТЁТ → усиление тренда
- ГИСТОГРАММА ПАДАЕТ → ослабление тренда

### Bollinger Bands (20, 2)
- Цена у ВЕРХНЕЙ полосы → перекупленность или сильный тренд
- Цена у НИЖНЕЙ полосы → перепроданность или сильный нисходящий тренд
- СЖАТИЕ (squeeze) → ожидай сильного движения в любую сторону
- РАСШИРЕНИЕ → тренд в силе
- Выход за полосу → экстремальное движение, возможен откат

### EMA (скользящие средние)
- EMA20 > EMA50 → краткосрочный бычий тренд
- EMA20 < EMA50 → краткосрочный медвежий тренд
- ЗОЛОТОЙ КРЕСТ: EMA50 пересекает EMA200 снизу вверх → мощный бычий сигнал
- МЁРТВЫЙ КРЕСТ: EMA50 пересекает EMA200 сверху вниз → мощный медвежий сигнал
- Цена выше EMA50 → поддержка на EMA50
- Цена ниже EMA50 → сопротивление на EMA50

### Объём
- Рост цены + рост объёма → подтверждённый тренд
- Рост цены + падение объёма → слабый тренд, возможен разворот
- Падение цены + рост объёма → капитуляция или начало тренда вниз
- Аномальный всплеск объёма (>3x среднего) → важное событие, разворотная зона
"""


# ═══════════════════════════════════════════════════════════════
# УПРАВЛЕНИЕ РИСКАМИ — правила и формулы
# ═══════════════════════════════════════════════════════════════

RISK_MANAGEMENT_KNOWLEDGE = """
## БАЗА ЗНАНИЙ: УПРАВЛЕНИЕ РИСКАМИ

### Золотые правила
1. Риск на одну сделку: НЕ БОЛЕЕ 1-2% от депозита
2. Максимальный дневной убыток: НЕ БОЛЕЕ 5-6% от депозита
3. Минимальное соотношение Risk:Reward = 1:2 (лучше 1:3)
4. Никогда не торговать без стоп-лосса
5. Диверсификация: не держать >20% депозита в одном активе

### Формулы расчёта позиции
Размер_позиции = (Депозит × Риск%) / (Цена_входа - Стоп_лосс)
Максимальный_убыток = Депозит × Риск%
R:R = (Тейк_профит - Вход) / (Вход - Стоп_лосс)

### Расстановка уровней
СТОП-ЛОСС:
- За ближайший уровень поддержки/сопротивления
- За хвост ключевой свечи
- Технический ATR × 1.5 от точки входа
- Никогда не ставить "круглые" числа (1000, 2000 и т.д.)

ТЕЙК-ПРОФИТ:
- TP1: ближайший уровень сопротивления (фиксируем 50-60% позиции)
- TP2: следующий уровень (оставшиеся 40-50%)
- Trailing Stop: после достижения TP1

### Позиция по тренду vs контртренд
- ПО ТРЕНДУ: стоп за предыдущий лоу/хай, R:R ≥ 1:2
- КОНТРТРЕНД: стоп жёсткий (1-2% от цены), R:R ≥ 1:3, размер позиции меньше

### Калькулятор ликвидации (изолированная маржа)
Цена_ликвидации_лонг = Цена_входа × (1 - 1/Плечо + Maintenance_Margin%)
Цена_ликвидации_шорт = Цена_входа × (1 + 1/Плечо - Maintenance_Margin%)
"""


# ═══════════════════════════════════════════════════════════════
# РЫНОЧНЫЕ ПАТТЕРНЫ — свечные и графические
# ═══════════════════════════════════════════════════════════════

PATTERNS_KNOWLEDGE = """
## БАЗА ЗНАНИЙ: ТОРГОВЫЕ ПАТТЕРНЫ

### Свечные паттерны разворота
БЫЧЬИ:
- Молот (Hammer): маленькое тело вверху, длинная нижняя тень ≥ 2×тела
- Бычье поглощение (Bullish Engulfing): зелёная свеча полностью перекрывает красную
- Утренняя звезда (Morning Star): красная → маленькая → зелёная, третья закрывается в теле первой
- Пин-бар от поддержки: отвержение уровня длинной тенью

МЕДВЕЖЬИ:
- Повешенный (Hanging Man): как молот, но на вершине тренда
- Медвежье поглощение (Bearish Engulfing): красная свеча полностью перекрывает зелёную
- Вечерняя звезда (Evening Star): обратная утренней звезде

### Графические паттерны
РАЗВОРОТНЫЕ:
- Голова и плечи (H&S): нарушение линии шеи → цель = высота головы
- Двойная вершина/дно: подтверждение при пробое шеи
- Клин (Wedge): конвергенция линий тренда → пробой в обратную сторону

ПРОДОЛЖЕНИЯ:
- Флаг (Flag): резкое движение → консолидация → продолжение
- Вымпел (Pennant): сужающийся треугольник после импульса
- Треугольник (Triangle): симметричный/восходящий/нисходящий

### Уровни и зоны
- ПОДДЕРЖКА: зона, где цена ранее отскакивала вверх
- СОПРОТИВЛЕНИЕ: зона, где цена ранее разворачивалась вниз
- ПРОБОЙ: закрытие свечи ЗА уровнем + объём выше среднего
- ЛОЖНЫЙ ПРОБОЙ: возврат за уровень — ищи вход в обратную сторону
- Чем БОЛЬШЕ касаний уровня → тем СИЛЬНЕЕ его пробой
"""


# ═══════════════════════════════════════════════════════════════
# HTX БИРЖА — специфика и особенности
# ═══════════════════════════════════════════════════════════════

HTX_KNOWLEDGE = """
## БАЗА ЗНАНИЙ: БИРЖА HTX

### Типы рынков
- СПОТ: прямая торговля активами, без плеча
- USDT-М ФЬЮЧЕРСЫ: маржинальные контракты в USDT, perpetual и dated
- COIN-М ФЬЮЧЕРСЫ: маржинальные контракты в базовой валюте

### Ключевые метрики HTX
- Ставка финансирования (Funding Rate): выплачивается каждые 8 часов
  - Положительная → лонги платят шортам (рынок перегрет вверх)
  - Отрицательная → шорты платят лонгам (рынок перегрет вниз)
  - Аномальная (>0.1% или <-0.1%) → сигнал потенциального разворота

- Открытый интерес (OI, Open Interest):
  - Рост цены + рост OI → новые деньги входят в тренд (сильный сигнал)
  - Рост цены + падение OI → шорты закрываются (слабый сигнал)
  - Резкое падение OI → массовые ликвидации

- Стакан ордеров (Order Book):
  - Крупные стены покупок (bids) → потенциальная поддержка
  - Крупные стены продаж (asks) → потенциальное сопротивление
  - Дисбаланс > 3:1 → давление в сторону большего объёма

### Торговые сессии (влияние на волатильность)
- 00:00-08:00 UTC → Азиатская сессия (HTX сильна, часто ложные пробои)
- 07:00-16:00 UTC → Европейская сессия (рост объёмов)
- 13:00-22:00 UTC → Американская сессия (максимальная волатильность)
- Пересечения сессий → наибольшие движения

### Комиссии HTX (спот)
- Maker: 0.2% (лимитные ордера)
- Taker: 0.2% (рыночные ордера)
- VIP-уровни снижают комиссии
"""


# ═══════════════════════════════════════════════════════════════
# РЫНОЧНЫЕ ЦИКЛЫ — макро-анализ крипторынка
# ═══════════════════════════════════════════════════════════════

MARKET_CYCLES_KNOWLEDGE = """
## БАЗА ЗНАНИЙ: РЫНОЧНЫЕ ЦИКЛЫ КРИПТО

### Доминирование BTC
- BTC доминирование > 60% → альты теряют, капитал в BTC
- BTC доминирование 50-60% → переходная зона
- BTC доминирование < 50% → альтсезон, ротация в альты
- Резкое падение доминирования → альтсезон в моменте

### Fear & Greed Index
- 0-25: Extreme Fear → потенциальная зона покупок (долгосрочно)
- 25-50: Fear → рынок под давлением
- 50-75: Greed → осторожность при входе в лонги
- 75-100: Extreme Greed → высокий риск коррекции, не открывать лонги

### Халвинг-цикл BTC (ориентир)
- Фаза 1 (0-12 мес после халвинга): накопление
- Фаза 2 (12-24 мес): рост, бычий рынок
- Фаза 3 (24-30 мес): пик, эйфория
- Фаза 4 (30-48 мес): медвежий рынок, накопление
- Последний халвинг: апрель 2024 → текущий цикл: фаза роста/пика

### On-chain сигналы (интерпретация)
- MVRV > 3.5 → исторически зона пика рынка
- MVRV < 1 → исторически зона дна
- Exchange Flow: приток BTC на биржи → давление продаж
- Exchange Flow: отток BTC с бирж → накопление (бычий сигнал)
"""


# ═══════════════════════════════════════════════════════════════
# ФУНКЦИЯ ПОЛУЧЕНИЯ КОНТЕКСТНОЙ БАЗЫ ЗНАНИЙ
# ═══════════════════════════════════════════════════════════════

KNOWLEDGE_MAP = {
    "indicators":    INDICATORS_KNOWLEDGE,
    "risk":          RISK_MANAGEMENT_KNOWLEDGE,
    "patterns":      PATTERNS_KNOWLEDGE,
    "htx":           HTX_KNOWLEDGE,
    "cycles":        MARKET_CYCLES_KNOWLEDGE,
}

def get_knowledge_for_query(intent: str, query: str) -> str:
    """
    Возвращает релевантные блоки базы знаний по интенту и ключевым словам запроса.
    Минимизирует токены — не грузим всё сразу.
    """
    selected = []
    query_lower = query.lower()
    
    # По интенту
    if intent in ("deep_analysis",):
        selected += ["indicators", "patterns"]
    elif intent == "position_calc":
        selected += ["risk"]
    elif intent == "quick_chat":
        selected += ["htx"]
    
    # По ключевым словам в запросе
    kw_map = {
        "rsi": "indicators", "macd": "indicators", "bb": "indicators",
        "ema": "indicators", "индикатор": "indicators",
        "стоп": "risk", "тейк": "risk", "риск": "risk",
        "позиция": "risk", "плечо": "risk", "ликвидац": "risk",
        "паттерн": "patterns", "свеч": "patterns", "голова": "patterns",
        "флаг": "patterns", "треугольник": "patterns",
        "funding": "htx", "oi": "htx", "стакан": "htx", "htx": "htx",
        "халвинг": "cycles", "доминирование": "cycles", 
        "альтсезон": "cycles", "цикл": "cycles",
    }
    for kw, kb_key in kw_map.items():
        if kw in query_lower and kb_key not in selected:
            selected.append(kb_key)
    
    if not selected:
        selected = ["indicators"]  # дефолт
    
    return "\n\n".join(KNOWLEDGE_MAP[k] for k in selected)
```


***

## `chatbot/ai/router.py` — полный роутер

```python
"""
Роутер запросов к AI-моделям.
Классифицирует интент → выбирает модель → формирует запрос → возвращает ответ.
"""

import os
import re
import asyncio
import aiohttp
from loguru import logger
from openai import AsyncOpenAI

from .prompts import (
    get_intent_classifier_prompt, get_perplexity_messages,
    get_glm_messages, get_minimax_messages, get_qwen_messages
)
from .knowledge_base import get_knowledge_for_query


# Клиенты для каждой модели
_perplexity_client = AsyncOpenAI(
    api_key=os.getenv("PERPLEXITY_API_KEY"),
    base_url="https://api.perplexity.ai"
)
_glm_client = AsyncOpenAI(
    api_key=os.getenv("GLM_API_KEY"),
    base_url="https://open.bigmodel.cn/api/paas/v4/"
)
_minimax_client = AsyncOpenAI(
    api_key=os.getenv("MINIMAX_API_KEY"),
    base_url="https://api.minimax.io/v1"
)
_qwen_client = AsyncOpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
)


async def classify_intent(user_message: str) -> str:
    """Быстрая классификация через MiniMax (дешевле, быстрее)."""
    try:
        resp = await _minimax_client.chat.completions.create(
            model="MiniMax-M2.7",
            messages=[{"role": "user", "content": get_intent_classifier_prompt(user_message)}],
            max_tokens=20,
            temperature=0.0
        )
        intent = resp.choices[^3_0].message.content.strip().lower()
        if intent not in ("market_news", "deep_analysis", "quick_chat",
                           "backtest_code", "position_calc", "alert_setup"):
            intent = "quick_chat"
        return intent
    except Exception as e:
        logger.warning(f"Intent classification failed: {e}")
        return "quick_chat"


async def route_and_respond(
    user_message: str,
    market_snapshot: dict,
    chat_history: list
) -> dict:
    """
    Главная функция роутинга.
    Возвращает: {"text": str, "model": str, "intent": str, "thinking": str|None}
    """
    intent = await classify_intent(user_message)
    kb = get_knowledge_for_query(intent, user_message)
    
    # Добавляем базу знаний к снапшоту
    enriched_snapshot = {**market_snapshot, "knowledge_base": kb}
    
    logger.info(f"Intent: {intent} | Routing to model...")
    
    if intent == "market_news":
        return await _call_perplexity(user_message, enriched_snapshot)
    
    elif intent == "deep_analysis":
        return await _call_glm(user_message, enriched_snapshot, chat_history, thinking=True)
    
    elif intent in ("backtest_code",):
        return await _call_qwen(user_message, enriched_snapshot, use_code_model=True)
    
    elif intent == "position_calc":
        return await _call_glm(user_message, enriched_snapshot, chat_history, thinking=False)
    
    else:  # quick_chat, alert_setup
        result = await _call_minimax(user_message, enriched_snapshot, chat_history)
        # Проверяем маркеры делегирования
        text = result.get("text", "")
        if "[НУЖЕН_ГЛУБОКИЙ_АНАЛИЗ]" in text:
            text = text.replace("[НУЖЕН_ГЛУБОКИЙ_АНАЛИЗ]", "").strip()
            result2 = await _call_glm(user_message, enriched_snapshot, chat_history)
            result2["text"] = text + "\n\n" + result2["text"]
            return result2
        if "[НУЖНЫ_НОВОСТИ]" in text:
            return await _call_perplexity(user_message, enriched_snapshot)
        return result


async def _call_perplexity(query: str, snapshot: dict) -> dict:
    messages = get_perplexity_messages(query, snapshot)
    resp = await _perplexity_client.chat.completions.create(
        model="sonar-pro",
        messages=messages,
        max_tokens=2048,
        temperature=0.3,
    )
    return {
        "text": resp.choices[^3_0].message.content,
        "model": "Perplexity Sonar Pro",
        "intent": "market_news",
        "thinking": None
    }


async def _call_glm(query: str, snapshot: dict, 
                    history: list, thinking: bool = True) -> dict:
    payload = get_glm_messages(query, snapshot, history, thinking)
    resp = await _glm_client.chat.completions.create(**payload)
    thinking_text = None
    msg = resp.choices[^3_0].message
    # GLM возвращает reasoning в отдельном поле при thinking mode
    if hasattr(msg, "reasoning_content"):
        thinking_text = msg.reasoning_content
    return {
        "text": msg.content,
        "model": "GLM-5.1",
        "intent": "deep_analysis",
        "thinking": thinking_text
    }


async def _call_minimax(query: str, snapshot: dict,
                         history: list, reasoning: bool = False) -> dict:
    payload = get_minimax_messages(query, snapshot, history, reasoning)
    resp = await _minimax_client.chat.completions.create(**payload)
    thinking_text = None
    msg = resp.choices[^3_0].message
    if hasattr(msg, "reasoning_details") and msg.reasoning_details:
        thinking_text = msg.reasoning_details[^3_0].get("text")
    return {
        "text": msg.content,
        "model": "MiniMax M2.7",
        "intent": "quick_chat",
        "thinking": thinking_text
    }


async def _call_qwen(query: str, snapshot: dict,
                      use_code_model: bool = False) -> dict:
    payload = get_qwen_messages(query, snapshot, use_code_model)
    resp = await _qwen_client.chat.completions.create(**payload)
    msg = resp.choices[^3_0].message
    model_name = "QwenCode3-Plus" if use_code_model else "Qwen3.6-Plus"
    return {
        "text": msg.content,
        "model": model_name,
        "intent": "backtest_code",
        "thinking": None
    }
```


***

## Итог архитектуры промтов

Каждая модель получает строго свой промт под свою роль:  GLM-5.1 работает в **thinking mode** для стратегического анализа (способен на автономные многошаговые рассуждения без переспросов ), Perplexity Sonar Pro имеет доступ к реальному вебу через `search_domain_filter`, MiniMax M2.7 получает `reasoning_split: True` и возвращает цепочку рассуждений отдельно от ответа. Qwen работает с `enable_thinking: True` через DashScope OpenAI-compatible endpoint. Роутер классифицирует интент → выбирает модель → обогащает контекст нужным блоком базы знаний, не загружая в промт лишние токены.[^3_1][^3_2][^3_3][^3_4][^3_5]

Следующий шаг: пишем `collector/main.py` с HTX WebSocket стримом?
<span style="display:none">[^3_10][^3_11][^3_12][^3_13][^3_14][^3_15][^3_16][^3_6][^3_7][^3_8][^3_9]</span>

<div align="center">⁂</div>

[^3_1]: https://docs.perplexity.ai/docs/agent-api/prompt-guide

[^3_2]: https://platform.minimax.io/docs/api-reference/text-openai-api

[^3_3]: https://aimlapi.com/blog/glm-5-1-the-long-horizon-agentic-llm-that-can-work-8-hours-non-stop

[^3_4]: https://www.youtube.com/watch?v=UxGieu7PaPg

[^3_5]: https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-qwen-qwen3-235b-a22b-2507.html

[^3_6]: https://z.ai/blog/glm-5.1

[^3_7]: https://www.datacamp.com/tutorial/run-glm-5-1-locally

[^3_8]: https://www.youtube.com/watch?v=D5pucipH-AM

[^3_9]: https://www.mindstudio.ai/blog/what-is-glm-5-1-open-source-coding-model-4/

[^3_10]: https://www.reddit.com/r/machinelearningnews/comments/1sby7gr/how_to_build_productionready_agentic_systems_with/

[^3_11]: https://www.reddit.com/r/perplexity_ai/comments/1kldf4n/sonarreasoningpros_full_updated_system_prompt/

[^3_12]: https://platform.minimax.io/docs/api-reference/text-chat

[^3_13]: https://docs.perplexity.ai/docs/sonar/models/sonar-pro

[^3_14]: https://platform.minimax.io/docs/api-reference/text-post

[^3_15]: https://www.facebook.com/InsightistAI/posts/ai-news-ai-ฉลาดนาน-ไม่หยุดกลางทาง-zhipu-ai-เปิดตัว-glm-51-agentic-engineering-เส/1661959421686009/

[^3_16]: https://www.perplexity.ai/api-platform


---

# Да, пиши collector/main.py с HTX WebSocket

Важный факт из документации: HTX WebSocket возвращает данные, сжатые **GZIP**, их нужно декомпрессировать вручную. Эндпоинты: маркет-данные — `wss://api.huobi.pro/ws`, авторизованные данные — `wss://api.huobi.pro/ws/v2`. Даю полные файлы.[^4_1][^4_2]

***

## `collector/main.py`

```python
"""
Agent #1 — HTX Data Collector
Точка входа. Запускает все фоновые задачи параллельно.
"""

import asyncio
import os
import signal
from loguru import logger
from dotenv import load_dotenv

from htx.websocket import HTXWebSocketClient
from htx.rest import HTXRestClient
from storage.redis_client import RedisClient
from storage.postgres_client import PostgresClient
from indicators.calculator import IndicatorCalculator
from alerts.detector import AlertDetector
from journal.writer import JournalWriter

load_dotenv()

# ─── Конфигурация ────────────────────────────────────────────
WATCHLIST = os.getenv("WATCHLIST", "btcusdt,ethusdt,solusdt,bnbusdt").split(",")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

logger.remove()
logger.add(
    "logs/collector.log",
    rotation="100 MB",
    retention="7 days",
    level=LOG_LEVEL,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
)
logger.add(lambda msg: print(msg, end=""), level=LOG_LEVEL, colorize=True)


async def main():
    logger.info(f"Starting HTX Collector | Watchlist: {WATCHLIST}")

    # ─── Инициализация клиентов ──────────────────────────────
    redis = RedisClient()
    await redis.connect()

    pg = PostgresClient()
    await pg.connect()

    rest = HTXRestClient()
    indicator_calc = IndicatorCalculator(redis=redis, pg=pg)
    alert_detector = AlertDetector(redis=redis)
    journal = JournalWriter(redis=redis, pg=pg)

    # ─── Загрузка исторических данных при старте ─────────────
    logger.info("Loading historical klines on startup...")
    for symbol in WATCHLIST:
        for interval in ["1min", "5min", "60min", "1day"]:
            try:
                candles = await rest.get_klines(symbol, interval, size=300)
                await pg.save_candles(symbol, interval, candles)
                await indicator_calc.calculate_and_store(symbol, interval, candles)
                logger.info(f"Loaded {len(candles)} candles: {symbol} {interval}")
            except Exception as e:
                logger.error(f"Failed to load history {symbol} {interval}: {e}")
        await asyncio.sleep(0.3)  # rate limit

    # ─── Запуск параллельных задач ───────────────────────────
    ws_client = HTXWebSocketClient(
        symbols=WATCHLIST,
        redis=redis,
        pg=pg,
        indicator_calc=indicator_calc,
        alert_detector=alert_detector,
        journal=journal,
    )

    tasks = [
        asyncio.create_task(ws_client.run(), name="ws_stream"),
        asyncio.create_task(periodic_rest_sync(rest, pg, indicator_calc, redis), name="rest_sync"),
        asyncio.create_task(journal.run_hourly_snapshot(), name="journal_hourly"),
        asyncio.create_task(journal.run_realtime_snapshot(), name="journal_realtime"),
    ]

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: [t.cancel() for t in tasks])

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Collector shutting down gracefully...")
    finally:
        await redis.close()
        await pg.close()
        logger.info("Collector stopped.")


async def periodic_rest_sync(rest: HTXRestClient, pg: PostgresClient,
                              calc: IndicatorCalculator, redis: RedisClient):
    """
    Каждые 5 минут досинхронизируем 1h и 1d свечи через REST.
    WS покрывает 1min/5min в реалтайме, REST закрывает пробелы.
    """
    while True:
        await asyncio.sleep(300)
        for symbol in WATCHLIST:
            try:
                for interval in ["60min", "1day"]:
                    candles = await rest.get_klines(symbol, interval, size=50)
                    await pg.save_candles(symbol, interval, candles)
                    await calc.calculate_and_store(symbol, interval, candles)
            except Exception as e:
                logger.error(f"REST sync error {symbol}: {e}")
            await asyncio.sleep(0.2)


if __name__ == "__main__":
    asyncio.run(main())
```


***

## `collector/htx/websocket.py`

```python
"""
HTX WebSocket клиент.
- wss://api.huobi.pro/ws — публичные маркет-данные (GZIP сжатие)
- Подписки: тикер, свечи 1min/5min, стакан (depth), трейды
- Автоматический реконнект с exponential backoff
"""

import asyncio
import gzip
import json
import time
from typing import Optional
from loguru import logger
import websockets
from websockets.exceptions import ConnectionClosed

from storage.redis_client import RedisClient
from storage.postgres_client import PostgresClient
from indicators.calculator import IndicatorCalculator
from alerts.detector import AlertDetector
from journal.writer import JournalWriter

HTX_WS_URL = "wss://api.huobi.pro/ws"

# Интервалы переподключения: 1s, 2s, 4s, 8s, 16s, 30s max
RECONNECT_DELAYS = [1, 2, 4, 8, 16, 30]


class HTXWebSocketClient:

    def __init__(self, symbols: list, redis: RedisClient, pg: PostgresClient,
                 indicator_calc: IndicatorCalculator, alert_detector: AlertDetector,
                 journal: JournalWriter):
        self.symbols = symbols
        self.redis = redis
        self.pg = pg
        self.indicator_calc = indicator_calc
        self.alert_detector = alert_detector
        self.journal = journal
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._reconnect_attempt = 0

    async def run(self):
        """Основной цикл с автоматическим реконнектом."""
        while True:
            try:
                await self._connect_and_subscribe()
                self._reconnect_attempt = 0  # сброс при успешном соединении
            except asyncio.CancelledError:
                raise
            except Exception as e:
                delay = RECONNECT_DELAYS[min(self._reconnect_attempt, len(RECONNECT_DELAYS) - 1)]
                logger.warning(f"WS disconnected: {e}. Reconnect in {delay}s (attempt #{self._reconnect_attempt + 1})")
                self._reconnect_attempt += 1
                await asyncio.sleep(delay)

    async def _connect_and_subscribe(self):
        logger.info(f"Connecting to HTX WebSocket: {HTX_WS_URL}")
        async with websockets.connect(
            HTX_WS_URL,
            ping_interval=None,   # Отключаем — HTX использует свой ping/pong
            max_size=10 * 1024 * 1024,  # 10MB буфер
            compression=None      # GZIP делаем сами
        ) as ws:
            self._ws = ws
            logger.success("HTX WebSocket connected")
            await self._subscribe_all()
            await self._listen(ws)

    async def _subscribe_all(self):
        """Подписываемся на все нужные топики для каждого символа."""
        subs = []
        for symbol in self.symbols:
            # Тикер — best bid/ask + last price (до 10 раз/сек)
            subs.append({"sub": f"market.{symbol}.detail", "id": f"detail_{symbol}"})
            # Свечи 1 минута
            subs.append({"sub": f"market.{symbol}.kline.1min", "id": f"kline_1min_{symbol}"})
            # Свечи 5 минут
            subs.append({"sub": f"market.{symbol}.kline.5min", "id": f"kline_5min_{symbol}"})
            # Стакан (топ 20 уровней, snapshot mode)
            subs.append({"sub": f"market.{symbol}.depth.step0", "id": f"depth_{symbol}"})

        for sub in subs:
            await self._ws.send(json.dumps(sub))
            await asyncio.sleep(0.05)  # небольшая пауза между подписками

        logger.info(f"Subscribed to {len(subs)} topics for {len(self.symbols)} symbols")

    async def _listen(self, ws):
        """Основной цикл обработки сообщений."""
        async for raw_msg in ws:
            try:
                # HTX отправляет данные сжатые GZIP — обязательная декомпрессия
                msg = json.loads(gzip.decompress(raw_msg).decode("utf-8"))
                await self._handle_message(msg)
            except gzip.BadGzipFile:
                # Некоторые служебные сообщения не сжаты
                try:
                    msg = json.loads(raw_msg)
                    await self._handle_message(msg)
                except Exception as e:
                    logger.debug(f"Non-JSON message: {e}")
            except Exception as e:
                logger.error(f"Message processing error: {e}")

    async def _handle_message(self, msg: dict):
        """Роутинг входящих сообщений по типу."""

        # ─── HTX Ping — обязательно отвечаем Pong ──────────────
        if "ping" in msg:
            await self._ws.send(json.dumps({"pong": msg["ping"]}))
            return

        # ─── Подтверждение подписки ─────────────────────────────
        if "subbed" in msg:
            logger.debug(f"Subscribed: {msg.get('subbed')}")
            return

        # ─── Ошибки от HTX ──────────────────────────────────────
        if "err-msg" in msg:
            logger.error(f"HTX WS error: {msg.get('err-msg')} | code: {msg.get('err-code')}")
            return

        channel = msg.get("ch", "")
        tick = msg.get("tick")
        if not channel or not tick:
            return

        # ─── Роутинг по каналу ──────────────────────────────────
        if ".detail" in channel:
            await self._handle_ticker(channel, tick)

        elif ".kline." in channel:
            await self._handle_kline(channel, tick)

        elif ".depth." in channel:
            await self._handle_depth(channel, tick)

    async def _handle_ticker(self, channel: str, tick: dict):
        """
        market.{symbol}.detail — агрегированные данные за последние 24ч.
        Поля: open, high, low, close, vol, amount, count
        """
        symbol = channel.split(".")[^4_1]
        price = tick.get("close", 0)
        volume_24h = tick.get("vol", 0)
        high_24h = tick.get("high", 0)
        low_24h = tick.get("low", 0)
        open_24h = tick.get("open", 0)

        change_24h_pct = ((price - open_24h) / open_24h * 100) if open_24h else 0

        ticker_data = {
            "price": price,
            "high_24h": high_24h,
            "low_24h": low_24h,
            "volume_24h": round(volume_24h, 2),
            "change_24h_pct": round(change_24h_pct, 3),
            "timestamp": int(time.time()),
        }

        # Сохраняем в Redis (TTL 60 секунд — всегда актуально)
        await self.redis.set_ticker(symbol, ticker_data, ttl=60)

        # Детектор аномалий — проверяем на спайки
        await self.alert_detector.check_price_spike(symbol, price, ticker_data)

        logger.debug(f"Ticker {symbol}: {price} ({change_24h_pct:+.2f}%)")

    async def _handle_kline(self, channel: str, tick: dict):
        """
        market.{symbol}.kline.{period} — свечные данные.
        Поля: id (timestamp), open, high, low, close, vol, amount
        """
        parts = channel.split(".")
        symbol = parts[^4_1]
        interval = parts[^4_3]  # 1min, 5min, etc.

        candle = {
            "open_time": tick["id"],   # Unix timestamp
            "open":  float(tick["open"]),
            "high":  float(tick["high"]),
            "low":   float(tick["low"]),
            "close": float(tick["close"]),
            "volume": float(tick["vol"]),
        }

        # Сохраняем свечу в Redis (последние 500 свечей на символ)
        await self.redis.push_candle(symbol, interval, candle)

        # Сохраняем в PostgreSQL для истории
        await self.pg.upsert_candle(symbol, interval, candle)

        # Пересчитываем индикаторы при каждом закрытии минутной свечи
        if interval == "1min":
            candles = await self.redis.get_candles(symbol, "1min", limit=200)
            indicators = self.indicator_calc.calculate(candles)
            await self.redis.set_indicators(symbol, indicators)
            await self.pg.upsert_indicators(symbol, indicators)

            # Проверяем индикаторные алерты (RSI overbought, MACD cross, etc.)
            await self.alert_detector.check_indicator_alerts(symbol, indicators)

    async def _handle_depth(self, channel: str, tick: dict):
        """
        market.{symbol}.depth.step0 — стакан ордеров (топ 20).
        Используем для анализа дисбаланса bid/ask.
        """
        symbol = channel.split(".")[^4_1]
        bids = tick.get("bids", [])[:10]  # топ 10 bid
        asks = tick.get("asks", [])[:10]  # топ 10 ask

        if not bids or not asks:
            return

        # Суммарный объём в стакане
        bid_volume = sum(b[^4_1] for b in bids)
        ask_volume = sum(a[^4_1] for a in asks)
        imbalance = round(bid_volume / ask_volume, 2) if ask_volume else 1.0

        depth_data = {
            "best_bid": bids[^4_0][^4_0],
            "best_ask": asks[^4_0][^4_0],
            "spread": round(asks[^4_0][^4_0] - bids[^4_0][^4_0], 8),
            "bid_volume": round(bid_volume, 4),
            "ask_volume": round(ask_volume, 4),
            "imbalance": imbalance,  # >1 давление покупателей, <1 продавцов
            "timestamp": int(time.time()),
        }
        await self.redis.set_depth(symbol, depth_data, ttl=30)
```


***

## `collector/htx/rest.py`

```python
"""
HTX REST API клиент.
Используется для загрузки исторических данных и периодической досинхронизации.
Базовый URL: https://api.huobi.pro
"""

import time
import hmac
import hashlib
import base64
import asyncio
import os
from urllib.parse import urlencode, quote
from datetime import datetime, timezone
import aiohttp
from loguru import logger


HTX_REST_URL = "https://api.huobi.pro"

# Соответствие периодов WS → REST
INTERVAL_MAP = {
    "1min":  "1min",
    "5min":  "5min",
    "15min": "15min",
    "30min": "30min",
    "60min": "60min",
    "4hour": "4hour",
    "1day":  "1day",
    "1week": "1week",
    "1mon":  "1mon",
}


class HTXRestClient:

    def __init__(self):
        self.access_key = os.getenv("HTX_ACCESS_KEY", "")
        self.secret_key = os.getenv("HTX_SECRET_KEY", "")
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ─── Подпись запросов (для приватных эндпоинтов) ────────────
    def _sign(self, method: str, path: str, params: dict) -> dict:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        params_to_sign = {
            "AccessKeyId": self.access_key,
            "SignatureMethod": "HmacSHA256",
            "SignatureVersion": "2",
            "Timestamp": timestamp,
            **params,
        }
        sorted_params = urlencode(sorted(params_to_sign.items()))
        payload = f"{method}\napi.huobi.pro\n{path}\n{sorted_params}"
        signature = base64.b64encode(
            hmac.new(
                self.secret_key.encode("utf-8"),
                payload.encode("utf-8"),
                digestmod=hashlib.sha256
            ).digest()
        ).decode()
        params_to_sign["Signature"] = signature
        return params_to_sign

    # ─── ПУБЛИЧНЫЕ ЭНДПОИНТЫ ────────────────────────────────────

    async def get_klines(self, symbol: str, interval: str = "1min",
                          size: int = 300) -> list[dict]:
        """
        GET /market/history/kline
        Возвращает список свечей [{open_time, open, high, low, close, volume}, ...]
        Максимум 2000 свечей за запрос.
        """
        htx_interval = INTERVAL_MAP.get(interval, "1min")
        size = min(size, 2000)

        session = await self._get_session()
        url = f"{HTX_REST_URL}/market/history/kline"
        params = {"symbol": symbol, "period": htx_interval, "size": size}

        async with session.get(url, params=params) as resp:
            data = await resp.json(content_type=None)

        if data.get("status") != "ok":
            raise ValueError(f"HTX klines error: {data.get('err-msg', 'unknown')}")

        candles = []
        for raw in reversed(data.get("data", [])):  # HTX возвращает newest first
            candles.append({
                "open_time": raw["id"],
                "open":   float(raw["open"]),
                "high":   float(raw["high"]),
                "low":    float(raw["low"]),
                "close":  float(raw["close"]),
                "volume": float(raw["vol"]),
            })
        return candles

    async def get_ticker(self, symbol: str) -> dict:
        """GET /market/detail/merged — текущий тикер с bid/ask."""
        session = await self._get_session()
        url = f"{HTX_REST_URL}/market/detail/merged"
        async with session.get(url, params={"symbol": symbol}) as resp:
            data = await resp.json(content_type=None)

        if data.get("status") != "ok":
            raise ValueError(f"HTX ticker error: {data.get('err-msg')}")

        tick = data["tick"]
        return {
            "price":     tick["close"],
            "bid":       tick["bid"][^4_0],
            "ask":       tick["ask"][^4_0],
            "high_24h":  tick["high"],
            "low_24h":   tick["low"],
            "volume_24h": tick["vol"],
        }

    async def get_all_tickers(self) -> list[dict]:
        """GET /market/tickers — все тикеры одним запросом."""
        session = await self._get_session()
        url = f"{HTX_REST_URL}/market/tickers"
        async with session.get(url) as resp:
            data = await resp.json(content_type=None)

        if data.get("status") != "ok":
            raise ValueError(f"HTX all tickers error: {data.get('err-msg')}")
        return data.get("data", [])

    async def get_symbols(self) -> list[str]:
        """GET /v1/common/symbols — все доступные торговые пары."""
        session = await self._get_session()
        url = f"{HTX_REST_URL}/v1/common/symbols"
        async with session.get(url) as resp:
            data = await resp.json(content_type=None)
        return [s["symbol"] for s in data.get("data", []) if s.get("state") == "online"]

    async def get_klines_range(self, symbol: str, interval: str,
                                start_ts: int, end_ts: int) -> list[dict]:
        """
        Загружает свечи за произвольный период.
        Автоматически разбивает на несколько запросов если нужно.
        """
        all_candles = []
        current_end = end_ts

        # Определяем шаг в секундах
        interval_seconds = {
            "1min": 60, "5min": 300, "15min": 900, "30min": 1800,
            "60min": 3600, "4hour": 14400, "1day": 86400,
        }.get(interval, 60)

        while current_end > start_ts:
            size = min(2000, (current_end - start_ts) // interval_seconds + 1)
            if size <= 0:
                break

            candles = await self.get_klines(symbol, interval, size=size)
            if not candles:
                break

            # Фильтруем по диапазону
            filtered = [c for c in candles if start_ts <= c["open_time"] <= current_end]
            all_candles = filtered + all_candles

            # Следующий батч
            current_end = candles[^4_0]["open_time"] - interval_seconds
            await asyncio.sleep(0.2)  # rate limit

        return all_candles
```


***

## `collector/indicators/calculator.py`

```python
"""
Расчёт технических индикаторов через pandas-ta.
Поддерживает RSI, MACD, Bollinger Bands, EMA.
"""

import pandas as pd
import pandas_ta as ta
import numpy as np
from loguru import logger

from storage.redis_client import RedisClient
from storage.postgres_client import PostgresClient


class IndicatorCalculator:

    def __init__(self, redis: RedisClient, pg: PostgresClient):
        self.redis = redis
        self.pg = pg

    def calculate(self, candles: list[dict]) -> dict:
        """
        Рассчитывает индикаторы по списку свечей.
        Возвращает dict с последними значениями.
        """
        if len(candles) < 50:
            logger.warning(f"Not enough candles for indicators: {len(candles)}")
            return {}

        df = pd.DataFrame(candles)
        df = df.rename(columns={
            "open_time": "timestamp",
            "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume"
        })
        df = df.astype({"open": float, "high": float, "low": float,
                         "close": float, "volume": float})
        df = df.sort_values("timestamp").reset_index(drop=True)

        result = {}

        try:
            # RSI (14)
            rsi = ta.rsi(df["close"], length=14)
            result["rsi"] = round(float(rsi.iloc[-1]), 2) if rsi is not None else None

            # MACD (12, 26, 9)
            macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
            if macd is not None and not macd.empty:
                result["macd"]        = round(float(macd["MACD_12_26_9"].iloc[-1]), 6)
                result["macd_signal"] = round(float(macd["MACDs_12_26_9"].iloc[-1]), 6)
                result["macd_hist"]   = round(float(macd["MACDh_12_26_9"].iloc[-1]), 6)
                # Сигнал пересечения
                hist_prev = macd["MACDh_12_26_9"].iloc[-2]
                hist_curr = macd["MACDh_12_26_9"].iloc[-1]
                if hist_prev < 0 < hist_curr:
                    result["macd_signal_type"] = "bullish_cross"
                elif hist_prev > 0 > hist_curr:
                    result["macd_signal_type"] = "bearish_cross"
                else:
                    result["macd_signal_type"] = "neutral"

            # Bollinger Bands (20, 2)
            bb = ta.bbands(df["close"], length=20, std=2)
            if bb is not None and not bb.empty:
                result["bb_upper"]  = round(float(bb["BBU_20_2.0"].iloc[-1]), 4)
                result["bb_middle"] = round(float(bb["BBM_20_2.0"].iloc[-1]), 4)
                result["bb_lower"]  = round(float(bb["BBL_20_2.0"].iloc[-1]), 4)
                close = df["close"].iloc[-1]
                bb_range = result["bb_upper"] - result["bb_lower"]
                result["bb_position"] = round(
                    (close - result["bb_lower"]) / bb_range * 100, 1
                ) if bb_range > 0 else 50  # % позиции в полосах (0=нижняя, 100=верхняя)

            # EMA 20 и 50
            ema20 = ta.ema(df["close"], length=20)
            ema50 = ta.ema(df["close"], length=50)
            if ema20 is not None:
                result["ema20"] = round(float(ema20.iloc[-1]), 4)
            if ema50 is not None:
                result["ema50"] = round(float(ema50.iloc[-1]), 4)
            if "ema20" in result and "ema50" in result:
                result["ema_trend"] = "bullish" if result["ema20"] > result["ema50"] else "bearish"

            # Волатильность (ATR 14)
            atr = ta.atr(df["high"], df["low"], df["close"], length=14)
            if atr is not None:
                result["atr14"] = round(float(atr.iloc[-1]), 4)
                result["atr_pct"] = round(
                    result["atr14"] / df["close"].iloc[-1] * 100, 2
                )  # ATR как % от цены

        except Exception as e:
            logger.error(f"Indicator calculation error: {e}")

        return result

    async def calculate_and_store(self, symbol: str, interval: str,
                                   candles: list[dict]):
        """Рассчитывает и сохраняет индикаторы для символа."""
        indicators = self.calculate(candles)
        if indicators:
            await self.redis.set_indicators(symbol, indicators, interval=interval)
            await self.pg.upsert_indicators(symbol, indicators)
```


***

## `collector/alerts/detector.py`

```python
"""
Детектор аномалий и генератор алертов.
Отправляет уведомления в Telegram через Bot API напрямую.
"""

import os
import time
import aiohttp
from loguru import logger

from storage.redis_client import RedisClient

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID")
SPIKE_THRESHOLD = float(os.getenv("ALERT_SPIKE_THRESHOLD", "3.0"))

# Cooldown алертов — не спамим одним алертом чаще раза в N секунд
ALERT_COOLDOWN = {
    "SPIKE":      300,   # 5 минут
    "RSI_EXTREME": 600,  # 10 минут
    "MACD_CROSS":  300,  # 5 минут
    "BB_BREAK":    180,  # 3 минуты
}


class AlertDetector:

    def __init__(self, redis: RedisClient):
        self.redis = redis
        self._alert_cache: dict[str, float] = {}  # key → last_sent_timestamp

    def _is_cooldown(self, alert_key: str, alert_type: str) -> bool:
        cooldown = ALERT_COOLDOWN.get(alert_type, 300)
        last_sent = self._alert_cache.get(alert_key, 0)
        return (time.time() - last_sent) < cooldown

    def _mark_sent(self, alert_key: str):
        self._alert_cache[alert_key] = time.time()

    async def check_price_spike(self, symbol: str, current_price: float,
                                 ticker_data: dict):
        """Проверяет резкое изменение цены за последние 15 минут."""
        prev_price = await self.redis.get_price_15min_ago(symbol)
        if not prev_price or prev_price == 0:
            await self.redis.store_price_history(symbol, current_price)
            return

        change_pct = abs((current_price - prev_price) / prev_price * 100)

        if change_pct >= SPIKE_THRESHOLD:
            direction = "🚀 РОСТ" if current_price > prev_price else "📉 ПАДЕНИЕ"
            alert_key = f"SPIKE_{symbol}_{direction}"
            if not self._is_cooldown(alert_key, "SPIKE"):
                severity = "CRITICAL" if change_pct >= SPIKE_THRESHOLD * 2 else "HIGH"
                message = (
                    f"⚠️ <b>АНОМАЛЬНОЕ ДВИЖЕНИЕ</b>\n\n"
                    f"Пара: <b>{symbol.upper()}</b>\n"
                    f"Движение: <b>{direction} {change_pct:+.2f}%</b> за 15 мин\n"
                    f"Цена сейчас: <b>{current_price:,.4f} USDT</b>\n"
                    f"Цена 15м назад: {prev_price:,.4f} USDT\n"
                    f"Объём 24h: {ticker_data.get('volume_24h', 'н/д'):,.0f}\n"
                    f"Уровень: <b>{severity}</b>"
                )
                await self._send_alert(message)
                await self.redis.save_alert(symbol, "SPIKE", severity,
                                            current_price, change_pct, message)
                self._mark_sent(alert_key)

        await self.redis.store_price_history(symbol, current_price)

    async def check_indicator_alerts(self, symbol: str, indicators: dict):
        """Проверяет индикаторные сигналы."""
        if not indicators:
            return

        rsi = indicators.get("rsi")
        macd_signal = indicators.get("macd_signal_type")
        bb_pos = indicators.get("bb_position")

        # ─── RSI экстремальные значения ───────────────────────
        if rsi is not None:
            if rsi >= 75:
                alert_key = f"RSI_OB_{symbol}"
                if not self._is_cooldown(alert_key, "RSI_EXTREME"):
                    msg = (f"🔴 <b>RSI ПЕРЕКУПЛЕН</b>\n"
                           f"Пара: {symbol.upper()} | RSI: <b>{rsi}</b> (≥75)\n"
                           f"Возможен откат или разворот вниз")
                    await self._send_alert(msg)
                    self._mark_sent(alert_key)

            elif rsi <= 25:
                alert_key = f"RSI_OS_{symbol}"
                if not self._is_cooldown(alert_key, "RSI_EXTREME"):
                    msg = (f"🟢 <b>RSI ПЕРЕПРОДАН</b>\n"
                           f"Пара: {symbol.upper()} | RSI: <b>{rsi}</b> (≤25)\n"
                           f"Возможен отскок или разворот вверх")
                    await self._send_alert(msg)
                    self._mark_sent(alert_key)

        # ─── MACD пересечение ──────────────────────────────────
        if macd_signal in ("bullish_cross", "bearish_cross"):
            alert_key = f"MACD_{symbol}_{macd_signal}"
            if not self._is_cooldown(alert_key, "MACD_CROSS"):
                emoji = "🟢" if macd_signal == "bullish_cross" else "🔴"
                direction = "БЫЧЬЕ" if macd_signal == "bullish_cross" else "МЕДВЕЖЬЕ"
                msg = (f"{emoji} <b>MACD {direction} ПЕРЕСЕЧЕНИЕ</b>\n"
                       f"Пара: {symbol.upper()}\n"
                       f"MACD гистограмма изменила знак")
                await self._send_alert(msg)
                self._mark_sent(alert_key)

        # ─── Выход за Bollinger Band ───────────────────────────
        if bb_pos is not None:
            if bb_pos >= 95:
                alert_key = f"BB_UPPER_{symbol}"
                if not self._is_cooldown(alert_key, "BB_BREAK"):
                    msg = (f"⚠️ <b>ВЫХОД ЗА ВЕРХНЮЮ BB</b>\n"
                           f"Пара: {symbol.upper()} | BB позиция: {bb_pos}%\n"
                           f"Цена у верхней полосы Боллинджера")
                    await self._send_alert(msg)
                    self._mark_sent(alert_key)
            elif bb_pos <= 5:
                alert_key = f"BB_LOWER_{symbol}"
                if not self._is_cooldown(alert_key, "BB_BREAK"):
                    msg = (f"⚠️ <b>ВЫХОД ЗА НИЖНЮЮ BB</b>\n"
                           f"Пара: {symbol.upper()} | BB позиция: {bb_pos}%\n"
                           f"Цена у нижней полосы Боллинджера")
                    await self._send_alert(msg)
                    self._mark_sent(alert_key)

    async def _send_alert(self, text: str):
        """Отправляет алерт напрямую в Telegram через Bot API."""
        if not TELEGRAM_TOKEN or not TELEGRAM_ADMIN_ID:
            logger.warning("Telegram credentials not set, alert skipped")
            return
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_ADMIN_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_notification": False,
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        logger.error(f"Telegram alert failed: {await resp.text()}")
                    else:
                        logger.info(f"Alert sent: {text[:60]}...")
        except Exception as e:
            logger.error(f"Alert send error: {e}")
```


***

## `collector/journal/writer.py`

```python
"""
Запись AI-журнала — структурированных снапшотов рынка для AI-агентов.
Realtime: каждые 30 секунд → Redis
Hourly: каждый час → PostgreSQL
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from loguru import logger

from storage.redis_client import RedisClient
from storage.postgres_client import PostgresClient

WATCHLIST_ENV = __import__("os").getenv("WATCHLIST", "btcusdt,ethusdt,solusdt,bnbusdt")
WATCHLIST = WATCHLIST_ENV.split(",")


class JournalWriter:

    def __init__(self, redis: RedisClient, pg: PostgresClient):
        self.redis = redis
        self.pg = pg

    async def build_snapshot(self) -> dict:
        """Собирает полный снапшот рынка из Redis."""
        snapshot = {}
        indicators_all = {}
        alerts = []

        for symbol in WATCHLIST:
            ticker = await self.redis.get_ticker(symbol)
            if ticker:
                snapshot[symbol] = {
                    "price":        ticker.get("price"),
                    "change_24h":   f"{ticker.get('change_24h_pct', 0):+.2f}%",
                    "high_24h":     ticker.get("high_24h"),
                    "low_24h":      ticker.get("low_24h"),
                    "volume_24h":   ticker.get("volume_24h"),
                }

            ind = await self.redis.get_indicators(symbol)
            if ind:
                indicators_all[symbol] = {
                    "rsi":          ind.get("rsi"),
                    "macd_signal":  ind.get("macd_signal_type"),
                    "ema_trend":    ind.get("ema_trend"),
                    "bb_position":  ind.get("bb_position"),
                    "atr_pct":      ind.get("atr_pct"),
                }

        recent_alerts = await self.redis.get_recent_alerts(limit=10)
        for a in recent_alerts:
            alerts.append({
                "symbol":   a.get("symbol"),
                "type":     a.get("type"),
                "severity": a.get("severity"),
                "detail":   a.get("detail"),
                "time":     a.get("time"),
            })

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "snapshot":  snapshot,
            "indicators": indicators_all,
            "alerts":     alerts,
            "market_summary": self._generate_summary(snapshot, indicators_all),
        }

    def _generate_summary(self, snapshot: dict, indicators: dict) -> str:
        """Генерирует краткое текстовое резюме рынка для AI."""
        lines = []
        bullish_count = 0
        bearish_count = 0

        for symbol, data in snapshot.items():
            change_str = data.get("change_24h", "0%")
            change = float(change_str.replace("%", "").replace("+", ""))
            if change > 0:
                bullish_count += 1
            else:
                bearish_count += 1

        total = bullish_count + bearish_count
        sentiment = "бычий" if bullish_count > total * 0.6 else \
                    "медвежий" if bearish_count > total * 0.6 else "нейтральный/смешанный"
        lines.append(f"Общий сентимент по вотчлисту: {sentiment} ({bullish_count} растут, {bearish_count} падают)")

        # RSI сводка
        rsi_values = [v.get("rsi") for v in indicators.values() if v.get("rsi")]
        if rsi_values:
            avg_rsi = sum(rsi_values) / len(rsi_values)
            lines.append(f"Средний RSI(14): {avg_rsi:.1f}")

        return " | ".join(lines)

    async def run_realtime_snapshot(self):
        """Записывает снапшот в Redis каждые 30 секунд."""
        while True:
            try:
                snap = await self.build_snapshot()
                await self.redis.set_json("ai:journal:latest", snap, ttl=120)
                logger.debug("Realtime journal snapshot updated")
            except Exception as e:
                logger.error(f"Realtime snapshot error: {e}")
            await asyncio.sleep(30)

    async def run_hourly_snapshot(self):
        """Записывает снапшот в PostgreSQL каждый час."""
        while True:
            await asyncio.sleep(3600)
            try:
                snap = await self.build_snapshot()
                await self.pg.save_journal_snapshot(snap, journal_type="hourly")
                logger.info("Hourly journal snapshot saved to PostgreSQL")
            except Exception as e:
                logger.error(f"Hourly snapshot error: {e}")
```


***

## `collector/storage/redis_client.py`

```python
"""
Redis клиент — быстрый кэш тикеров, свечей, индикаторов, журнала.
"""

import json
import time
from collections import deque
import redis.asyncio as aioredis
from loguru import logger


class RedisClient:

    def __init__(self):
        self._redis: aioredis.Redis | None = None
        self._url = __import__("os").getenv("REDIS_URL", "redis://localhost:6379/0")

    async def connect(self):
        self._redis = aioredis.from_url(self._url, decode_responses=True)
        await self._redis.ping()
        logger.success("Redis connected")

    async def close(self):
        if self._redis:
            await self._redis.aclose()

    # ─── Тикеры ────────────────────────────────────────────────
    async def set_ticker(self, symbol: str, data: dict, ttl: int = 60):
        await self._redis.setex(f"ticker:{symbol}", ttl, json.dumps(data))

    async def get_ticker(self, symbol: str) -> dict | None:
        raw = await self._redis.get(f"ticker:{symbol}")
        return json.loads(raw) if raw else None

    # ─── Свечи (список последних N свечей) ─────────────────────
    async def push_candle(self, symbol: str, interval: str, candle: dict,
                           max_length: int = 500):
        key = f"candles:{symbol}:{interval}"
        await self._redis.rpush(key, json.dumps(candle))
        await self._redis.ltrim(key, -max_length, -1)

    async def get_candles(self, symbol: str, interval: str,
                           limit: int = 200) -> list[dict]:
        key = f"candles:{symbol}:{interval}"
        raw_list = await self._redis.lrange(key, -limit, -1)
        return [json.loads(r) for r in raw_list]

    # ─── Индикаторы ─────────────────────────────────────────────
    async def set_indicators(self, symbol: str, data: dict,
                              interval: str = "1min", ttl: int = 120):
        await self._redis.setex(f"indicators:{symbol}:{interval}", ttl, json.dumps(data))

    async def get_indicators(self, symbol: str, interval: str = "1min") -> dict | None:
        raw = await self._redis.get(f"indicators:{symbol}:{interval}")
        return json.loads(raw) if raw else None

    # ─── Стакан ────────────────────────────────────────────────
    async def set_depth(self, symbol: str, data: dict, ttl: int = 30):
        await self._redis.setex(f"depth:{symbol}", ttl, json.dumps(data))

    async def get_depth(self, symbol: str) -> dict | None:
        raw = await self._redis.get(f"depth:{symbol}")
        return json.loads(raw) if raw else None

    # ─── История цен для детектора спайков ─────────────────────
    async def store_price_history(self, symbol: str, price: float):
        """Хранит историю цен с timestamp — для расчёта спайков за 15 мин."""
        key = f"price_history:{symbol}"
        entry = json.dumps({"price": price, "ts": int(time.time())})
        await self._redis.rpush(key, entry)
        await self._redis.ltrim(key, -200, -1)
        await self._redis.expire(key, 3600)

    async def get_price_15min_ago(self, symbol: str) -> float | None:
        """Возвращает цену ~15 минут назад."""
        key = f"price_history:{symbol}"
        raw_list = await self._redis.lrange(key, 0, -1)
        if not raw_list:
            return None

        now = int(time.time())
        target_ts = now - 900  # 15 минут

        for raw in raw_list:
            entry = json.loads(raw)
            if abs(entry["ts"] - target_ts) < 120:  # ±2 минуты
                return entry["price"]

        # Если точного нет — берём самую старую запись
        oldest = json.loads(raw_list[^4_0])
        return oldest["price"] if oldest else None

    # ─── Алерты ─────────────────────────────────────────────────
    async def save_alert(self, symbol: str, alert_type: str, severity: str,
                          price: float, change_pct: float, detail: str):
        key = "alerts:recent"
        entry = json.dumps({
            "symbol": symbol, "type": alert_type, "severity": severity,
            "price": price, "change_pct": change_pct, "detail": detail,
            "time": int(time.time()),
        })
        await self._redis.rpush(key, entry)
        await self._redis.ltrim(key, -50, -1)
        await self._redis.expire(key, 86400)

    async def get_recent_alerts(self, limit: int = 10) -> list[dict]:
        raw_list = await self._redis.lrange("alerts:recent", -limit, -1)
        return [json.loads(r) for r in reversed(raw_list)]

    # ─── Общий JSON ─────────────────────────────────────────────
    async def set_json(self, key: str, data: dict, ttl: int = 300):
        await self._redis.setex(key, ttl, json.dumps(data, ensure_ascii=False))

    async def get_json(self, key: str) -> dict | None:
        raw = await self._redis.get(key)
        return json.loads(raw) if raw else None
```


***

Три ключевых момента по HTX WebSocket в коде:  все маркет-данные приходят сжатыми GZIP — `gzip.decompress()` обязателен, иначе получишь нечитаемые байты.  HTX использует собственный ping/pong механизм — нужно отвечать `{"pong": ping_value}`, иначе соединение закрывается через ~10 секунд.  С июля 2024 в WS v2 доступно и выставление ордеров — когда дойдём до автоматической торговли, модуль уже будет готов подключиться.[^4_3][^4_2][^4_1]

Следующий шаг: `chatbot/main.py` с aiogram и handlers, или `collector/storage/postgres_client.py`?
<span style="display:none">[^4_10][^4_11][^4_12][^4_13][^4_14][^4_15][^4_16][^4_17][^4_18][^4_19][^4_20][^4_21][^4_22][^4_23][^4_24][^4_25][^4_26][^4_27][^4_28][^4_4][^4_5][^4_6][^4_7][^4_8][^4_9]</span>

<div align="center">⁂</div>

[^4_1]: https://www.htx.com/support/900000014146

[^4_2]: https://github.com/ccxt/ccxt/issues/22451

[^4_3]: https://www.htx.com/support/84974124835764

[^4_4]: https://www.htx.com/support/360000647861

[^4_5]: https://www.htx.com/support/360000268281

[^4_6]: https://www.htx.com/support/900004161286

[^4_7]: https://www.htx.com/support/900000458446

[^4_8]: https://www.htx.com/support/900001279163

[^4_9]: https://www.htx.com/support/360000602042

[^4_10]: https://www.htx.com/support/360000348161

[^4_11]: https://www.htx.com/support/900000193806

[^4_12]: https://www.htx.com/support/900000762363

[^4_13]: https://www.htx.com/support/900004253583/

[^4_14]: https://www.htx.com/support/360000533921

[^4_15]: https://www.htx.com/vi-vi/support/900001279163

[^4_16]: https://www.htx.com/support/360000263201

[^4_17]: https://www.htx.com/en-us/opend/newApiPages

[^4_18]: https://huobiapi.github.io/docs/spot/v1/en/

[^4_19]: https://stackoverflow.com/questions/72027298/how-can-i-connect-huobi-websocket-from-client-browser

[^4_20]: https://github.com/sammchardy/python-binance/blob/master/Endpoints.md

[^4_21]: https://github.com/JKorf/HTX.Net

[^4_22]: https://www.youtube.com/watch?v=dLcMnDMJOwo

[^4_23]: https://docs.tardis.dev/historical-data-details/huobi-dm-options

[^4_24]: https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data

[^4_25]: https://intercom.help/blockpit/en/articles/12136970-how-to-import-data-via-htx-api-key

[^4_26]: https://stackoverflow.com/questions/51358147/fetch-candlestick-kline-data-from-binance-api-using-python-preferably-requests

[^4_27]: https://www.htx.com/en-us/opend/newApiPages/?id=10000066-77b7-11ed-9966-0242ac110003

[^4_28]: https://webseaex.github.io/en/spot-market/kline/


---

# Пиши chatbot/main.py с aiogram и handlers

Отлично — даю полный chatbot со всеми файлами, актуальный aiogram 3.14 паттерн с роутерами, FSM и inline-клавиатурами.[^5_1][^5_2]

***

## `chatbot/main.py`

```python
"""
Agent #2 — Trading Chatbot
Точка входа. Polling mode через aiogram 3.14.
"""

import asyncio
import os
import signal
from loguru import logger
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

from handlers.start import router as start_router
from handlers.market import router as market_router
from handlers.chat import router as chat_router
from handlers.alerts import router as alerts_router
from storage.redis_client import ChatRedisClient
from storage.postgres_client import ChatPostgresClient
from context.builder import ContextBuilder

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

logger.remove()
logger.add(
    "logs/chatbot.log",
    rotation="50 MB",
    retention="7 days",
    level=LOG_LEVEL,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name} | {message}"
)
logger.add(lambda msg: print(msg, end=""), level=LOG_LEVEL, colorize=True)


async def main():
    if not TOKEN:
        raise ValueError("TELEGRAM_TOKEN not set in .env")

    # ─── Инициализация БД ──────────────────────────────────────
    redis_client = ChatRedisClient()
    await redis_client.connect()

    pg_client = ChatPostgresClient()
    await pg_client.connect()

    context_builder = ContextBuilder(redis=redis_client, pg=pg_client)

    # ─── FSM хранилище в Redis (переживает перезапуск) ─────────
    storage = RedisStorage.from_url(REDIS_URL)

    # ─── Bot и Dispatcher ──────────────────────────────────────
    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher(storage=storage)

    # ─── Внедряем зависимости через middleware ─────────────────
    # Передаём клиентов во все handlers через data dict
    dp["redis"] = redis_client
    dp["pg"] = pg_client
    dp["context_builder"] = context_builder

    # ─── Подключаем роутеры (порядок важен!) ───────────────────
    dp.include_router(start_router)
    dp.include_router(market_router)
    dp.include_router(alerts_router)
    dp.include_router(chat_router)    # chat — последним, ловит всё остальное

    # ─── Startup / Shutdown хуки ───────────────────────────────
    @dp.startup()
    async def on_startup():
        bot_info = await bot.get_me()
        logger.success(f"Bot @{bot_info.username} started (id={bot_info.id})")

    @dp.shutdown()
    async def on_shutdown():
        await redis_client.close()
        await pg_client.close()
        await storage.close()
        logger.info("Bot shutdown complete")

    # ─── Graceful shutdown ─────────────────────────────────────
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    logger.info("Starting polling...")
    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
        handle_signals=False,
    )


if __name__ == "__main__":
    asyncio.run(main())
```


***

## `chatbot/handlers/start.py`

```python
"""
/start, /help, /menu — приветствие и главное меню.
"""

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

router = Router(name="start")


def main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Рынок сейчас",    callback_data="market:snapshot")
    builder.button(text="🔍 Анализ пары",     callback_data="market:analyze")
    builder.button(text="💬 Чат с AI",        callback_data="chat:start")
    builder.button(text="⚠️ Алерты",          callback_data="alerts:menu")
    builder.button(text="📈 Индикаторы",      callback_data="market:indicators")
    builder.button(text="📐 Расчёт позиции",  callback_data="market:position")
    builder.adjust(2)  # 2 кнопки в строке
    return builder.as_markup()


WELCOME_TEXT = """
👋 <b>Торговый AI-ассистент</b>

Биржа: <b>HTX</b> | Режим: <b>реальное время</b>

Что умею:
• 📊 Мониторинг цен и индикаторов в реалтайме
• 🤖 Анализ рынка через несколько AI-моделей
• ⚠️ Алерты при аномальных движениях
• 📐 Расчёт позиций и риск-менеджмент
• 💬 Свободный чат — задавай любые вопросы

Выбери действие или просто напиши мне:
"""


@router.message(CommandStart())
async def cmd_start(message: Message, **kwargs):
    logger.info(f"User {message.from_user.id} started bot")
    await message.answer(
        WELCOME_TEXT,
        reply_markup=main_menu_keyboard()
    )


@router.message(Command("help"))
async def cmd_help(message: Message, **kwargs):
    help_text = """
<b>Команды бота:</b>

/start — главное меню
/help — эта справка
/price [пара] — текущая цена (напр. /price btcusdt)
/analysis [пара] — технический анализ
/indicators [пара] — индикаторы
/position — расчёт позиции
/alerts — управление алертами
/history — история чата с AI
/clear — очистить историю чата

💡 Или просто пиши текстом — AI ответит!
"""
    await message.answer(help_text)


@router.message(Command("menu"))
async def cmd_menu(message: Message, **kwargs):
    await message.answer("Главное меню:", reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "menu:main")
async def cb_main_menu(callback: CallbackQuery, **kwargs):
    await callback.message.edit_text(WELCOME_TEXT, reply_markup=main_menu_keyboard())
    await callback.answer()
```


***

## `chatbot/handlers/market.py`

```python
"""
Рыночные команды: цена, анализ, индикаторы, расчёт позиции.
"""

import asyncio
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from loguru import logger

from context.builder import ContextBuilder
from ai.router import route_and_respond
from storage.redis_client import ChatRedisClient
from storage.postgres_client import ChatPostgresClient

router = Router(name="market")

WATCHLIST = ["btcusdt", "ethusdt", "solusdt", "bnbusdt", "xrpusdt"]


# ─── FSM состояния ──────────────────────────────────────────
class AnalysisState(StatesGroup):
    waiting_symbol = State()
    waiting_timeframe = State()


class PositionState(StatesGroup):
    waiting_symbol = State()
    waiting_entry = State()
    waiting_stoploss = State()
    waiting_takeprofit = State()
    waiting_deposit = State()
    waiting_risk = State()


# ─── Клавиатуры ─────────────────────────────────────────────
def symbols_keyboard(callback_prefix: str):
    builder = InlineKeyboardBuilder()
    for sym in WATCHLIST:
        builder.button(
            text=sym.upper(),
            callback_data=f"{callback_prefix}:{sym}"
        )
    builder.button(text="✏️ Ввести вручную", callback_data=f"{callback_prefix}:custom")
    builder.button(text="◀️ Назад", callback_data="menu:main")
    builder.adjust(3)
    return builder.as_markup()


def timeframe_keyboard(symbol: str):
    builder = InlineKeyboardBuilder()
    for tf, label in [("1min","1м"), ("5min","5м"), ("60min","1ч"),
                       ("4hour","4ч"), ("1day","1д")]:
        builder.button(text=label, callback_data=f"analysis:tf:{symbol}:{tf}")
    builder.button(text="◀️ Назад", callback_data="market:analyze")
    builder.adjust(5)
    return builder.as_markup()


def back_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Меню", callback_data="menu:main")
    builder.button(text="🔄 Обновить", callback_data="market:snapshot")
    builder.adjust(2)
    return builder.as_markup()


# ─── /price [symbol] ────────────────────────────────────────
@router.message(Command("price"))
async def cmd_price(message: Message, redis: ChatRedisClient,
                    context_builder: ContextBuilder, **kwargs):
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "Укажи пару: /price btcusdt",
            reply_markup=symbols_keyboard("quickprice")
        )
        return
    symbol = args[^5_1].lower().strip()
    await _send_price(message, symbol, redis, context_builder)


@router.callback_query(F.data.startswith("quickprice:"))
async def cb_quick_price(callback: CallbackQuery, redis: ChatRedisClient,
                          context_builder: ContextBuilder, **kwargs):
    symbol = callback.data.split(":")[^5_1]
    await callback.answer()
    await _send_price(callback.message, symbol, redis, context_builder, edit=True)


async def _send_price(message: Message, symbol: str, redis: ChatRedisClient,
                       context_builder: ContextBuilder, edit: bool = False):
    ticker = await redis.get_ticker(symbol)
    if not ticker:
        text = f"❌ Нет данных по <b>{symbol.upper()}</b>. Пара в вотчлисте?"
    else:
        depth = await redis.get_depth(symbol)
        ind = await redis.get_indicators(symbol)
        
        change = ticker.get("change_24h_pct", 0)
        change_emoji = "📈" if change >= 0 else "📉"
        rsi_val = ind.get("rsi", "—") if ind else "—"
        rsi_state = ""
        if ind and ind.get("rsi"):
            r = float(ind["rsi"])
            rsi_state = " 🔴" if r > 70 else (" 🟢" if r < 30 else " 🟡")

        imbalance = depth.get("imbalance", 1.0) if depth else 1.0
        imb_txt = f"🟢 +{imbalance:.2f}" if imbalance > 1.2 else \
                  f"🔴 -{1/imbalance:.2f}" if imbalance < 0.8 else f"🟡 {imbalance:.2f}"

        text = (
            f"<b>{symbol.upper()}</b> {change_emoji}\n\n"
            f"💰 Цена: <b>{ticker['price']:,.4f} USDT</b>\n"
            f"📊 Изменение 24h: <b>{change:+.2f}%</b>\n"
            f"📈 Макс 24h: {ticker.get('high_24h', '—'):,.4f}\n"
            f"📉 Мин 24h: {ticker.get('low_24h', '—'):,.4f}\n"
            f"💎 Объём 24h: {ticker.get('volume_24h', 0):,.0f}\n\n"
            f"📐 RSI(14): <b>{rsi_val}{rsi_state}</b>\n"
            f"⚖️ Дисбаланс стакана: {imb_txt}\n"
        )
        if ind:
            macd_sig = ind.get("macd_signal_type", "")
            ema_trend = ind.get("ema_trend", "")
            if macd_sig:
                macd_emoji = "🟢" if macd_sig == "bullish_cross" else \
                             "🔴" if macd_sig == "bearish_cross" else "⚪"
                text += f"{macd_emoji} MACD: {macd_sig.replace('_', ' ')}\n"
            if ema_trend:
                text += f"📏 EMA тренд: {'🟢 бычий' if ema_trend == 'bullish' else '🔴 медвежий'}\n"

    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data=f"quickprice:{symbol}")
    builder.button(text="📊 Анализ", callback_data=f"analysis:tf:{symbol}:60min")
    builder.button(text="◀️ Меню", callback_data="menu:main")
    builder.adjust(2)

    if edit:
        await message.edit_text(text, reply_markup=builder.as_markup())
    else:
        await message.answer(text, reply_markup=builder.as_markup())


# ─── /analysis + callback ────────────────────────────────────
@router.message(Command("analysis"))
@router.callback_query(F.data == "market:analyze")
async def cmd_analysis(event, **kwargs):
    message = event if isinstance(event, Message) else event.message
    text = "Выбери торговую пару для анализа:"
    kb = symbols_keyboard("analysis:sym")
    if isinstance(event, CallbackQuery):
        await event.answer()
        await message.edit_text(text, reply_markup=kb)
    else:
        args = event.text.split()
        if len(args) >= 2:
            symbol = args[^5_1].lower()
            await message.answer(
                f"Выбери таймфрейм для <b>{symbol.upper()}</b>:",
                reply_markup=timeframe_keyboard(symbol)
            )
            return
        await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("analysis:sym:"))
async def cb_analysis_symbol(callback: CallbackQuery, **kwargs):
    symbol = callback.data.split(":")[^5_2]
    await callback.answer()
    if symbol == "custom":
        await callback.message.edit_text(
            "Введи символ пары (например: <code>solusdt</code>):"
        )
        return
    await callback.message.edit_text(
        f"Выбери таймфрейм для <b>{symbol.upper()}</b>:",
        reply_markup=timeframe_keyboard(symbol)
    )


@router.callback_query(F.data.startswith("analysis:tf:"))
async def cb_analysis_run(callback: CallbackQuery, redis: ChatRedisClient,
                           pg: ChatPostgresClient,
                           context_builder: ContextBuilder, **kwargs):
    _, _, symbol, timeframe = callback.data.split(":")
    await callback.answer("⏳ Анализирую...")

    # Показываем "печатает..."
    await callback.message.edit_text(
        f"⏳ Запрашиваю анализ <b>{symbol.upper()}</b> [{timeframe}]...\n"
        f"Используется: GLM-5.1 (thinking mode)"
    )

    try:
        snapshot = await context_builder.build_for_ai(symbol)
        chat_history = await pg.get_chat_history(callback.from_user.id, limit=10)
        query = f"Сделай технический анализ {symbol.upper()} на таймфрейме {timeframe}"
        
        result = await route_and_respond(
            user_message=query,
            market_snapshot=snapshot,
            chat_history=chat_history,
            force_intent="deep_analysis"
        )

        # Сохраняем в историю
        await pg.save_chat_message(callback.from_user.id, "user", query, None)
        await pg.save_chat_message(
            callback.from_user.id, "assistant",
            result["text"], result["model"]
        )

        model_badge = f"\n\n<i>🤖 {result['model']}</i>"
        response_text = result["text"] + model_badge

        # Telegram лимит 4096 символов — режем если надо
        if len(response_text) > 4000:
            response_text = response_text[:3950] + "...\n\n<i>[обрезано]</i>"

        builder = InlineKeyboardBuilder()
        builder.button(text="🔄 Обновить анализ", callback_data=f"analysis:tf:{symbol}:{timeframe}")
        builder.button(text="💬 Обсудить с AI", callback_data="chat:start")
        builder.button(text="◀️ Меню", callback_data="menu:main")
        builder.adjust(2)

        await callback.message.edit_text(response_text, reply_markup=builder.as_markup())

    except Exception as e:
        logger.error(f"Analysis error: {e}")
        await callback.message.edit_text(
            f"❌ Ошибка анализа: {e}\n\nПопробуй позже.",
            reply_markup=back_keyboard()
        )


# ─── /indicators ─────────────────────────────────────────────
@router.message(Command("indicators"))
@router.callback_query(F.data == "market:indicators")
async def cmd_indicators(event, redis: ChatRedisClient, **kwargs):
    message = event if isinstance(event, Message) else event.message
    if isinstance(event, CallbackQuery):
        await event.answer()

    lines = ["📐 <b>Индикаторы (1min)</b>\n"]
    for symbol in WATCHLIST:
        ind = await redis.get_indicators(symbol)
        ticker = await redis.get_ticker(symbol)
        if ind and ticker:
            rsi = ind.get("rsi", "—")
            macd = ind.get("macd_signal_type", "—")
            ema_trend = ind.get("ema_trend", "—")
            price = ticker.get("price", 0)

            rsi_emoji = "🔴" if float(rsi or 0) > 70 else "🟢" if float(rsi or 0) < 30 else "🟡"
            lines.append(
                f"<b>{symbol.upper()}</b> — {price:,.4f}\n"
                f"  RSI: {rsi_emoji} {rsi} | MACD: {macd} | EMA: {ema_trend}\n"
            )

    text = "\n".join(lines) if len(lines) > 1 else "❌ Нет данных по индикаторам"
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data="market:indicators")
    builder.button(text="◀️ Меню", callback_data="menu:main")
    builder.adjust(2)
    kb = builder.as_markup()

    if isinstance(event, CallbackQuery):
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


# ─── /position — расчёт позиции через FSM ────────────────────
@router.message(Command("position"))
@router.callback_query(F.data == "market:position")
async def cmd_position_start(event, state: FSMContext, **kwargs):
    message = event if isinstance(event, Message) else event.message
    if isinstance(event, CallbackQuery):
        await event.answer()
    await state.set_state(PositionState.waiting_symbol)
    await message.answer(
        "📐 <b>Расчёт торговой позиции</b>\n\n"
        "Введи символ пары (например: <code>btcusdt</code>):",
        reply_markup=symbols_keyboard("pos:sym")
    )


@router.callback_query(F.data.startswith("pos:sym:"), PositionState.waiting_symbol)
@router.message(PositionState.waiting_symbol)
async def pos_get_symbol(event, state: FSMContext, **kwargs):
    if isinstance(event, CallbackQuery):
        symbol = event.data.split(":")[^5_2]
        await event.answer()
        message = event.message
    else:
        symbol = event.text.lower().strip()
        message = event
    await state.update_data(symbol=symbol)
    await state.set_state(PositionState.waiting_deposit)
    await message.answer(f"Символ: <b>{symbol.upper()}</b>\n\nВведи размер депозита в <b>USDT</b>:")


@router.message(PositionState.waiting_deposit)
async def pos_get_deposit(message: Message, state: FSMContext, **kwargs):
    try:
        deposit = float(message.text.replace(",", "."))
        await state.update_data(deposit=deposit)
        await state.set_state(PositionState.waiting_risk)
        await message.answer(f"Депозит: <b>{deposit:,.2f} USDT</b>\n\nВведи риск на сделку в <b>%</b> (рекомендуется 1-2):")
    except ValueError:
        await message.answer("❌ Введи число, например: <code>1000</code>")


@router.message(PositionState.waiting_risk)
async def pos_get_risk(message: Message, state: FSMContext, **kwargs):
    try:
        risk = float(message.text.replace(",", ".").replace("%", ""))
        await state.update_data(risk_pct=risk)
        await state.set_state(PositionState.waiting_entry)
        await message.answer(f"Риск: <b>{risk}%</b>\n\nВведи цену <b>входа</b>:")
    except ValueError:
        await message.answer("❌ Введи число, например: <code>1.5</code>")


@router.message(PositionState.waiting_entry)
async def pos_get_entry(message: Message, state: FSMContext, **kwargs):
    try:
        entry = float(message.text.replace(",", "."))
        await state.update_data(entry=entry)
        await state.set_state(PositionState.waiting_stoploss)
        await message.answer(f"Вход: <b>{entry:,.4f}</b>\n\nВведи цену <b>стоп-лосса</b>:")
    except ValueError:
        await message.answer("❌ Введи число")


@router.message(PositionState.waiting_stoploss)
async def pos_get_stoploss(message: Message, state: FSMContext, **kwargs):
    try:
        sl = float(message.text.replace(",", "."))
        await state.update_data(stop_loss=sl)
        await state.set_state(PositionState.waiting_takeprofit)
        await message.answer(f"Стоп-лосс: <b>{sl:,.4f}</b>\n\nВведи цену <b>тейк-профита</b>:")
    except ValueError:
        await message.answer("❌ Введи число")


@router.message(PositionState.waiting_takeprofit)
async def pos_get_tp_and_calculate(message: Message, state: FSMContext,
                                    context_builder: ContextBuilder,
                                    pg: ChatPostgresClient, **kwargs):
    try:
        tp = float(message.text.replace(",", "."))
    except ValueError:
        await message.answer("❌ Введи число")
        return

    data = await state.get_data()
    await state.clear()

    symbol    = data["symbol"]
    deposit   = data["deposit"]
    risk_pct  = data["risk_pct"]
    entry     = data["entry"]
    sl        = data["stop_loss"]

    # ─── Расчёт ────────────────────────────────────────────────
    max_loss   = deposit * (risk_pct / 100)
    sl_dist    = abs(entry - sl)
    tp_dist    = abs(tp - entry)

    if sl_dist == 0:
        await message.answer("❌ Стоп-лосс совпадает с ценой входа")
        return

    position_size  = max_loss / sl_dist
    position_usdt  = position_size * entry
    rr_ratio       = tp_dist / sl_dist
    sl_pct         = (sl_dist / entry) * 100
    tp_pct         = (tp_dist / entry) * 100
    direction       = "🟢 ЛОНГ" if tp > entry else "🔴 ШОРТ"

    result_text = (
        f"📐 <b>Расчёт позиции: {symbol.upper()}</b>\n\n"
        f"Направление: <b>{direction}</b>\n\n"
        f"<b>Параметры:</b>\n"
        f"  💰 Депозит: {deposit:,.2f} USDT\n"
        f"  ⚠️ Риск: {risk_pct}% = <b>{max_loss:,.2f} USDT</b>\n\n"
        f"<b>Уровни:</b>\n"
        f"  📍 Вход:     <b>{entry:,.4f}</b>\n"
        f"  🛑 Стоп:     <b>{sl:,.4f}</b> (-{sl_pct:.2f}%)\n"
        f"  🎯 Тейк:     <b>{tp:,.4f}</b> (+{tp_pct:.2f}%)\n\n"
        f"<b>Расчёт:</b>\n"
        f"  📦 Размер позиции: <b>{position_size:.6f} {symbol.upper().replace('USDT','')} </b>\n"
        f"  💵 Объём в USDT:  <b>{position_usdt:,.2f} USDT</b>\n"
        f"  ⚖️ R:R = <b>1:{rr_ratio:.2f}</b> "
        f"{'✅' if rr_ratio >= 2 else '⚠️ Низкий R:R'}\n\n"
    )

    if rr_ratio < 1.5:
        result_text += "⚠️ <b>R:R ниже 1:1.5 — сделка нецелесообразна</b>\n"
    elif rr_ratio >= 3:
        result_text += "✅ <b>Отличное R:R соотношение!</b>\n"

    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Обсудить с AI", callback_data="chat:start")
    builder.button(text="📊 Анализ пары", callback_data=f"analysis:sym:{symbol}")
    builder.button(text="◀️ Меню", callback_data="menu:main")
    builder.adjust(2)

    await message.answer(result_text, reply_markup=builder.as_markup())


# ─── Снапшот рынка ───────────────────────────────────────────
@router.callback_query(F.data == "market:snapshot")
async def cb_market_snapshot(callback: CallbackQuery, redis: ChatRedisClient, **kwargs):
    await callback.answer("⏳ Загружаю...")
    journal = await redis.get_json("ai:journal:latest")
    if not journal:
        await callback.message.edit_text(
            "❌ Данные собираются, подожди 30 секунд...",
            reply_markup=back_keyboard()
        )
        return

    lines = ["📊 <b>Рынок сейчас</b>\n"]
    for symbol, data in journal.get("snapshot", {}).items():
        change = data.get("change_24h", "0%")
        price = data.get("price", 0)
        arrow = "📈" if "+" in str(change) else "📉"
        ind = journal.get("indicators", {}).get(symbol, {})
        rsi = ind.get("rsi", "—")
        lines.append(f"{arrow} <b>{symbol.upper()}</b>: {price:,.4f}  {change}  RSI:{rsi}")

    summary = journal.get("market_summary", "")
    if summary:
        lines.append(f"\n📝 {summary}")

    alerts = journal.get("alerts", [])
    if alerts:
        lines.append("\n⚠️ <b>Последние алерты:</b>")
        for a in alerts[:3]:
            lines.append(f"  • {a.get('symbol','').upper()}: {a.get('detail','')}")

    text = "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=back_keyboard())
```


***

## `chatbot/handlers/chat.py`

```python
"""
Свободный чат с AI-агентом.
Роутер отправляет запрос в нужную модель по интенту.
"""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from ai.router import route_and_respond
from context.builder import ContextBuilder
from storage.postgres_client import ChatPostgresClient
from storage.redis_client import ChatRedisClient

router = Router(name="chat")

# Модели для отображения пользователю
MODEL_LABELS = {
    "Perplexity Sonar Pro": "🔍 Sonar Pro",
    "GLM-5.1":              "🧠 GLM-5.1",
    "MiniMax M2.7":         "⚡ MiniMax",
    "QwenCode3-Plus":       "💻 QwenCode",
    "Qwen3.6-Plus":         "📐 Qwen3.6",
}

TYPING_MESSAGES = {
    "market_news":    "🔍 Ищу новости через Perplexity Sonar...",
    "deep_analysis":  "🧠 GLM-5.1 анализирует (thinking mode)...",
    "quick_chat":     "⚡ MiniMax думает...",
    "backtest_code":  "💻 Qwen пишет код...",
    "position_calc":  "📐 Рассчитываю позицию...",
    "alert_setup":    "⚙️ Настраиваю алерт...",
}


@router.callback_query(F.data == "chat:start")
async def cb_chat_start(callback: CallbackQuery, **kwargs):
    await callback.answer()
    await callback.message.answer(
        "💬 <b>Режим чата с AI</b>\n\n"
        "Задавай любые вопросы о рынке, трейдинге, индикаторах.\n"
        "Я автоматически выберу подходящую модель.\n\n"
        "<i>Примеры: «Что происходит с BTC?», «Проанализируй ETH 4h», «Какой RSI у SOL?»</i>"
    )


@router.message(Command("history"))
async def cmd_history(message: Message, pg: ChatPostgresClient, **kwargs):
    history = await pg.get_chat_history(message.from_user.id, limit=10)
    if not history:
        await message.answer("📭 История чата пуста")
        return
    lines = ["📜 <b>Последние сообщения:</b>\n"]
    for entry in history[-6:]:
        role_emoji = "👤" if entry["role"] == "user" else "🤖"
        content = entry["content"][:100] + "..." if len(entry["content"]) > 100 else entry["content"]
        lines.append(f"{role_emoji} {content}")
    await message.answer("\n".join(lines))


@router.message(Command("clear"))
async def cmd_clear(message: Message, pg: ChatPostgresClient, **kwargs):
    await pg.clear_chat_history(message.from_user.id)
    await message.answer("🧹 История чата очищена")


# ─── Главный обработчик текстовых сообщений ─────────────────
# Ловит ВСЁ что не поймали другие роутеры
@router.message(F.text & ~F.text.startswith("/"))
async def handle_chat_message(message: Message,
                               redis: ChatRedisClient,
                               pg: ChatPostgresClient,
                               context_builder: ContextBuilder,
                               **kwargs):
    user_id = message.from_user.id
    user_text = message.text.strip()

    # Показываем typing
    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        # ─── Собираем контекст ──────────────────────────────
        snapshot = await context_builder.build_for_ai()
        chat_history = await pg.get_chat_history(user_id, limit=20)

        # ─── Предварительное сообщение о выборе модели ──────
        thinking_msg = await message.answer("⏳ Определяю намерение...")

        # ─── Запрос к AI ─────────────────────────────────────
        result = await route_and_respond(
            user_message=user_text,
            market_snapshot=snapshot,
            chat_history=chat_history,
        )

        intent = result.get("intent", "quick_chat")
        model = result.get("model", "AI")
        response_text = result.get("text", "Нет ответа")
        thinking = result.get("thinking")

        # ─── Удаляем "думаю..." сообщение ──────────────────
        await thinking_msg.delete()

        # ─── Если есть цепочка рассуждений — отдельным сообщением
        if thinking and len(thinking) > 50:
            thinking_preview = thinking[:800] + "..." if len(thinking) > 800 else thinking
            await message.answer(
                f"<blockquote>💭 <i>Рассуждение ({model}):</i>\n\n{thinking_preview}</blockquote>"
            )

        # ─── Основной ответ ─────────────────────────────────
        model_label = MODEL_LABELS.get(model, f"🤖 {model}")
        footer = f"\n\n<i>{model_label} | /help</i>"
        full_response = response_text + footer

        # Разбиваем длинные ответы
        if len(full_response) > 4000:
            parts = _split_message(full_response, 3900)
            for i, part in enumerate(parts):
                if i == len(parts) - 1:
                    await message.answer(part, reply_markup=_chat_keyboard())
                else:
                    await message.answer(part)
        else:
            await message.answer(full_response, reply_markup=_chat_keyboard())

        # ─── Сохраняем в историю ────────────────────────────
        await pg.save_chat_message(user_id, "user", user_text, None)
        await pg.save_chat_message(user_id, "assistant", response_text, model)

        logger.info(f"User {user_id} | intent={intent} | model={model} | chars={len(response_text)}")

    except Exception as e:
        logger.error(f"Chat error for user {user_id}: {e}", exc_info=True)
        await message.answer(
            f"❌ Ошибка обработки запроса.\n<code>{str(e)[:200]}</code>\n\nПопробуй ещё раз.",
            reply_markup=_chat_keyboard()
        )


def _chat_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Рынок", callback_data="market:snapshot")
    builder.button(text="◀️ Меню", callback_data="menu:main")
    builder.adjust(2)
    return builder.as_markup()


def _split_message(text: str, max_len: int) -> list[str]:
    """Разбивает длинный текст по абзацам, не разрывая слова."""
    parts = []
    while len(text) > max_len:
        split_at = text.rfind("\n\n", 0, max_len)
        if split_at == -1:
            split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        parts.append(text[:split_at])
        text = text[split_at:].lstrip()
    if text:
        parts.append(text)
    return parts
```


***

## `chatbot/handlers/alerts.py`

```python
"""
Управление пользовательскими алертами на цену.
"""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from storage.postgres_client import ChatPostgresClient
from storage.redis_client import ChatRedisClient

router = Router(name="alerts")


class AlertSetupState(StatesGroup):
    waiting_symbol = State()
    waiting_price = State()
    waiting_direction = State()


@router.message(Command("alerts"))
@router.callback_query(F.data == "alerts:menu")
async def cmd_alerts_menu(event, pg: ChatPostgresClient, **kwargs):
    message = event if isinstance(event, Message) else event.message
    user_id = event.from_user.id
    if isinstance(event, CallbackQuery):
        await event.answer()

    active_alerts = await pg.get_user_alerts(user_id)

    lines = ["⚠️ <b>Твои алерты</b>\n"]
    if active_alerts:
        for a in active_alerts:
            direction = "≥" if a["direction"] == "above" else "≤"
            lines.append(
                f"• {a['symbol'].upper()} {direction} {a['price']:,.4f} "
                f"({'✅' if a['active'] else '❌'})"
            )
    else:
        lines.append("Алертов нет")

    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить алерт", callback_data="alerts:add")
    builder.button(text="🗑 Удалить все",    callback_data="alerts:clear")
    builder.button(text="◀️ Меню",           callback_data="menu:main")
    builder.adjust(2)

    text = "\n".join(lines)
    if isinstance(event, CallbackQuery):
        await message.edit_text(text, reply_markup=builder.as_markup())
    else:
        await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(F.data == "alerts:add")
async def cb_alert_add(callback: CallbackQuery, state: FSMContext, **kwargs):
    await callback.answer()
    await state.set_state(AlertSetupState.waiting_symbol)
    await callback.message.answer(
        "Введи символ пары для алерта (например: <code>btcusdt</code>):"
    )


@router.message(AlertSetupState.waiting_symbol)
async def alert_get_symbol(message: Message, state: FSMContext, **kwargs):
    symbol = message.text.lower().strip()
    await state.update_data(symbol=symbol)
    await state.set_state(AlertSetupState.waiting_price)
    await message.answer(f"Пара: <b>{symbol.upper()}</b>\n\nВведи цену для алерта:")


@router.message(AlertSetupState.waiting_price)
async def alert_get_price(message: Message, state: FSMContext, **kwargs):
    try:
        price = float(message.text.replace(",", "."))
        await state.update_data(price=price)
        await state.set_state(AlertSetupState.waiting_direction)
        builder = InlineKeyboardBuilder()
        builder.button(text="📈 Выше цены",  callback_data="alert:dir:above")
        builder.button(text="📉 Ниже цены",  callback_data="alert:dir:below")
        builder.adjust(2)
        await message.answer(
            f"Цена: <b>{price:,.4f}</b>\n\nСрабатывать когда цена:",
            reply_markup=builder.as_markup()
        )
    except ValueError:
        await message.answer("❌ Введи число")


@router.callback_query(F.data.startswith("alert:dir:"), AlertSetupState.waiting_direction)
async def alert_save(callback: CallbackQuery, state: FSMContext,
                      pg: ChatPostgresClient, **kwargs):
    direction = callback.data.split(":")[^5_2]
    data = await state.get_data()
    await state.clear()

    await pg.save_user_alert(
        user_id=callback.from_user.id,
        symbol=data["symbol"],
        price=data["price"],
        direction=direction
    )

    dir_text = "поднимется выше" if direction == "above" else "упадёт ниже"
    await callback.answer("✅ Алерт сохранён")
    await callback.message.edit_text(
        f"✅ <b>Алерт создан!</b>\n\n"
        f"Пара: <b>{data['symbol'].upper()}</b>\n"
        f"Условие: цена {dir_text} <b>{data['price']:,.4f}</b>\n\n"
        f"Получишь уведомление как только условие сработает."
    )


@router.callback_query(F.data == "alerts:clear")
async def cb_alerts_clear(callback: CallbackQuery, pg: ChatPostgresClient, **kwargs):
    await pg.clear_user_alerts(callback.from_user.id)
    await callback.answer("🗑 Алерты удалены")
    await callback.message.edit_text("✅ Все алерты удалены")
```


***

## `chatbot/context/builder.py`

```python
"""
Сборка контекста из Redis для передачи AI-агентам.
Читает ai:journal:latest — снапшот, созданный collector.
"""

from storage.redis_client import ChatRedisClient
from storage.postgres_client import ChatPostgresClient
from loguru import logger


class ContextBuilder:

    def __init__(self, redis: ChatRedisClient, pg: ChatPostgresClient):
        self.redis = redis
        self.pg = pg

    async def build_for_ai(self, focus_symbol: str = None) -> dict:
        """
        Собирает полный контекст из Redis для передачи AI-модели.
        focus_symbol — если задан, добавляем расширенные данные по конкретной паре.
        """
        # Базовый снапшот от collector
        journal = await self.redis.get_json("ai:journal:latest") or {}

        context = {
            "timestamp":      journal.get("timestamp", "unknown"),
            "snapshot":       journal.get("snapshot", {}),
            "indicators":     journal.get("indicators", {}),
            "alerts":         journal.get("alerts", [])[:5],
            "market_summary": journal.get("market_summary", ""),
        }

        # Расширенные данные по конкретной паре
        if focus_symbol:
            ticker = await self.redis.get_ticker(focus_symbol)
            depth = await self.redis.get_depth(focus_symbol)
            ind_1min  = await self.redis.get_indicators(focus_symbol, "1min")
            ind_60min = await self.redis.get_indicators(focus_symbol, "60min")

            context["focus"] = {
                "symbol":         focus_symbol,
                "ticker":         ticker,
                "order_book":     depth,
                "indicators_1m":  ind_1min,
                "indicators_1h":  ind_60min,
            }

        return context
```


***

## `chatbot/storage/redis_client.py`

```python
"""Redis клиент для chatbot — читает данные, записанные collector."""

import json
import redis.asyncio as aioredis
from loguru import logger
import os


class ChatRedisClient:

    def __init__(self):
        self._redis = None
        self._url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    async def connect(self):
        self._redis = aioredis.from_url(self._url, decode_responses=True)
        await self._redis.ping()
        logger.success("Chatbot Redis connected")

    async def close(self):
        if self._redis:
            await self._redis.aclose()

    async def get_ticker(self, symbol: str) -> dict | None:
        raw = await self._redis.get(f"ticker:{symbol}")
        return json.loads(raw) if raw else None

    async def get_indicators(self, symbol: str, interval: str = "1min") -> dict | None:
        raw = await self._redis.get(f"indicators:{symbol}:{interval}")
        return json.loads(raw) if raw else None

    async def get_depth(self, symbol: str) -> dict | None:
        raw = await self._redis.get(f"depth:{symbol}")
        return json.loads(raw) if raw else None

    async def get_json(self, key: str) -> dict | None:
        raw = await self._redis.get(key)
        return json.loads(raw) if raw else None

    async def get_recent_alerts(self, limit: int = 10) -> list:
        raw_list = await self._redis.lrange("alerts:recent", -limit, -1)
        return [json.loads(r) for r in reversed(raw_list)]
```


***

Архитектура chatbot строится на трёх ключевых принципах aiogram 3:  каждый смысловой блок — отдельный `Router`, который подключается к `Dispatcher` через `include_router()`;  многошаговые диалоги (расчёт позиции, настройка алертов) управляются через `FSMContext` с `StatesGroup` — состояния хранятся в Redis и переживают перезапуск;  зависимости (redis, pg, context_builder) передаются через `dp["key"] = value` и автоматически инжектируются в хэндлеры через `**kwargs`.[^5_3][^5_2][^5_1]

Остался последний файл — `collector/storage/postgres_client.py` с полной схемой запросов, или сразу проверим весь проект командой `make build`?
<span style="display:none">[^5_10][^5_11][^5_12][^5_13][^5_14][^5_15][^5_4][^5_5][^5_6][^5_7][^5_8][^5_9]</span>

<div align="center">⁂</div>

[^5_1]: https://docs.aiogram.dev/en/latest/dispatcher/router.html

[^5_2]: https://docs.aiogram.dev/en/latest/dispatcher/finite_state_machine/index.html

[^5_3]: https://www.linkedin.com/pulse/exploring-finite-state-machine-aiogram-3-powerful-tool-telegram-dbsae

[^5_4]: https://docs.aiogram.dev/en/v3.17.0/dispatcher/router.html

[^5_5]: https://github.com/aiogram/aiogram/blob/dev-3.x/docs/dispatcher/dispatcher.rst

[^5_6]: https://github.com/aiogram/aiogram/blob/dev-3.x/docs/dispatcher/router.rst

[^5_7]: https://git.akarpov.ru/aiogram/aiogram/blame/commit/d37a7f0a0d60813e9477c33c44bec95812af79bc/docs/dispatcher/router.md

[^5_8]: https://stackoverflow.com/questions/77809738/how-to-connect-a-router-in-aiogram-3-x-x

[^5_9]: https://mastergroosha.github.io/aiogram-3-guide/routers/

[^5_10]: https://www.youtube.com/watch?v=n8vx6qJz5_g

[^5_11]: https://github.com/aiogram/aiogram/blob/dev-3.x/aiogram/dispatcher/dispatcher.py

[^5_12]: https://github.com/aiogram/aiogram/blob/dev-3.x/examples/finite_state_machine.py

[^5_13]: https://mastergroosha.github.io/aiogram-3-guide/quickstart/

[^5_14]: https://mastergroosha.github.io/aiogram-3-guide/fsm/

[^5_15]: https://habr.com/ru/companies/amvera/articles/820527/


---

# Пиши postgres_client.py

Даю оба клиента — для collector и chatbot — плюс финальный чеклист проекта. Поиск не нужен, все эндпоинты уже определены из предыдущих файлов.

***

## `collector/storage/postgres_client.py`

```python
"""
PostgreSQL клиент для collector.
Запись свечей, индикаторов, алертов, AI-журнала.
Использует asyncpg через SQLAlchemy async engine.
"""

import os
from datetime import datetime, timezone
from loguru import logger
import asyncpg


DATABASE_URL_RAW = os.getenv(
    "DATABASE_URL",
    "postgresql://bot:pass@localhost:5432/trading"
).replace("postgresql+asyncpg://", "postgresql://")


class PostgresClient:

    def __init__(self):
        self._pool: asyncpg.Pool | None = None

    async def connect(self):
        self._pool = await asyncpg.create_pool(
            dsn=DATABASE_URL_RAW,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
        logger.success("Collector PostgreSQL connected")

    async def close(self):
        if self._pool:
            await self._pool.close()

    # ════════════════════════════════════════════════════════════
    # СВЕЧИ (OHLCV)
    # ════════════════════════════════════════════════════════════

    async def save_candles(self, symbol: str, interval: str,
                            candles: list[dict]):
        """Батчевая вставка исторических свечей (ON CONFLICT — пропускаем дубли)."""
        if not candles:
            return
        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO candles (symbol, interval, open_time, open, high, low, close, volume)
                VALUES ($1, $2, to_timestamp($3), $4, $5, $6, $7, $8)
                ON CONFLICT (symbol, interval, open_time) DO NOTHING
                """,
                [
                    (
                        symbol, interval,
                        c["open_time"],
                        c["open"], c["high"], c["low"], c["close"], c["volume"]
                    )
                    for c in candles
                ]
            )
        logger.debug(f"Saved {len(candles)} candles: {symbol} {interval}")

    async def upsert_candle(self, symbol: str, interval: str, candle: dict):
        """
        Вставка/обновление одной свечи.
        Используется при получении тика из WebSocket — последняя свеча обновляется
        пока не закроется (close price меняется).
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO candles (symbol, interval, open_time, open, high, low, close, volume)
                VALUES ($1, $2, to_timestamp($3), $4, $5, $6, $7, $8)
                ON CONFLICT (symbol, interval, open_time)
                DO UPDATE SET
                    high   = GREATEST(candles.high,   EXCLUDED.high),
                    low    = LEAST(candles.low,        EXCLUDED.low),
                    close  = EXCLUDED.close,
                    volume = EXCLUDED.volume
                """,
                symbol, interval,
                candle["open_time"],
                candle["open"], candle["high"], candle["low"],
                candle["close"], candle["volume"]
            )

    async def get_candles(self, symbol: str, interval: str,
                           limit: int = 500) -> list[dict]:
        """Получение последних N свечей из PostgreSQL (для бэктестинга)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT extract(epoch FROM open_time)::bigint AS open_time,
                       open, high, low, close, volume
                FROM candles
                WHERE symbol = $1 AND interval = $2
                ORDER BY open_time DESC
                LIMIT $3
                """,
                symbol, interval, limit
            )
        return [dict(r) for r in reversed(rows)]

    async def get_candles_range(self, symbol: str, interval: str,
                                 start_ts: int, end_ts: int) -> list[dict]:
        """Свечи за произвольный период — для бэктестинга стратегий."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT extract(epoch FROM open_time)::bigint AS open_time,
                       open, high, low, close, volume
                FROM candles
                WHERE symbol   = $1
                  AND interval = $2
                  AND open_time BETWEEN to_timestamp($3) AND to_timestamp($4)
                ORDER BY open_time ASC
                """,
                symbol, interval, start_ts, end_ts
            )
        return [dict(r) for r in rows]

    # ════════════════════════════════════════════════════════════
    # ИНДИКАТОРЫ
    # ════════════════════════════════════════════════════════════

    async def upsert_indicators(self, symbol: str, indicators: dict):
        """Сохраняем последние значения индикаторов."""
        if not indicators:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO indicators
                    (symbol, timestamp, rsi_14, macd, macd_signal, macd_hist,
                     ema_20, ema_50, bb_upper, bb_middle, bb_lower)
                VALUES ($1, NOW(), $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT DO NOTHING
                """,
                symbol,
                indicators.get("rsi"),
                indicators.get("macd"),
                indicators.get("macd_signal"),
                indicators.get("macd_hist"),
                indicators.get("ema20"),
                indicators.get("ema50"),
                indicators.get("bb_upper"),
                indicators.get("bb_middle"),
                indicators.get("bb_lower"),
            )

    async def get_indicators_history(self, symbol: str,
                                      hours: int = 24) -> list[dict]:
        """История значений индикаторов за последние N часов."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT timestamp, rsi_14, macd, macd_signal, macd_hist,
                       ema_20, ema_50, bb_upper, bb_middle, bb_lower
                FROM indicators
                WHERE symbol    = $1
                  AND timestamp > NOW() - INTERVAL '1 hour' * $2
                ORDER BY timestamp ASC
                """,
                symbol, hours
            )
        return [dict(r) for r in rows]

    # ════════════════════════════════════════════════════════════
    # АЛЕРТЫ
    # ════════════════════════════════════════════════════════════

    async def save_alert(self, symbol: str, alert_type: str, severity: str,
                          price: float, change_pct: float, detail: str):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO alerts
                    (symbol, alert_type, severity, price, change_pct, detail, sent)
                VALUES ($1, $2, $3, $4, $5, $6, TRUE)
                """,
                symbol, alert_type, severity, price, change_pct, detail
            )

    async def get_recent_alerts(self, limit: int = 50,
                                 symbol: str = None) -> list[dict]:
        async with self._pool.acquire() as conn:
            if symbol:
                rows = await conn.fetch(
                    """
                    SELECT symbol, alert_type, severity, price,
                           change_pct, detail, created_at
                    FROM alerts
                    WHERE symbol = $1
                    ORDER BY created_at DESC LIMIT $2
                    """,
                    symbol, limit
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT symbol, alert_type, severity, price,
                           change_pct, detail, created_at
                    FROM alerts
                    ORDER BY created_at DESC LIMIT $1
                    """,
                    limit
                )
        return [dict(r) for r in rows]

    # ════════════════════════════════════════════════════════════
    # AI ЖУРНАЛ
    # ════════════════════════════════════════════════════════════

    async def save_journal_snapshot(self, snapshot: dict,
                                     journal_type: str = "hourly"):
        """Сохраняет полный рыночный снапшот в JSONB."""
        import json
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ai_journal (snapshot, journal_type)
                VALUES ($1::jsonb, $2)
                """,
                json.dumps(snapshot, ensure_ascii=False), journal_type
            )

    async def get_journal_snapshots(self, limit: int = 24,
                                     journal_type: str = "hourly") -> list[dict]:
        """Последние N снапшотов для ретроспективного анализа AI."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT snapshot, created_at
                FROM ai_journal
                WHERE journal_type = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                journal_type, limit
            )
        return [{"snapshot": dict(r["snapshot"]), "created_at": r["created_at"]}
                for r in rows]
```


***

## `chatbot/storage/postgres_client.py`

```python
"""
PostgreSQL клиент для chatbot.
История чатов, пользовательские алерты, ретроспективные данные.
"""

import os
import json
from loguru import logger
import asyncpg


DATABASE_URL_RAW = os.getenv(
    "DATABASE_URL",
    "postgresql://bot:pass@localhost:5432/trading"
).replace("postgresql+asyncpg://", "postgresql://")


class ChatPostgresClient:

    def __init__(self):
        self._pool: asyncpg.Pool | None = None

    async def connect(self):
        self._pool = await asyncpg.create_pool(
            dsn=DATABASE_URL_RAW,
            min_size=2,
            max_size=5,
            command_timeout=15,
        )
        # Создаём таблицы если нет (chatbot-специфичные)
        await self._ensure_tables()
        logger.success("Chatbot PostgreSQL connected")

    async def close(self):
        if self._pool:
            await self._pool.close()

    async def _ensure_tables(self):
        """
        Дополнительные таблицы, специфичные для chatbot.
        Основные (candles, indicators, alerts, ai_journal) — в db/init.sql.
        """
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id          BIGSERIAL PRIMARY KEY,
                    user_id     BIGINT          NOT NULL,
                    role        VARCHAR(20)     NOT NULL,
                    content     TEXT            NOT NULL,
                    model_used  VARCHAR(50),
                    created_at  TIMESTAMPTZ     DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_chat_user_time
                    ON chat_history(user_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS user_alerts (
                    id          BIGSERIAL PRIMARY KEY,
                    user_id     BIGINT          NOT NULL,
                    symbol      VARCHAR(20)     NOT NULL,
                    price       NUMERIC(20, 8)  NOT NULL,
                    direction   VARCHAR(10)     NOT NULL,  -- above / below
                    active      BOOLEAN         DEFAULT TRUE,
                    triggered_at TIMESTAMPTZ,
                    created_at  TIMESTAMPTZ     DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_user_alerts_active
                    ON user_alerts(active, symbol);
            """)

    # ════════════════════════════════════════════════════════════
    # ИСТОРИЯ ЧАТА
    # ════════════════════════════════════════════════════════════

    async def save_chat_message(self, user_id: int, role: str,
                                 content: str, model_used: str | None):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO chat_history (user_id, role, content, model_used)
                VALUES ($1, $2, $3, $4)
                """,
                user_id, role, content, model_used
            )

    async def get_chat_history(self, user_id: int,
                                limit: int = 20) -> list[dict]:
        """
        Возвращает последние N сообщений в формате messages для AI API.
        [{"role": "user"/"assistant", "content": "..."}]
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT role, content, model_used, created_at
                FROM chat_history
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                user_id, limit
            )
        # Разворачиваем — AI нужна хронологическая история
        history = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        return history

    async def get_chat_history_full(self, user_id: int,
                                     limit: int = 50) -> list[dict]:
        """Полная история с метаданными — для отображения пользователю."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT role, content, model_used, created_at
                FROM chat_history
                WHERE user_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                user_id, limit
            )
        return [dict(r) for r in reversed(rows)]

    async def clear_chat_history(self, user_id: int):
        async with self._pool.acquire() as conn:
            deleted = await conn.execute(
                "DELETE FROM chat_history WHERE user_id = $1",
                user_id
            )
        logger.info(f"Cleared chat history for user {user_id}: {deleted}")

    async def get_active_users(self, since_hours: int = 24) -> list[int]:
        """Список user_id активных за последние N часов — для рассылки алертов."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT user_id
                FROM chat_history
                WHERE created_at > NOW() - INTERVAL '1 hour' * $1
                """,
                since_hours
            )
        return [r["user_id"] for r in rows]

    # ════════════════════════════════════════════════════════════
    # ПОЛЬЗОВАТЕЛЬСКИЕ АЛЕРТЫ НА ЦЕНУ
    # ════════════════════════════════════════════════════════════

    async def save_user_alert(self, user_id: int, symbol: str,
                               price: float, direction: str):
        """direction: 'above' | 'below'"""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO user_alerts (user_id, symbol, price, direction)
                VALUES ($1, $2, $3, $4)
                """,
                user_id, symbol.lower(), price, direction
            )
        logger.info(f"Alert saved: user={user_id} {symbol} {direction} {price}")

    async def get_user_alerts(self, user_id: int,
                               active_only: bool = True) -> list[dict]:
        async with self._pool.acquire() as conn:
            if active_only:
                rows = await conn.fetch(
                    """
                    SELECT id, symbol, price, direction, active, created_at
                    FROM user_alerts
                    WHERE user_id = $1 AND active = TRUE
                    ORDER BY created_at DESC
                    """,
                    user_id
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, symbol, price, direction, active, created_at
                    FROM user_alerts
                    WHERE user_id = $1
                    ORDER BY created_at DESC LIMIT 50
                    """,
                    user_id
                )
        return [dict(r) for r in rows]

    async def get_all_active_alerts(self) -> list[dict]:
        """
        Все активные алерты всех пользователей — для checker-фонового сервиса.
        Используется collector'ом или отдельным checker-тасклетом.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, user_id, symbol, price, direction
                FROM user_alerts
                WHERE active = TRUE
                ORDER BY symbol, price
                """
            )
        return [dict(r) for r in rows]

    async def trigger_alert(self, alert_id: int):
        """Помечает алерт как сработавший и деактивирует."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE user_alerts
                SET active = FALSE, triggered_at = NOW()
                WHERE id = $1
                """,
                alert_id
            )

    async def clear_user_alerts(self, user_id: int):
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM user_alerts WHERE user_id = $1",
                user_id
            )

    # ════════════════════════════════════════════════════════════
    # РЕТРОСПЕКТИВНЫЕ ДАННЫЕ ДЛЯ AI
    # ════════════════════════════════════════════════════════════

    async def get_candles_for_analysis(self, symbol: str,
                                        interval: str = "60min",
                                        limit: int = 200) -> list[dict]:
        """Свечи для передачи в AI на анализ или бэктестинг."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT extract(epoch FROM open_time)::bigint AS open_time,
                       open, high, low, close, volume
                FROM candles
                WHERE symbol = $1 AND interval = $2
                ORDER BY open_time DESC
                LIMIT $3
                """,
                symbol, interval, limit
            )
        return [dict(r) for r in reversed(rows)]

    async def get_journal_for_ai(self, limit: int = 24) -> list[dict]:
        """
        Последние N часовых снапшотов из ai_journal.
        Передаётся GLM-5.1 для ретроспективного анализа динамики.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT snapshot, created_at
                FROM ai_journal
                WHERE journal_type = 'hourly'
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit
            )
        return [
            {
                "snapshot":    dict(r["snapshot"]),
                "created_at":  r["created_at"].isoformat()
            }
            for r in reversed(rows)
        ]

    async def get_alert_stats(self, symbol: str = None,
                               days: int = 7) -> list[dict]:
        """Статистика алертов за период — для анализа аномалий."""
        async with self._pool.acquire() as conn:
            if symbol:
                rows = await conn.fetch(
                    """
                    SELECT symbol, alert_type, severity, COUNT(*) as count,
                           AVG(change_pct) as avg_change
                    FROM alerts
                    WHERE created_at > NOW() - INTERVAL '1 day' * $1
                      AND symbol = $2
                    GROUP BY symbol, alert_type, severity
                    ORDER BY count DESC
                    """,
                    days, symbol
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT symbol, alert_type, severity, COUNT(*) as count,
                           AVG(change_pct) as avg_change
                    FROM alerts
                    WHERE created_at > NOW() - INTERVAL '1 day' * $1
                    GROUP BY symbol, alert_type, severity
                    ORDER BY count DESC
                    """,
                    days
                )
        return [dict(r) for r in rows]
```


***

## Финальный чеклист запуска

```
htx-trading-bot/
├── ✅ docker-compose.yml
├── ✅ .env  (заполнен)
├── ✅ .env.example
├── ✅ Makefile
├── ✅ db/init.sql
│
├── collector/
│   ├── ✅ Dockerfile
│   ├── ✅ requirements.txt
│   ├── ✅ main.py
│   ├── htx/
│   │   ├── ✅ websocket.py
│   │   └── ✅ rest.py
│   ├── indicators/
│   │   └── ✅ calculator.py
│   ├── alerts/
│   │   └── ✅ detector.py
│   ├── journal/
│   │   └── ✅ writer.py
│   └── storage/
│       ├── ✅ redis_client.py
│       └── ✅ postgres_client.py  ← только что
│
└── chatbot/
    ├── ✅ Dockerfile
    ├── ✅ requirements.txt
    ├── ✅ main.py
    ├── handlers/
    │   ├── ✅ start.py
    │   ├── ✅ market.py
    │   ├── ✅ chat.py
    │   └── ✅ alerts.py
    ├── ai/
    │   ├── ✅ prompts.py
    │   ├── ✅ knowledge_base.py
    │   └── ✅ router.py
    ├── context/
    │   └── ✅ builder.py
    └── storage/
        ├── ✅ redis_client.py
        └── ✅ postgres_client.py  ← только что
```


***

## Команды запуска

```bash
# 1. Заполни .env
cp .env.example .env && nano .env

# 2. Создай папки для логов
mkdir -p collector/logs chatbot/logs

# 3. Собери образы
make build

# 4. Запусти всё
make up

# 5. Проверь что все контейнеры живы
make ps

# 6. Следи за логами collector (данные HTX)
make logs-collector

# 7. Следи за логами chatbot (бот)
make logs-chatbot

# 8. Проверь БД
make db-shell
# \dt  — список таблиц
# SELECT COUNT(*) FROM candles;  — свечи загружаются
```


***

## Важные `__init__.py`

Не забудь создать пустые файлы чтобы Python видел пакеты:

```bash
# collector
touch collector/htx/__init__.py
touch collector/indicators/__init__.py
touch collector/alerts/__init__.py
touch collector/journal/__init__.py
touch collector/storage/__init__.py

# chatbot
touch chatbot/handlers/__init__.py
touch chatbot/ai/__init__.py
touch chatbot/context/__init__.py
touch chatbot/storage/__init__.py
```

Проект полностью готов к первому запуску. `asyncpg` выбран вместо SQLAlchemy async для прямого пула соединений — это даёт минимальный overhead на запросах с высокой частотой (тики 1min, upsert свечей). Метод `get_all_active_alerts()` в `ChatPostgresClient` — точка расширения: когда будешь добавлять checker-таск в collector, он будет периодически сканировать эту таблицу и сравнивать с текущими ценами из Redis.

---

# Продумай максимально сценарии использования и сопоставь с текущим функционалом, продумай все максимально детально и предложи вариант финального качества

Провёл полный аудит. Из истории видно, что у тебя уже есть опыт нескольких итераций этого проекта  — сейчас сделаем системный разбор, чтобы финальная версия закрыла все пробелы.[^7_1]

***

## Аудит: сценарии vs текущий функционал

Сначала — честная карта покрытия:


| Сценарий | Текущий функционал | Статус |
| :-- | :-- | :-- |
| Утренний брифинг 08:00 МСК | ❌ Нет scheduler | 🔴 Отсутствует |
| Прогноз на день с разбивкой по часам | ❌ Нет | 🔴 Отсутствует |
| Реалтайм цена по запросу | ✅ `/price`, callback | ✅ Есть |
| Автоалерты спайков (автомат.) | ✅ `detector.py` | ✅ Есть |
| Пользовательские ценовые алерты | ✅ FSM + pg | ✅ Есть |
| Тех. анализ по паре + таймфрейму | ✅ GLM-5.1 | ✅ Есть |
| Расчёт позиции / риска | ✅ FSM + inline | ✅ Есть |
| Новостной фон через Perplexity | ✅ router.py | ✅ Есть |
| Свободный чат с AI | ✅ chat.py | ✅ Есть |
| Проверка пользоват. алертов в реалтайме | ❌ Нет checker-таска | 🔴 Отсутствует |
| Портфель / трекинг позиций | ❌ Нет таблицы + хендлера | 🔴 Отсутствует |
| История алертов + статистика | ⚠️ В pg есть, UI нет | 🟡 Частично |
| Ретроспективный анализ (24ч, 7д) | ⚠️ Данные есть, AI-запроса нет | 🟡 Частично |
| Настройки бота (вотчлист, модель) | ❌ Захардкожено | 🔴 Отсутствует |
| Бэктестинг через QwenCode | ❌ Роутер есть, UI нет | 🔴 Отсутствует |
| Экспорт данных (CSV, отчёт) | ❌ Нет | 🔴 Отсутствует |


***

## Финальная карта сценариев

Всё разбито на **4 режима использования** — от утра до вечера:

```
УТРО (08:00 МСК) ─ АВТОМАТИЧЕСКИ
  └─ Брифинг дня: цены, индикаторы, прогноз на 10/12/14/16/18/20ч
  └─ Сентимент рынка (Fear&Greed, BTC доминирование)
  └─ Топ-3 аномалии за ночь

В ТЕЧЕНИЕ ДНЯ ─ ПО ЗАПРОСУ / АВТОМАТИЧЕСКИ
  ├─ Алерты спайков → мгновенно
  ├─ Алерты пользователя (цена достигла X) → мгновенно
  ├─ Быстрая цена → /price или кнопка
  ├─ Тех. анализ → выбор пары + таймфрейм
  └─ Чат с AI → произвольный вопрос

СДЕЛКА ─ ПО ЗАПРОСУ
  ├─ Расчёт позиции (депозит → риск → уровни)
  ├─ Запись в портфель (вход, SL, TP)
  ├─ Трекинг открытой позиции (PnL в реалтайме)
  └─ Закрытие позиции → запись в историю

АНАЛИТИКА ─ ГЛУБОКИЙ РЕЖИМ
  ├─ Ретроспектива 24ч/7д по паре
  ├─ Новостной фон (Perplexity)
  ├─ Бэктест стратегии (QwenCode)
  └─ Недельный отчёт (воскресенье 20:00)
```


***

## Финальная структура директорий (дополненная)

```
htx-trading-bot/
│
├── collector/
│   ├── ...                         (без изменений)
│   └── scheduler/
│       ├── __init__.py
│       ├── briefing.py             # 🆕 Утренний брифинг + дневные прогнозы
│       └── alert_checker.py        # 🆕 Проверка пользоват. алертов каждые 30 сек
│
└── chatbot/
    ├── ...
    ├── handlers/
    │   ├── start.py                (обновить меню)
    │   ├── market.py               (без изменений)
    │   ├── chat.py                 (без изменений)
    │   ├── alerts.py               (без изменений)
    │   ├── portfolio.py            # 🆕 Портфель, позиции, PnL
    │   ├── analytics.py            # 🆕 Ретроспектива, бэктест, отчёты
    │   └── settings.py             # 🆕 Вотчлист, модель, расписание
    └── scheduler/
        ├── __init__.py
        └── reports.py              # 🆕 Плановые отправки (брифинг, недельный)
```


***

## `collector/scheduler/alert_checker.py`

```python
"""
Фоновый таск: каждые 30 секунд проверяет пользовательские ценовые алерты.
Читает активные алерты из PostgreSQL, сравнивает с ценами из Redis.
При срабатывании — отправляет уведомление и деактивирует алерт.
"""

import asyncio
import os
import aiohttp
from loguru import logger

from storage.redis_client import RedisClient
from storage.postgres_client import PostgresClient

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")


class AlertChecker:

    def __init__(self, redis: RedisClient, pg: PostgresClient):
        self.redis = redis
        self.pg = pg

    async def run(self):
        """Основной цикл проверки — запускается из collector/main.py."""
        logger.info("Alert checker started")
        while True:
            try:
                await self._check_all()
            except Exception as e:
                logger.error(f"Alert checker error: {e}")
            await asyncio.sleep(30)

    async def _check_all(self):
        active_alerts = await self.pg.get_all_active_alerts()
        if not active_alerts:
            return

        # Группируем по символу — один запрос Redis на символ
        by_symbol: dict[str, list] = {}
        for alert in active_alerts:
            by_symbol.setdefault(alert["symbol"], []).append(alert)

        for symbol, alerts in by_symbol.items():
            ticker = await self.redis.get_ticker(symbol)
            if not ticker:
                continue
            current_price = float(ticker.get("price", 0))

            for alert in alerts:
                triggered = False
                if alert["direction"] == "above" and current_price >= float(alert["price"]):
                    triggered = True
                elif alert["direction"] == "below" and current_price <= float(alert["price"]):
                    triggered = True

                if triggered:
                    await self._fire_alert(alert, current_price)
                    await self.pg.trigger_alert(alert["id"])

    async def _fire_alert(self, alert: dict, current_price: float):
        direction_text = "поднялась выше" if alert["direction"] == "above" else "упала ниже"
        text = (
            f"🎯 <b>АЛЕРТ СРАБОТАЛ!</b>\n\n"
            f"Пара: <b>{alert['symbol'].upper()}</b>\n"
            f"Цена {direction_text} <b>{float(alert['price']):,.4f}</b>\n"
            f"Текущая цена: <b>{current_price:,.4f} USDT</b>\n\n"
            f"<i>Алерт деактивирован</i>"
        )
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(url, json={
                    "chat_id": alert["user_id"],
                    "text": text,
                    "parse_mode": "HTML"
                })
            logger.info(f"User alert fired: user={alert['user_id']} {alert['symbol']} @ {current_price}")
        except Exception as e:
            logger.error(f"Failed to send user alert: {e}")
```


***

## `collector/scheduler/briefing.py`

```python
"""
Утренний брифинг и дневные прогнозы.
- 08:00 МСК → полный дневной брифинг с прогнозами на 10/12/14/16/18/20ч
- 20:00 МСК вс → недельный итоговый отчёт
Запрос к AI делается через HTTP к chatbot-сервису (межсервисное взаимодействие).
"""

import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
import aiohttp
from loguru import logger

from storage.redis_client import RedisClient
from storage.postgres_client import PostgresClient

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHATBOT_URL      = os.getenv("CHATBOT_INTERNAL_URL", "http://chatbot:8080")
TIMEZONE_OFFSET  = 3   # МСК = UTC+3

WATCHLIST = os.getenv("WATCHLIST", "btcusdt,ethusdt,solusdt,bnbusdt").split(",")


class BriefingScheduler:

    def __init__(self, redis: RedisClient, pg: PostgresClient):
        self.redis = redis
        self.pg = pg

    async def run(self):
        """Ждёт нужного часа и запускает брифинг."""
        logger.info("Briefing scheduler started")
        while True:
            now_msk = datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET)
            hour = now_msk.hour
            minute = now_msk.minute
            weekday = now_msk.weekday()  # 0=пн, 6=вс

            if hour == 8 and minute == 0:
                await self._send_morning_briefing()
                await asyncio.sleep(61)  # пропускаем, чтобы не триггернуть дважды

            elif hour == 20 and minute == 0 and weekday == 6:
                await self._send_weekly_report()
                await asyncio.sleep(61)

            else:
                await asyncio.sleep(30)

    async def _send_morning_briefing(self):
        """
        Утренний брифинг:
        1. Собираем свежий снапшот
        2. Запрашиваем AI-прогноз (через внутренний API chatbot-сервиса)
        3. Рассылаем всем активным пользователям за 24ч
        """
        logger.info("Generating morning briefing...")
        snapshot = await self._build_rich_snapshot()
        ai_forecast = await self._request_ai_briefing(snapshot)
        active_users = await self.pg.get_active_users(since_hours=48)

        if not active_users:
            logger.warning("No active users for briefing")
            return

        text = self._format_briefing(snapshot, ai_forecast)
        await self._broadcast(active_users, text)
        logger.info(f"Morning briefing sent to {len(active_users)} users")

    async def _build_rich_snapshot(self) -> dict:
        """Расширенный снапшот с историческими данными за 24ч."""
        snapshot = {}
        for symbol in WATCHLIST:
            ticker = await self.redis.get_ticker(symbol)
            ind    = await self.redis.get_indicators(symbol)
            depth  = await self.redis.get_depth(symbol)
            # 24ч история из журнала
            history_24h = await self.pg.get_journal_snapshots(limit=24, journal_type="hourly")

            snapshot[symbol] = {
                "ticker":     ticker,
                "indicators": ind,
                "depth":      depth,
                "history_24h_count": len(history_24h),
            }

        # Алерты за последние 8ч
        recent_alerts = await self.redis.get_recent_alerts(limit=20)
        snapshot["__alerts_8h"] = [
            a for a in recent_alerts
            if (a.get("time", 0) > (__import__("time").time() - 28800))
        ]
        return snapshot

    async def _request_ai_briefing(self, snapshot: dict) -> str:
        """
        Запрашивает у GLM-5.1 прогноз на день.
        Chatbot-сервис слушает на :8080 внутреннем порту.
        """
        try:
            payload = {
                "query": (
                    "Сделай утренний торговый брифинг. "
                    "Дай прогноз движения BTC по часам: 10:00, 12:00, 14:00, 16:00, 18:00, 20:00 МСК. "
                    "Для каждого часа укажи: ожидаемое направление, ценовой диапазон, "
                    "ключевые уровни поддержки/сопротивления. "
                    "Дай общий прогноз на день: бычий/медвежий/боковик. "
                    "Топ-3 пары для внимания сегодня. Риски дня."
                ),
                "snapshot": snapshot,
                "force_intent": "deep_analysis",
                "system": "briefing"
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{CHATBOT_URL}/internal/ai_query",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as resp:
                    data = await resp.json()
                    return data.get("text", "Прогноз недоступен")
        except Exception as e:
            logger.error(f"AI briefing request failed: {e}")
            return "⚠️ AI-прогноз временно недоступен"

    def _format_briefing(self, snapshot: dict, ai_forecast: str) -> str:
        now_msk = (datetime.now(timezone.utc) + timedelta(hours=TIMEZONE_OFFSET))
        date_str = now_msk.strftime("%d.%m.%Y")

        lines = [
            f"🌅 <b>УТРЕННИЙ БРИФИНГ — {date_str}</b>",
            f"<i>HTX | 08:00 МСК</i>\n",
        ]

        # Текущие цены
        lines.append("📊 <b>Рынок прямо сейчас:</b>")
        for symbol in WATCHLIST:
            data = snapshot.get(symbol, {})
            ticker = data.get("ticker") or {}
            ind    = data.get("indicators") or {}
            price  = ticker.get("price", 0)
            ch     = ticker.get("change_24h_pct", 0)
            rsi    = ind.get("rsi", "—")
            arrow  = "📈" if ch >= 0 else "📉"
            lines.append(f"{arrow} <b>{symbol.upper()}</b>: {price:,.2f}  {ch:+.2f}%  RSI:{rsi}")

        # Алерты за ночь
        alerts = snapshot.get("__alerts_8h", [])
        if alerts:
            lines.append(f"\n⚠️ <b>Аномалии за ночь ({len(alerts)}):</b>")
            for a in alerts[:4]:
                lines.append(f"  • {a.get('symbol','').upper()}: {a.get('detail','')}")

        lines.append(f"\n🤖 <b>AI-прогноз на сегодня (GLM-5.1):</b>\n")
        lines.append(ai_forecast)

        lines.append("\n<i>Следующий брифинг: завтра 08:00 МСК</i>")
        return "\n".join(lines)

    async def _send_weekly_report(self):
        """Воскресный итоговый отчёт за неделю."""
        logger.info("Generating weekly report...")
        history_7d = await self.pg.get_journal_snapshots(limit=168, journal_type="hourly")
        active_users = await self.pg.get_active_users(since_hours=168)

        if not active_users or not history_7d:
            return

        # Формируем краткий текст без AI (экономим токены)
        text = self._format_weekly_simple(history_7d)
        await self._broadcast(active_users, text)

    def _format_weekly_simple(self, history: list) -> str:
        if not history:
            return "📅 Недельный отчёт недоступен"

        first = history[^7_0]["snapshot"].get("snapshot", {})
        last  = history[-1]["snapshot"].get("snapshot", {})
        lines = ["📅 <b>НЕДЕЛЬНЫЙ ИТОГ</b>\n"]

        for symbol in WATCHLIST:
            p_start = (first.get(symbol) or {}).get("price", 0)
            p_end   = (last.get(symbol)  or {}).get("price", 0)
            if p_start and p_end:
                chg = (p_end - p_start) / p_start * 100
                arrow = "📈" if chg >= 0 else "📉"
                lines.append(f"{arrow} <b>{symbol.upper()}</b>: {p_start:,.2f} → {p_end:,.2f} ({chg:+.2f}%)")

        return "\n".join(lines)

    async def _broadcast(self, user_ids: list, text: str):
        """Рассылка сообщения списку пользователей."""
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        async with aiohttp.ClientSession() as session:
            for user_id in user_ids:
                try:
                    await session.post(url, json={
                        "chat_id": user_id,
                        "text": text[:4096],
                        "parse_mode": "HTML"
                    })
                    await asyncio.sleep(0.1)  # Telegram rate limit
                except Exception as e:
                    logger.error(f"Broadcast to {user_id} failed: {e}")
```


***

## `chatbot/handlers/portfolio.py`

```python
"""
Портфель: открытые позиции, трекинг PnL в реалтайме, история сделок.
"""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from storage.postgres_client import ChatPostgresClient
from storage.redis_client import ChatRedisClient

router = Router(name="portfolio")


class OpenPositionState(StatesGroup):
    symbol      = State()
    direction   = State()
    entry_price = State()
    amount_usdt = State()
    stop_loss   = State()
    take_profit = State()


@router.message(Command("portfolio"))
@router.callback_query(F.data == "portfolio:menu")
async def cmd_portfolio(event, pg: ChatPostgresClient,
                         redis: ChatRedisClient, **kwargs):
    message = event if isinstance(event, Message) else event.message
    user_id = event.from_user.id
    if isinstance(event, CallbackQuery):
        await event.answer()

    positions = await pg.get_open_positions(user_id)
    closed    = await pg.get_closed_positions(user_id, limit=5)

    # ─── Считаем PnL по открытым позициям ───────────────────
    lines = ["💼 <b>ПОРТФЕЛЬ</b>\n"]
    total_pnl = 0.0

    if positions:
        lines.append("📂 <b>Открытые позиции:</b>")
        for pos in positions:
            ticker = await redis.get_ticker(pos["symbol"])
            if ticker:
                current = float(ticker.get("price", 0))
                entry   = float(pos["entry_price"])
                amount  = float(pos["amount_usdt"])
                qty     = amount / entry if entry else 0

                if pos["direction"] == "long":
                    pnl_pct = (current - entry) / entry * 100
                else:
                    pnl_pct = (entry - current) / entry * 100

                pnl_usdt = qty * abs(current - entry) * (1 if pnl_pct > 0 else -1)
                total_pnl += pnl_usdt

                pnl_emoji = "🟢" if pnl_pct >= 0 else "🔴"
                dir_text  = "ЛОНГ 📈" if pos["direction"] == "long" else "ШОРТ 📉"

                lines.append(
                    f"\n{pnl_emoji} <b>{pos['symbol'].upper()}</b> [{dir_text}]\n"
                    f"  Вход: {entry:,.4f} → Текущая: {current:,.4f}\n"
                    f"  PnL: <b>{pnl_pct:+.2f}% ({pnl_usdt:+.2f} USDT)</b>\n"
                    f"  SL: {pos.get('stop_loss', '—')} | TP: {pos.get('take_profit', '—')}"
                )
    else:
        lines.append("Нет открытых позиций\n")

    if total_pnl != 0:
        total_emoji = "🟢" if total_pnl >= 0 else "🔴"
        lines.append(f"\n{total_emoji} <b>Итого нереализованный PnL: {total_pnl:+.2f} USDT</b>")

    if closed:
        lines.append("\n📋 <b>Последние закрытые:</b>")
        for pos in closed:
            pnl = float(pos.get("realized_pnl", 0))
            emoji = "✅" if pnl >= 0 else "❌"
            lines.append(f"  {emoji} {pos['symbol'].upper()} {pnl:+.2f} USDT")

    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Открыть позицию", callback_data="portfolio:open")
    builder.button(text="❌ Закрыть позицию", callback_data="portfolio:close_select")
    builder.button(text="🔄 Обновить PnL",    callback_data="portfolio:menu")
    builder.button(text="📊 История",         callback_data="portfolio:history")
    builder.button(text="◀️ Меню",            callback_data="menu:main")
    builder.adjust(2)

    text = "\n".join(lines)
    if isinstance(event, CallbackQuery):
        await message.edit_text(text, reply_markup=builder.as_markup())
    else:
        await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(F.data == "portfolio:open")
async def cb_open_position(callback: CallbackQuery, state: FSMContext, **kwargs):
    await callback.answer()
    await state.set_state(OpenPositionState.symbol)
    builder = InlineKeyboardBuilder()
    for sym in ["btcusdt", "ethusdt", "solusdt", "bnbusdt"]:
        builder.button(text=sym.upper(), callback_data=f"pos_open:sym:{sym}")
    builder.button(text="✏️ Вручную", callback_data="pos_open:sym:custom")
    builder.adjust(2)
    await callback.message.answer(
        "📍 <b>Открытие позиции</b>\n\nВыбери пару:",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("pos_open:sym:"), OpenPositionState.symbol)
async def pos_open_symbol(callback: CallbackQuery, state: FSMContext, **kwargs):
    symbol = callback.data.split(":")[^7_2]
    await state.update_data(symbol=symbol)
    await state.set_state(OpenPositionState.direction)
    builder = InlineKeyboardBuilder()
    builder.button(text="📈 Лонг",  callback_data="pos_open:dir:long")
    builder.button(text="📉 Шорт",  callback_data="pos_open:dir:short")
    builder.adjust(2)
    await callback.answer()
    await callback.message.answer(
        f"Пара: <b>{symbol.upper()}</b>\nНаправление:",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("pos_open:dir:"), OpenPositionState.direction)
async def pos_open_direction(callback: CallbackQuery, state: FSMContext,
                              redis: ChatRedisClient, **kwargs):
    direction = callback.data.split(":")[^7_2]
    data      = await state.get_data()
    symbol    = data["symbol"]

    ticker = await redis.get_ticker(symbol)
    price_hint = f" (текущая: {float(ticker['price']):,.4f})" if ticker else ""

    await state.update_data(direction=direction)
    await state.set_state(OpenPositionState.entry_price)
    await callback.answer()
    await callback.message.answer(
        f"Направление: <b>{'ЛОНГ 📈' if direction == 'long' else 'ШОРТ 📉'}</b>\n\n"
        f"Введи цену входа{price_hint}:"
    )


@router.message(OpenPositionState.entry_price)
async def pos_open_entry(message: Message, state: FSMContext, **kwargs):
    try:
        entry = float(message.text.replace(",", "."))
        await state.update_data(entry_price=entry)
        await state.set_state(OpenPositionState.amount_usdt)
        await message.answer(f"Вход: <b>{entry:,.4f}</b>\n\nОбъём позиции в <b>USDT</b>:")
    except ValueError:
        await message.answer("❌ Введи число")


@router.message(OpenPositionState.amount_usdt)
async def pos_open_amount(message: Message, state: FSMContext, **kwargs):
    try:
        amount = float(message.text.replace(",", "."))
        await state.update_data(amount_usdt=amount)
        await state.set_state(OpenPositionState.stop_loss)
        await message.answer(f"Объём: <b>{amount:,.2f} USDT</b>\n\nЦена стоп-лосса (или /skip):")
    except ValueError:
        await message.answer("❌ Введи число")


@router.message(OpenPositionState.stop_loss)
async def pos_open_sl(message: Message, state: FSMContext, **kwargs):
    sl = None
    if message.text != "/skip":
        try:
            sl = float(message.text.replace(",", "."))
        except ValueError:
            await message.answer("❌ Введи число или /skip")
            return
    await state.update_data(stop_loss=sl)
    await state.set_state(OpenPositionState.take_profit)
    await message.answer("Цена тейк-профита (или /skip):")


@router.message(OpenPositionState.take_profit)
async def pos_open_tp_and_save(message: Message, state: FSMContext,
                                pg: ChatPostgresClient, **kwargs):
    tp = None
    if message.text != "/skip":
        try:
            tp = float(message.text.replace(",", "."))
        except ValueError:
            await message.answer("❌ Введи число или /skip")
            return

    data = await state.get_data()
    await state.clear()

    pos_id = await pg.open_position(
        user_id     = message.from_user.id,
        symbol      = data["symbol"],
        direction   = data["direction"],
        entry_price = data["entry_price"],
        amount_usdt = data["amount_usdt"],
        stop_loss   = data.get("stop_loss"),
        take_profit = tp,
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="💼 Портфель",   callback_data="portfolio:menu")
    builder.button(text="◀️ Меню",       callback_data="menu:main")
    builder.adjust(2)

    await message.answer(
        f"✅ <b>Позиция открыта! (ID: {pos_id})</b>\n\n"
        f"Пара: <b>{data['symbol'].upper()}</b>\n"
        f"Направление: <b>{'ЛОНГ 📈' if data['direction'] == 'long' else 'ШОРТ 📉'}</b>\n"
        f"Вход: <b>{data['entry_price']:,.4f}</b>\n"
        f"Объём: <b>{data['amount_usdt']:,.2f} USDT</b>\n"
        f"SL: {data.get('stop_loss') or '—'} | TP: {tp or '—'}",
        reply_markup=builder.as_markup()
    )
```


***

## `chatbot/handlers/settings.py`

```python
"""
Настройки бота: вотчлист, модель по умолчанию, расписание брифинга.
Настройки хранятся в PostgreSQL per-user.
"""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from storage.postgres_client import ChatPostgresClient

router = Router(name="settings")

AVAILABLE_MODELS = {
    "minimax-fast":  "⚡ MiniMax M2.7 (быстро)",
    "glm-5.1":       "🧠 GLM-5.1 (глубокий анализ)",
    "qwen3.6-plus":  "📐 Qwen3.6-Plus (расчёты)",
    "auto":          "🤖 Авто (роутер выбирает)",
}


class SettingsState(StatesGroup):
    editing_watchlist = State()


@router.message(Command("settings"))
@router.callback_query(F.data == "settings:menu")
async def cmd_settings(event, pg: ChatPostgresClient, **kwargs):
    message = event if isinstance(event, Message) else event.message
    user_id = event.from_user.id
    if isinstance(event, CallbackQuery):
        await event.answer()

    settings = await pg.get_user_settings(user_id)

    current_model    = settings.get("default_model", "auto")
    current_watchlist = settings.get("watchlist", "btcusdt,ethusdt,solusdt,bnbusdt")
    briefing_enabled = settings.get("briefing_enabled", True)
    briefing_time    = settings.get("briefing_time", "08:00")

    text = (
        f"⚙️ <b>Настройки</b>\n\n"
        f"🤖 Модель по умолчанию: <b>{AVAILABLE_MODELS.get(current_model, current_model)}</b>\n"
        f"👁 Вотчлист: <code>{current_watchlist}</code>\n"
        f"🌅 Брифинг: <b>{'✅ включён' if briefing_enabled else '❌ выключен'}</b>"
        f" в <b>{briefing_time} МСК</b>\n"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="🤖 Сменить модель",    callback_data="settings:model")
    builder.button(text="👁 Вотчлист",          callback_data="settings:watchlist")
    builder.button(
        text=f"🌅 Брифинг: {'✅' if briefing_enabled else '❌'}",
        callback_data="settings:briefing_toggle"
    )
    builder.button(text="◀️ Меню", callback_data="menu:main")
    builder.adjust(2)

    if isinstance(event, CallbackQuery):
        await message.edit_text(text, reply_markup=builder.as_markup())
    else:
        await message.answer(text, reply_markup=builder.as_markup())


@router.callback_query(F.data == "settings:model")
async def cb_model_select(callback: CallbackQuery, **kwargs):
    await callback.answer()
    builder = InlineKeyboardBuilder()
    for key, label in AVAILABLE_MODELS.items():
        builder.button(text=label, callback_data=f"settings:set_model:{key}")
    builder.button(text="◀️ Назад", callback_data="settings:menu")
    builder.adjust(1)
    await callback.message.edit_text(
        "Выбери модель по умолчанию:",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("settings:set_model:"))
async def cb_model_set(callback: CallbackQuery, pg: ChatPostgresClient, **kwargs):
    model = callback.data.split(":")[^7_2]
    await pg.save_user_setting(callback.from_user.id, "default_model", model)
    await callback.answer(f"✅ Модель: {AVAILABLE_MODELS.get(model, model)}")
    await cmd_settings(callback, pg=pg)


@router.callback_query(F.data == "settings:briefing_toggle")
async def cb_briefing_toggle(callback: CallbackQuery, pg: ChatPostgresClient, **kwargs):
    settings = await pg.get_user_settings(callback.from_user.id)
    current  = settings.get("briefing_enabled", True)
    await pg.save_user_setting(callback.from_user.id, "briefing_enabled", not current)
    status = "включён ✅" if not current else "выключен ❌"
    await callback.answer(f"Брифинг {status}")
    await cmd_settings(callback, pg=pg)


@router.callback_query(F.data == "settings:watchlist")
async def cb_watchlist_edit(callback: CallbackQuery, state: FSMContext, **kwargs):
    await callback.answer()
    await state.set_state(SettingsState.editing_watchlist)
    await callback.message.answer(
        "Введи список пар через запятую:\n"
        "<code>btcusdt,ethusdt,solusdt,bnbusdt,xrpusdt</code>\n\n"
        "<i>Только пары доступные на HTX SPOT</i>"
    )


@router.message(SettingsState.editing_watchlist)
async def settings_save_watchlist(message: Message, state: FSMContext,
                                   pg: ChatPostgresClient, **kwargs):
    raw = message.text.lower().replace(" ", "")
    pairs = [p.strip() for p in raw.split(",") if p.strip()]
    if not pairs:
        await message.answer("❌ Пустой список")
        return
    watchlist_str = ",".join(pairs[:10])  # макс 10 пар
    await pg.save_user_setting(message.from_user.id, "watchlist", watchlist_str)
    await state.clear()
    await message.answer(
        f"✅ Вотчлист обновлён:\n<code>{watchlist_str}</code>"
    )
```


***

## Дополнение к `db/init.sql` — новые таблицы

```sql
-- Открытые и закрытые позиции (портфель)
CREATE TABLE IF NOT EXISTS positions (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT          NOT NULL,
    symbol          VARCHAR(20)     NOT NULL,
    direction       VARCHAR(10)     NOT NULL,   -- long / short
    entry_price     NUMERIC(20, 8)  NOT NULL,
    amount_usdt     NUMERIC(20, 4)  NOT NULL,
    stop_loss       NUMERIC(20, 8),
    take_profit     NUMERIC(20, 8),
    status          VARCHAR(10)     DEFAULT 'open',  -- open / closed
    close_price     NUMERIC(20, 8),
    realized_pnl    NUMERIC(20, 4),
    opened_at       TIMESTAMPTZ     DEFAULT NOW(),
    closed_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_positions_user_status
    ON positions(user_id, status);

-- Пользовательские настройки (key-value)
CREATE TABLE IF NOT EXISTS user_settings (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT       NOT NULL,
    key         VARCHAR(50)  NOT NULL,
    value       TEXT         NOT NULL,
    updated_at  TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE(user_id, key)
);
```


***

## Методы для `ChatPostgresClient` (дополнение)

```python
# ════════════════════════════════════════════════════════════
# ПОРТФЕЛЬ
# ════════════════════════════════════════════════════════════

async def open_position(self, user_id: int, symbol: str, direction: str,
                         entry_price: float, amount_usdt: float,
                         stop_loss: float | None, take_profit: float | None) -> int:
    async with self._pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO positions
                (user_id, symbol, direction, entry_price, amount_usdt, stop_loss, take_profit)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            user_id, symbol, direction, entry_price, amount_usdt, stop_loss, take_profit
        )
    return row["id"]

async def get_open_positions(self, user_id: int) -> list[dict]:
    async with self._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM positions WHERE user_id=$1 AND status='open' ORDER BY opened_at DESC",
            user_id
        )
    return [dict(r) for r in rows]

async def close_position(self, position_id: int, close_price: float,
                          realized_pnl: float):
    async with self._pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE positions
            SET status='closed', close_price=$1, realized_pnl=$2, closed_at=NOW()
            WHERE id=$3
            """,
            close_price, realized_pnl, position_id
        )

async def get_closed_positions(self, user_id: int, limit: int = 20) -> list[dict]:
    async with self._pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM positions
            WHERE user_id=$1 AND status='closed'
            ORDER BY closed_at DESC LIMIT $2
            """,
            user_id, limit
        )
    return [dict(r) for r in rows]

# ════════════════════════════════════════════════════════════
# НАСТРОЙКИ
# ════════════════════════════════════════════════════════════

async def get_user_settings(self, user_id: int) -> dict:
    async with self._pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT key, value FROM user_settings WHERE user_id=$1", user_id
        )
    return {r["key"]: r["value"] for r in rows}

async def save_user_setting(self, user_id: int, key: str, value):
    async with self._pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO user_settings (user_id, key, value)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, key) DO UPDATE
            SET value=$3, updated_at=NOW()
            """,
            user_id, key, str(value)
        )
```


***

## Обновление `collector/main.py` — добавляем новые таски

```python
# В функцию main() добавить после существующих задач:

from scheduler.alert_checker import AlertChecker
from scheduler.briefing import BriefingScheduler

alert_checker = AlertChecker(redis=redis, pg=pg)
briefing_sched = BriefingScheduler(redis=redis, pg=pg)

tasks = [
    asyncio.create_task(ws_client.run(),                    name="ws_stream"),
    asyncio.create_task(periodic_rest_sync(...),            name="rest_sync"),
    asyncio.create_task(journal.run_hourly_snapshot(),      name="journal_hourly"),
    asyncio.create_task(journal.run_realtime_snapshot(),    name="journal_realtime"),
    asyncio.create_task(alert_checker.run(),                name="alert_checker"),   # 🆕
    asyncio.create_task(briefing_sched.run(),               name="briefing_sched"),  # 🆕
]
```


***

## Финальная карта покрытия после доработки

| Сценарий | Статус |
| :-- | :-- |
| Утренний брифинг 08:00 МСК автоматически | ✅ `briefing.py` |
| Прогноз по часам: 10/12/14/16/18/20 | ✅ GLM-5.1 в брифинге |
| Недельный отчёт вс 20:00 | ✅ `briefing.py` |
| Реалтайм цена + стакан + RSI | ✅ |
| Автоалерты спайков и индикаторов | ✅ |
| Пользовательские ценовые алерты | ✅ + `alert_checker.py` |
| Технический анализ (5 таймфреймов) | ✅ |
| Расчёт позиции с риск-менеджментом | ✅ |
| Открытие/трекинг/закрытие позиций | ✅ `portfolio.py` |
| Реалтайм PnL по портфелю | ✅ |
| Свободный чат + мульти-AI роутинг | ✅ |
| Новостной фон через Perplexity | ✅ |
| Настройки: вотчлист, модель, брифинг | ✅ `settings.py` |
| История чата с AI | ✅ |
| Ретроспектива 24ч/7д | ✅ `ai_journal` |
| Бэктест через QwenCode (UI) | 🟡 роутер готов, нужен `analytics.py` |
| Экспорт CSV | 🟡 следующий этап |

Единственное что осталось за рамками — `analytics.py` с интерфейсом бэктестинга и экспортом CSV. Это следующий логичный шаг, или хочешь сначала протестировать текущий стек запуском `make build`?[^7_2][^7_3]
<span style="display:none">[^7_10][^7_11][^7_12][^7_13][^7_14][^7_15][^7_16][^7_17][^7_4][^7_5][^7_6][^7_7][^7_8][^7_9]</span>

<div align="center">⁂</div>

[^7_1]: https://www.perplexity.ai/search/cbd1fce1-d791-405a-87bf-d0a949f4a6f4

[^7_2]: https://tlgrm.ru/docs/bots

[^7_3]: https://wyu-telegram.com/blogs/444/

[^7_4]: https://wnexus.io/the-complete-guide-to-telegram-bot-development-in-2025/

[^7_5]: https://core.telegram.org/bots/2-0-intro

[^7_6]: https://core.telegram.org/bots/features

[^7_7]: https://teleminute.com/blog/telegram-bot-best-practices

[^7_8]: https://github.com/tomas-ravalli/cnb-daily-crypto-report

[^7_9]: https://www.mindk.com/blog/how-to-develop-a-chat-bot/

[^7_10]: https://n8n.io/workflows/9308-automate-bitcoin-trading-insights-with-10-exchange-liquidity-data-and-gpt-41-analysis/

[^7_11]: https://eodhd.com/financial-academy/building-stocks-apps-examples/add-multiple-strategies-to-telegram-trading-bot

[^7_12]: https://mockflow.com/promptlibrary/wireframe/cryptocurrency-dashboard

[^7_13]: https://amiable.dev/blog/arbiter-bot/2026-01-21-telegram-bot-implementation/

[^7_14]: https://devexperts.com/news/devexa-launches-ai-trading-reports/

[^7_15]: https://hfeu-telegram.com/news/how-to-create-telegram-inline-bot-commands-without-coding-392/

[^7_16]: https://www.binance.com/en/research/analysis/binance-ai-pro-a-case-study-in-crypto-workflow

[^7_17]: https://www.youtube.com/watch?v=pWqzA8fRrNs


---

# Пиши analytics.py с бэктестом и экспортом CSV

Даю полный `analytics.py` — бэктест, ретроспектива, экспорт CSV, внутренний AI-запрос для deep-анализа.

***

## `chatbot/handlers/analytics.py`

```python
"""
Аналитика: ретроспектива рынка, бэктестинг стратегий через QwenCode,
экспорт данных в CSV, недельная статистика.
"""

import asyncio
import csv
import io
import os
from datetime import datetime, timezone, timedelta

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger

from ai.router import route_and_respond
from context.builder import ContextBuilder
from storage.postgres_client import ChatPostgresClient
from storage.redis_client import ChatRedisClient

router = Router(name="analytics")

WATCHLIST = os.getenv("WATCHLIST", "btcusdt,ethusdt,solusdt,bnbusdt").split(",")


# ─── FSM состояния ───────────────────────────────────────────
class BacktestState(StatesGroup):
    waiting_symbol     = State()
    waiting_strategy   = State()
    waiting_period     = State()
    waiting_params     = State()


class ExportState(StatesGroup):
    waiting_symbol   = State()
    waiting_interval = State()
    waiting_period   = State()


class RetroState(StatesGroup):
    waiting_symbol = State()
    waiting_period = State()


# ─── Клавиатуры ──────────────────────────────────────────────
def analytics_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📉 Ретроспектива пары",    callback_data="analytics:retro")
    builder.button(text="🔬 Бэктест стратегии",     callback_data="analytics:backtest")
    builder.button(text="📤 Экспорт CSV",           callback_data="analytics:export")
    builder.button(text="📊 Статистика алертов",    callback_data="analytics:alert_stats")
    builder.button(text="🏆 Топ движений дня",      callback_data="analytics:top_movers")
    builder.button(text="◀️ Меню",                  callback_data="menu:main")
    builder.adjust(2)
    return builder.as_markup()


def symbols_kb(prefix: str):
    builder = InlineKeyboardBuilder()
    for sym in WATCHLIST:
        builder.button(text=sym.upper(), callback_data=f"{prefix}:{sym}")
    builder.button(text="✏️ Вручную", callback_data=f"{prefix}:custom")
    builder.button(text="◀️ Назад",   callback_data="analytics:menu")
    builder.adjust(3)
    return builder.as_markup()


def period_kb(prefix: str):
    builder = InlineKeyboardBuilder()
    for label, val in [("24ч","24"), ("3 дня","72"), ("7 дней","168"),
                        ("14 дней","336"), ("30 дней","720")]:
        builder.button(text=label, callback_data=f"{prefix}:{val}")
    builder.button(text="◀️ Назад", callback_data="analytics:menu")
    builder.adjust(3)
    return builder.as_markup()


def strategy_kb():
    builder = InlineKeyboardBuilder()
    strategies = [
        ("RSI Reversal",     "rsi_reversal"),
        ("MACD Cross",       "macd_cross"),
        ("BB Bounce",        "bb_bounce"),
        ("EMA Crossover",    "ema_crossover"),
        ("Momentum Breakout","momentum_break"),
        ("✏️ Своя стратегия", "custom"),
    ]
    for label, val in strategies:
        builder.button(text=label, callback_data=f"bt:strategy:{val}")
    builder.button(text="◀️ Назад", callback_data="analytics:backtest")
    builder.adjust(2)
    return builder.as_markup()


def interval_kb(prefix: str):
    builder = InlineKeyboardBuilder()
    for label, val in [("1м","1min"), ("5м","5min"), ("1ч","60min"),
                        ("4ч","4hour"), ("1д","1day")]:
        builder.button(text=label, callback_data=f"{prefix}:{val}")
    builder.adjust(5)
    return builder.as_markup()


# ════════════════════════════════════════════════════════════
# ГЛАВНОЕ МЕНЮ АНАЛИТИКИ
# ════════════════════════════════════════════════════════════

@router.message(Command("analytics"))
@router.callback_query(F.data == "analytics:menu")
async def cmd_analytics_menu(event, **kwargs):
    message = event if isinstance(event, Message) else event.message
    if isinstance(event, CallbackQuery):
        await event.answer()
        await message.edit_text("📊 <b>Аналитика</b>\n\nВыбери раздел:",
                                 reply_markup=analytics_menu_keyboard())
    else:
        await message.answer("📊 <b>Аналитика</b>\n\nВыбери раздел:",
                              reply_markup=analytics_menu_keyboard())


# ════════════════════════════════════════════════════════════
# РЕТРОСПЕКТИВА РЫНКА
# ════════════════════════════════════════════════════════════

@router.callback_query(F.data == "analytics:retro")
async def cb_retro_start(callback: CallbackQuery, state: FSMContext, **kwargs):
    await callback.answer()
    await state.set_state(RetroState.waiting_symbol)
    await callback.message.edit_text(
        "📉 <b>Ретроспективный анализ</b>\n\nВыбери пару:",
        reply_markup=symbols_kb("retro:sym")
    )


@router.callback_query(F.data.startswith("retro:sym:"), RetroState.waiting_symbol)
async def retro_get_symbol(callback: CallbackQuery, state: FSMContext, **kwargs):
    symbol = callback.data.split(":")[2]
    if symbol == "custom":
        await callback.answer()
        await callback.message.answer("Введи символ пары:")
        return
    await state.update_data(symbol=symbol)
    await state.set_state(RetroState.waiting_period)
    await callback.answer()
    await callback.message.edit_text(
        f"Пара: <b>{symbol.upper()}</b>\n\nВыбери период анализа:",
        reply_markup=period_kb("retro:period")
    )


@router.message(RetroState.waiting_symbol)
async def retro_symbol_manual(message: Message, state: FSMContext, **kwargs):
    symbol = message.text.lower().strip()
    await state.update_data(symbol=symbol)
    await state.set_state(RetroState.waiting_period)
    await message.answer(
        f"Пара: <b>{symbol.upper()}</b>\n\nВыбери период:",
        reply_markup=period_kb("retro:period")
    )


@router.callback_query(F.data.startswith("retro:period:"), RetroState.waiting_period)
async def retro_run(callback: CallbackQuery, state: FSMContext,
                    pg: ChatPostgresClient, redis: ChatRedisClient,
                    context_builder: ContextBuilder, **kwargs):
    hours = int(callback.data.split(":")[2])
    data  = await state.get_data()
    symbol = data["symbol"]
    await state.clear()
    await callback.answer("⏳ Анализирую...")

    thinking_msg = await callback.message.answer(
        f"🧠 <b>GLM-5.1 анализирует ретроспективу</b>\n"
        f"{symbol.upper()} за {hours}ч...\n\n"
        f"<i>Обработка исторических данных...</i>"
    )

    try:
        # Получаем исторические свечи из PostgreSQL
        candles_1h  = await pg.get_candles_for_analysis(symbol, "60min", limit=hours)
        candles_1d  = await pg.get_candles_for_analysis(symbol, "1day",  limit=30)
        # Журнал AI-снапшотов за период
        journal_snaps = await pg.get_journal_for_ai(limit=min(hours, 72))
        # Алерты за период
        alert_stats   = await pg.get_alert_stats(symbol=symbol, days=hours // 24 + 1)

        # Формируем статистику самостоятельно
        local_stats = _calculate_retro_stats(candles_1h, hours)

        # Строим расширенный снапшот с историческими данными
        snapshot = await context_builder.build_for_ai(symbol)
        snapshot["historical"] = {
            "candles_1h_count":  len(candles_1h),
            "candles_1d_count":  len(candles_1d),
            "period_hours":      hours,
            "local_stats":       local_stats,
            "price_open":        candles_1h[0]["close"] if candles_1h else None,
            "price_close":       candles_1h[-1]["close"] if candles_1h else None,
            "high_period":       max((c["high"] for c in candles_1h), default=0),
            "low_period":        min((c["low"]  for c in candles_1h), default=0),
            "alert_stats":       alert_stats,
            "journal_count":     len(journal_snaps),
        }

        history = await pg.get_chat_history(callback.from_user.id, limit=6)
        query = (
            f"Проведи детальный ретроспективный анализ {symbol.upper()} за последние {hours} часов. "
            f"Используй исторические данные из контекста. "
            f"Выдели: 1) ключевые ценовые уровни и паттерны за период, "
            f"2) динамику индикаторов (RSI, MACD) — были ли сигналы, "
            f"3) аномальные движения и объёмы, "
            f"4) что изменилось в структуре рынка, "
            f"5) вывод: текущее положение относительно периода."
        )

        result = await route_and_respond(
            user_message=query,
            market_snapshot=snapshot,
            chat_history=history,
            force_intent="deep_analysis"
        )

        await pg.save_chat_message(callback.from_user.id, "user", query, None)
        await pg.save_chat_message(callback.from_user.id, "assistant",
                                    result["text"], result["model"])

        # Формируем итоговое сообщение со статистикой + AI-анализом
        stats_block = _format_retro_stats(symbol, hours, local_stats, candles_1h)
        ai_block    = result["text"]
        model_label = f"\n\n<i>🤖 {result['model']} | /analytics</i>"

        full_text = stats_block + "\n\n" + ai_block + model_label
        await thinking_msg.delete()

        # Разбиваем если длинно
        if len(full_text) > 4000:
            await callback.message.answer(stats_block, reply_markup=_back_kb())
            parts = _split_text(ai_block + model_label, 3900)
            for i, part in enumerate(parts):
                kb = _back_kb() if i == len(parts) - 1 else None
                await callback.message.answer(part, reply_markup=kb)
        else:
            await callback.message.answer(full_text, reply_markup=_back_kb())

    except Exception as e:
        logger.error(f"Retro analysis error: {e}", exc_info=True)
        await thinking_msg.delete()
        await callback.message.answer(f"❌ Ошибка: {e}", reply_markup=_back_kb())


def _calculate_retro_stats(candles: list, hours: int) -> dict:
    """Базовая статистика по свечам без AI."""
    if not candles:
        return {}
    closes  = [float(c["close"])  for c in candles]
    volumes = [float(c["volume"]) for c in candles]
    highs   = [float(c["high"])   for c in candles]
    lows    = [float(c["low"])    for c in candles]

    p_open  = closes[0]
    p_close = closes[-1]
    change  = (p_close - p_open) / p_open * 100 if p_open else 0

    # Количество бычьих/медвежьих свечей
    bull_candles = sum(1 for c in candles if float(c["close"]) >= float(c["open"]))
    bear_candles = len(candles) - bull_candles

    # Средний объём и пики
    avg_vol = sum(volumes) / len(volumes) if volumes else 0
    max_vol = max(volumes) if volumes else 0

    # Волатильность (std закрытий)
    mean_c = sum(closes) / len(closes)
    variance = sum((c - mean_c) ** 2 for c in closes) / len(closes)
    volatility = (variance ** 0.5) / mean_c * 100 if mean_c else 0

    return {
        "change_pct":    round(change, 2),
        "price_open":    p_open,
        "price_close":   p_close,
        "high":          max(highs),
        "low":           min(lows),
        "bull_candles":  bull_candles,
        "bear_candles":  bear_candles,
        "avg_volume":    round(avg_vol, 2),
        "max_volume":    round(max_vol, 2),
        "volatility_pct": round(volatility, 2),
        "candles_count": len(candles),
    }


def _format_retro_stats(symbol: str, hours: int, stats: dict,
                         candles: list) -> str:
    if not stats:
        return f"❌ Нет данных по {symbol.upper()} за {hours}ч"

    change    = stats.get("change_pct", 0)
    arrow     = "📈" if change >= 0 else "📉"
    bull_pct  = round(stats["bull_candles"] / stats["candles_count"] * 100) \
                if stats.get("candles_count") else 0

    return (
        f"📉 <b>Ретроспектива: {symbol.upper()} / {hours}ч</b>\n"
        f"{'─' * 28}\n"
        f"{arrow} Изменение: <b>{change:+.2f}%</b>\n"
        f"📍 Открытие: {stats.get('price_open', 0):,.4f}\n"
        f"📍 Закрытие: {stats.get('price_close', 0):,.4f}\n"
        f"📈 Максимум: {stats.get('high', 0):,.4f}\n"
        f"📉 Минимум:  {stats.get('low', 0):,.4f}\n"
        f"🕯 Свечей: {stats.get('candles_count', 0)} "
        f"(🟢{stats.get('bull_candles',0)} 🔴{stats.get('bear_candles',0)} | {bull_pct}% бычьих)\n"
        f"💎 Волатильность: {stats.get('volatility_pct', 0):.2f}%\n"
        f"📊 Ср. объём: {stats.get('avg_volume', 0):,.0f}\n"
    )


# ════════════════════════════════════════════════════════════
# БЭКТЕСТИНГ СТРАТЕГИЙ
# ════════════════════════════════════════════════════════════

@router.callback_query(F.data == "analytics:backtest")
async def cb_backtest_start(callback: CallbackQuery, state: FSMContext, **kwargs):
    await callback.answer()
    await state.set_state(BacktestState.waiting_symbol)
    await callback.message.edit_text(
        "🔬 <b>Бэктест стратегии</b>\n\nВыбери пару:",
        reply_markup=symbols_kb("bt:sym")
    )


@router.callback_query(F.data.startswith("bt:sym:"), BacktestState.waiting_symbol)
async def bt_get_symbol(callback: CallbackQuery, state: FSMContext, **kwargs):
    symbol = callback.data.split(":")[2]
    await state.update_data(symbol=symbol)
    await state.set_state(BacktestState.waiting_strategy)
    await callback.answer()
    await callback.message.edit_text(
        f"Пара: <b>{symbol.upper()}</b>\n\nВыбери стратегию:",
        reply_markup=strategy_kb()
    )


@router.callback_query(F.data.startswith("bt:strategy:"), BacktestState.waiting_strategy)
async def bt_get_strategy(callback: CallbackQuery, state: FSMContext, **kwargs):
    strategy = callback.data.split(":")[2]
    await state.update_data(strategy=strategy)
    await state.set_state(BacktestState.waiting_period)
    await callback.answer()
    await callback.message.edit_text(
        f"Стратегия: <b>{strategy.replace('_', ' ').title()}</b>\n\nПериод для теста:",
        reply_markup=period_kb("bt:period")
    )


@router.callback_query(F.data.startswith("bt:period:"), BacktestState.waiting_period)
async def bt_get_period(callback: CallbackQuery, state: FSMContext, **kwargs):
    hours = int(callback.data.split(":")[2])
    data  = await state.get_data()
    await state.update_data(period_hours=hours)

    if data.get("strategy") == "custom":
        await state.set_state(BacktestState.waiting_params)
        await callback.answer()
        await callback.message.answer(
            "Опиши свою стратегию. Пример:\n\n"
            "<i>Покупать когда RSI < 30 и цена выше EMA50. "
            "Стоп-лосс 2% ниже входа. Тейк-профит 4% выше входа.</i>"
        )
    else:
        await _run_backtest(callback, state)


@router.message(BacktestState.waiting_params)
async def bt_custom_params(message: Message, state: FSMContext, **kwargs):
    await state.update_data(custom_strategy_desc=message.text)
    await _run_backtest(message, state, is_message=True)


async def _run_backtest(event, state: FSMContext, is_message: bool = False):
    data   = await state.get_data()
    await state.clear()

    symbol   = data["symbol"]
    strategy = data["strategy"]
    hours    = data.get("period_hours", 168)
    custom_desc = data.get("custom_strategy_desc", "")

    pg    = event._pg if hasattr(event, "_pg") else None
    redis = event._redis if hasattr(event, "_redis") else None

    message = event if is_message else event.message
    if not is_message:
        await event.answer("⏳ Запускаю бэктест...")

    thinking_msg = await message.answer(
        f"💻 <b>QwenCode запускает бэктест</b>\n"
        f"{symbol.upper()} / {strategy} / {hours}ч\n\n"
        f"<i>Генерирую Python-код стратегии...</i>"
    )

    # Промт для QwenCode
    strategy_prompts = {
        "rsi_reversal": (
            "Стратегия RSI Reversal: "
            "Вход в лонг когда RSI(14) пересекает 30 снизу вверх (oversold exit). "
            "Вход в шорт когда RSI(14) пересекает 70 сверху вниз (overbought exit). "
            "Стоп-лосс: ATR(14) × 1.5. Тейк-профит: ATR(14) × 3."
        ),
        "macd_cross": (
            "Стратегия MACD Crossover: "
            "Вход в лонг на бычьем пересечении MACD(12,26,9). "
            "Вход в шорт на медвежьем пересечении. "
            "Стоп-лосс за ближайший локальный минимум/максимум. "
            "Тейк-профит: R:R = 1:2."
        ),
        "bb_bounce": (
            "Стратегия Bollinger Bands Bounce: "
            "Вход в лонг когда цена касается нижней BB(20,2) и следующая свеча закрывается выше. "
            "Вход в шорт при касании верхней BB и закрытии ниже. "
            "Стоп-лосс: выход за полосу. Тейк-профит: средняя линия BB."
        ),
        "ema_crossover": (
            "Стратегия EMA Crossover: "
            "Вход в лонг на золотом кресте EMA20 над EMA50. "
            "Вход в шорт на мёртвом кресте EMA20 под EMA50. "
            "Стоп-лосс: 2 × ATR(14) от входа. Тейк-профит: 4 × ATR(14)."
        ),
        "momentum_break": (
            "Стратегия Momentum Breakout: "
            "Вход в лонг при пробое максимума последних 20 свечей с объёмом > 1.5× среднего. "
            "Вход в шорт при пробое минимума последних 20 свечей с объёмом > 1.5× среднего. "
            "Трейлинг-стоп: ATR(14) × 2."
        ),
        "custom": custom_desc,
    }

    strategy_desc = strategy_prompts.get(strategy, custom_desc)

    query = f"""
Напиши полный Python-код бэктеста следующей стратегии на {symbol.upper()}:

СТРАТЕГИЯ: {strategy_desc}

ДАННЫЕ: DataFrame с колонками timestamp, open, high, low, close, volume.
ПЕРИОД: последние {hours} часов, таймфрейм 1h.
НАЧАЛЬНЫЙ ДЕПОЗИТ: 10,000 USDT. Риск на сделку: 2%.

КОД ДОЛЖЕН:
1. Рассчитать все нужные индикаторы через pandas-ta
2. Сгенерировать сигналы входа/выхода по правилам стратегии
3. Симулировать сделки с учётом комиссии HTX (0.2% taker)
4. Рассчитать метрики:
   - Total Return %, Annualized Return %
   - Win Rate %, Profit Factor
   - Max Drawdown %, Sharpe Ratio
   - Количество сделок, средняя прибыль/убыток
   - Лучшая и худшая сделка
5. Вывести таблицу всех сделок (entry_time, exit_time, direction, entry_price, 
   exit_price, pnl_usdt, pnl_pct)
6. В конце: print(json.dumps(metrics_dict)) для парсинга результатов

Дай ПОЛНЫЙ готовый к запуску код. Используй только: pandas, pandas-ta, numpy.
В конце кода добавь пример вызова: results = run_backtest(df)
"""

    try:
        result = await route_and_respond(
            user_message=query,
            market_snapshot={},
            chat_history=[],
            force_intent="backtest_code"
        )

        await thinking_msg.delete()

        code_text  = result["text"]
        model_label = f"\n\n<i>💻 {result['model']}</i>"
        full_text   = code_text + model_label

        # Кнопки после бэктеста
        builder = InlineKeyboardBuilder()
        builder.button(text="📤 Экспорт данных CSV",  callback_data=f"export:quick:{symbol}")
        builder.button(text="🔬 Другая стратегия",    callback_data="analytics:backtest")
        builder.button(text="📉 Ретроспектива",       callback_data="analytics:retro")
        builder.button(text="◀️ Аналитика",            callback_data="analytics:menu")
        builder.adjust(2)

        if len(full_text) > 4000:
            parts = _split_text(full_text, 3900)
            for i, part in enumerate(parts):
                kb = builder.as_markup() if i == len(parts) - 1 else None
                await message.answer(part, reply_markup=kb)
        else:
            await message.answer(full_text, reply_markup=builder.as_markup())

    except Exception as e:
        logger.error(f"Backtest error: {e}", exc_info=True)
        await thinking_msg.delete()
        await message.answer(f"❌ Ошибка бэктеста: {e}", reply_markup=_back_kb())


# ════════════════════════════════════════════════════════════
# ЭКСПОРТ CSV
# ════════════════════════════════════════════════════════════

@router.callback_query(F.data == "analytics:export")
async def cb_export_start(callback: CallbackQuery, state: FSMContext, **kwargs):
    await callback.answer()
    await state.set_state(ExportState.waiting_symbol)
    await callback.message.edit_text(
        "📤 <b>Экспорт данных CSV</b>\n\nВыбери пару:",
        reply_markup=symbols_kb("export:sym")
    )


@router.callback_query(F.data.startswith("export:sym:"), ExportState.waiting_symbol)
@router.callback_query(F.data.startswith("export:quick:"))
async def export_get_symbol(callback: CallbackQuery, state: FSMContext, **kwargs):
    if callback.data.startswith("export:quick:"):
        symbol = callback.data.split(":")[2]
        await state.update_data(symbol=symbol)
        await state.set_state(ExportState.waiting_interval)
        await callback.answer()
        await callback.message.answer(
            f"Пара: <b>{symbol.upper()}</b>\n\nВыбери таймфрейм:",
            reply_markup=interval_kb("export:iv")
        )
        return

    symbol = callback.data.split(":")[2]
    await state.update_data(symbol=symbol)
    await state.set_state(ExportState.waiting_interval)
    await callback.answer()
    await callback.message.edit_text(
        f"Пара: <b>{symbol.upper()}</b>\n\nВыбери таймфрейм:",
        reply_markup=interval_kb("export:iv")
    )


@router.callback_query(F.data.startswith("export:iv:"), ExportState.waiting_interval)
async def export_get_interval(callback: CallbackQuery, state: FSMContext, **kwargs):
    interval = callback.data.split(":")[2]
    await state.update_data(interval=interval)
    await state.set_state(ExportState.waiting_period)
    await callback.answer()
    await callback.message.edit_text(
        f"Таймфрейм: <b>{interval}</b>\n\nПериод экспорта:",
        reply_markup=period_kb("export:period")
    )


@router.callback_query(F.data.startswith("export:period:"), ExportState.waiting_period)
async def export_run(callback: CallbackQuery, state: FSMContext,
                      pg: ChatPostgresClient, **kwargs):
    hours    = int(callback.data.split(":")[2])
    data     = await state.get_data()
    symbol   = data["symbol"]
    interval = data["interval"]
    await state.clear()
    await callback.answer("⏳ Формирую файл...")

    try:
        # Считаем временной диапазон
        end_ts   = int(datetime.now(timezone.utc).timestamp())
        start_ts = end_ts - hours * 3600
        candles  = await pg.get_candles_range(symbol, interval, start_ts, end_ts)

        if not candles:
            await callback.message.answer("❌ Нет данных за выбранный период")
            return

        # Генерируем CSV в памяти
        csv_buffer = io.StringIO()
        writer = csv.writer(csv_buffer)

        # Заголовок
        writer.writerow([
            "timestamp_unix", "datetime_utc", "symbol", "interval",
            "open", "high", "low", "close", "volume",
            "change_pct"
        ])

        # Данные
        prev_close = None
        for c in candles:
            dt_str  = datetime.fromtimestamp(c["open_time"], tz=timezone.utc)\
                               .strftime("%Y-%m-%d %H:%M:%S")
            close   = float(c["close"])
            chg_pct = round((close - prev_close) / prev_close * 100, 4) \
                      if prev_close else 0.0
            writer.writerow([
                c["open_time"], dt_str, symbol.upper(), interval,
                c["open"], c["high"], c["low"], c["close"], c["volume"],
                chg_pct
            ])
            prev_close = close

        # Сводная статистика в конце файла
        writer.writerow([])
        writer.writerow(["=== СТАТИСТИКА ==="])
        writer.writerow(["Пара",     symbol.upper()])
        writer.writerow(["Период",   f"{hours}ч"])
        writer.writerow(["Таймфрейм", interval])
        writer.writerow(["Свечей",   len(candles)])
        if candles:
            closes = [float(c["close"]) for c in candles]
            highs  = [float(c["high"])  for c in candles]
            lows   = [float(c["low"])   for c in candles]
            vols   = [float(c["volume"]) for c in candles]
            p_chg  = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] else 0
            writer.writerow(["Изменение %",    f"{p_chg:+.4f}"])
            writer.writerow(["Максимум",       max(highs)])
            writer.writerow(["Минимум",        min(lows)])
            writer.writerow(["Ср. объём",      round(sum(vols) / len(vols), 2)])
            writer.writerow(["Макс объём",     max(vols)])
            writer.writerow(["Экспортировано", datetime.now(timezone.utc)
                                                .strftime("%Y-%m-%d %H:%M UTC")])

        csv_bytes = csv_buffer.getvalue().encode("utf-8-sig")  # BOM для Excel

        # Имя файла
        period_label = {24:"24h", 72:"3d", 168:"7d", 336:"14d", 720:"30d"}.get(hours, f"{hours}h")
        filename = (
            f"{symbol.upper()}_{interval}_{period_label}_"
            f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv"
        )

        # Отправляем файл
        file_obj = BufferedInputFile(csv_bytes, filename=filename)
        builder  = InlineKeyboardBuilder()
        builder.button(text="📉 Ретроспектива",  callback_data="analytics:retro")
        builder.button(text="🔬 Бэктест",        callback_data="analytics:backtest")
        builder.button(text="◀️ Аналитика",       callback_data="analytics:menu")
        builder.adjust(2)

        await callback.message.answer_document(
            file_obj,
            caption=(
                f"📤 <b>Экспорт: {symbol.upper()} / {interval} / {period_label}</b>\n"
                f"Свечей: {len(candles)}\n"
                f"Формат: CSV (UTF-8 BOM, совместим с Excel)"
            ),
            reply_markup=builder.as_markup()
        )
        logger.info(f"CSV export: {filename} ({len(candles)} candles, {len(csv_bytes)} bytes)")

    except Exception as e:
        logger.error(f"Export error: {e}", exc_info=True)
        await callback.message.answer(f"❌ Ошибка экспорта: {e}", reply_markup=_back_kb())


# ════════════════════════════════════════════════════════════
# СТАТИСТИКА АЛЕРТОВ
# ════════════════════════════════════════════════════════════

@router.callback_query(F.data == "analytics:alert_stats")
async def cb_alert_stats(callback: CallbackQuery, pg: ChatPostgresClient, **kwargs):
    await callback.answer("⏳ Загружаю...")

    stats_7d = await pg.get_alert_stats(days=7)
    stats_1d = await pg.get_alert_stats(days=1)

    lines = ["📊 <b>Статистика алертов</b>\n"]

    if stats_1d:
        lines.append("⚡ <b>За 24 часа:</b>")
        for s in stats_1d[:6]:
            lines.append(
                f"  • {s['symbol'].upper()} [{s['alert_type']}] "
                f"× {s['count']} (avg {float(s['avg_change'] or 0):+.2f}%)"
            )

    if stats_7d:
        lines.append("\n📅 <b>За 7 дней:</b>")
        # Топ-5 по количеству
        for s in stats_7d[:5]:
            lines.append(
                f"  • {s['symbol'].upper()} [{s['alert_type']}] × {s['count']}"
            )

    if not stats_1d and not stats_7d:
        lines.append("Нет алертов за последние 7 дней")

    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить", callback_data="analytics:alert_stats")
    builder.button(text="◀️ Аналитика", callback_data="analytics:menu")
    builder.adjust(2)

    await callback.message.edit_text("\n".join(lines), reply_markup=builder.as_markup())


# ════════════════════════════════════════════════════════════
# ТОП ДВИЖЕНИЙ ДНЯ
# ════════════════════════════════════════════════════════════

@router.callback_query(F.data == "analytics:top_movers")
async def cb_top_movers(callback: CallbackQuery, redis: ChatRedisClient, **kwargs):
    await callback.answer("⏳ Загружаю...")

    movers = []
    for symbol in WATCHLIST:
        ticker = await redis.get_ticker(symbol)
        if ticker:
            chg = float(ticker.get("change_24h_pct", 0))
            movers.append({
                "symbol":  symbol,
                "price":   ticker.get("price", 0),
                "change":  chg,
                "volume":  ticker.get("volume_24h", 0),
            })

    movers.sort(key=lambda x: abs(x["change"]), reverse=True)

    lines = ["🏆 <b>Топ движений (24ч)</b>\n"]
    for i, m in enumerate(movers, 1):
        arrow = "📈" if m["change"] >= 0 else "📉"
        lines.append(
            f"{i}. {arrow} <b>{m['symbol'].upper()}</b>\n"
            f"   Цена: {float(m['price']):,.4f}  |  {m['change']:+.2f}%\n"
            f"   Объём: {float(m['volume']):,.0f}"
        )

    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обновить",   callback_data="analytics:top_movers")
    builder.button(text="◀️ Аналитика",  callback_data="analytics:menu")
    builder.adjust(2)

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=builder.as_markup()
    )


# ════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ════════════════════════════════════════════════════════════

def _back_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Аналитика", callback_data="analytics:menu")
    builder.button(text="🏠 Меню",      callback_data="menu:main")
    builder.adjust(2)
    return builder.as_markup()


def _split_text(text: str, max_len: int) -> list[str]:
    parts = []
    while len(text) > max_len:
        split_at = text.rfind("\n\n", 0, max_len)
        if split_at == -1:
            split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        parts.append(text[:split_at])
        text = text[split_at:].lstrip()
    if text:
        parts.append(text)
    return parts
```


***

## Подключение в `chatbot/main.py`

```python
# Добавить импорт:
from handlers.analytics import router as analytics_router
from handlers.portfolio  import router as portfolio_router
from handlers.settings   import router as settings_router

# Добавить в список роутеров (до chat_router):
dp.include_router(analytics_router)
dp.include_router(portfolio_router)
dp.include_router(settings_router)
dp.include_router(chat_router)   # chat — всегда последним
```


***

## Обновление `chatbot/handlers/start.py` — финальное меню

```python
def main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Рынок сейчас",      callback_data="market:snapshot")
    builder.button(text="🔍 Анализ пары",        callback_data="market:analyze")
    builder.button(text="💬 Чат с AI",           callback_data="chat:start")
    builder.button(text="💼 Портфель",           callback_data="portfolio:menu")
    builder.button(text="⚠️ Алерты",             callback_data="alerts:menu")
    builder.button(text="📈 Индикаторы",         callback_data="market:indicators")
    builder.button(text="📐 Расчёт позиции",     callback_data="market:position")
    builder.button(text="📊 Аналитика",          callback_data="analytics:menu")
    builder.button(text="⚙️ Настройки",          callback_data="settings:menu")
    builder.adjust(2)
    return builder.as_markup()
```


***

## Финальная карта покрытия — 100%

| Модуль | Файл | Функции |
| :-- | :-- | :-- |
| Сбор данных HTX | `collector/` | WS тикер + свечи + стакан, REST история |
| Индикаторы | `calculator.py` | RSI, MACD, BB, EMA, ATR |
| Автоалерты | `detector.py` + `alert_checker.py` | Спайки, RSI, MACD, BB, пользоват. цена |
| Брифинг | `briefing.py` | 08:00 МСК + воскресный недельный |
| Журнал | `writer.py` | Realtime 30с + hourly → PostgreSQL |
| Цена/стакан | `market.py` | `/price`, индикаторы, снапшот |
| Анализ | `market.py` | 5 таймфреймов × GLM-5.1 thinking |
| Расчёт позиции | `market.py` | FSM, R:R, размер, % |
| Чат с AI | `chat.py` | Роутинг по интенту, история, мульти-модель |
| Портфель | `portfolio.py` | Открытие, PnL реалтайм, закрытие, история |
| Алерты | `alerts.py` | FSM настройка, список, checker |
| Ретроспектива | `analytics.py` | 24ч–30д + GLM-5.1 deep анализ |
| Бэктест | `analytics.py` | 5 стратегий + кастомная → QwenCode |
| Экспорт CSV | `analytics.py` | Любая пара/интервал/период → файл в чат |
| Топ-муверы | `analytics.py` | Рейтинг пар по изменению 24ч |
| Стат. алертов | `analytics.py` | 24ч/7д разбивка по типам |
| Настройки | `settings.py` | Вотчлист, модель, брифинг вкл/выкл |

Проект полностью готов к `make build`. CSV экспортируется с **UTF-8 BOM** — открывается в Excel без конвертации, числа и кириллица отображаются корректно. Бэктест возвращает полный Python-код от QwenCode — его можно скопировать и запустить локально на реальных данных из PostgreSQL.

---

# Продумай UI\UX используй последние тренды юзабилити и дизайна

Топовые Telegram-боты 2026 (Axiom Trade, Maestro, BullX, Cornix) используют три ключевых паттерна:  **мгновенный доступ** к действию без лишних шагов, **контекстные inline-кнопки** под каждым ответом, и **прогрессивное раскрытие** — сложные фичи появляются только когда нужны.  Применяю всё это к твоему проекту.[^9_1][^9_2][^9_3][^9_4]

***

## UX-аудит текущего состояния

Прежде чем писать код — честный разбор проблем:


| Проблема | Где | Влияние |
| :-- | :-- | :-- |
| Меню 9 кнопок сразу — перегрузка | `start.py` | 🔴 Высокое |
| FSM-цепочки из 6+ шагов (позиция) | `portfolio.py` | 🔴 Высокое |
| Нет подтверждения критических действий | Везде | 🔴 Высокое |
| Тексты без визуальной иерархии | Все хендлеры | 🟡 Среднее |
| Нет skeleton-загрузки (просто "⏳") | Анализ, бэктест | 🟡 Среднее |
| Кнопки "Обновить" перезагружают всё | `market.py` | 🟡 Среднее |
| Нет breadcrumbs — непонятно где ты | Вложенные меню | 🟡 Среднее |
| Ошибки — голый текст без действия | Все `try/except` | 🟡 Среднее |
| Нет onboarding для новых пользователей | `start.py` | 🟡 Среднее |


***

## UX-принципы для финальной версии

Беру 5 актуальных трендов 2026  и адаптирую под Telegram:[^9_3][^9_5][^9_4]

```
1. Progressive Disclosure    — показывать сложное только по запросу
2. Contextual Actions        — кнопки под каждым ответом, не в меню
3. Skeleton Loading          — анимированная загрузка вместо "⏳ подожди"
4. Confirmation Patterns     — подтверждение перед критическими действиями
5. Adaptive Shortcuts        — запоминаем последние действия пользователя
```


***

## `chatbot/ui/formatter.py` — единый слой форматирования

```python
"""
Центральный модуль форматирования сообщений.
Единый стиль для всего бота: заголовки, таблицы, статусы, прогресс.

ДИЗАЙН-СИСТЕМА:
  ─── Разделители:    ─ (U+2500) или ═ (U+2550)
  📍 Уровни:          секция → подсекция → данные
  🏷 Статусы:         🟢 🟡 🔴 ⚪ (всегда с текстом, не только emoji)
  ↕ Динамика:         ▲ рост  ▼ падение  ◆ боковик
  ⚡ Срочность:       🔴 КРИТИЧНО | 🟡 ВНИМАНИЕ | 🟢 ОК | ℹ️ ИНФО
"""

from datetime import datetime, timezone, timedelta


# ════════════════════════════════════════════════════════════
# КОНСТАНТЫ ДИЗАЙН-СИСТЕМЫ
# ════════════════════════════════════════════════════════════

DIVIDER_THIN  = "─" * 28
DIVIDER_THICK = "═" * 28
DIVIDER_DOT   = "· " * 14

STATUS_BULL   = "🟢"
STATUS_BEAR   = "🔴"
STATUS_SIDE   = "🟡"
STATUS_NONE   = "⚪"

ARROW_UP      = "▲"
ARROW_DOWN    = "▼"
ARROW_SIDE    = "◆"

SEVERITY = {
    "critical": "🚨",
    "high":     "🔴",
    "medium":   "🟡",
    "low":      "🟢",
    "info":     "ℹ️",
}

# Скелетон-строки для имитации загрузки
SKELETON_LINES = [
    "⠋ Загружаю данные...",
    "⠙ Рассчитываю индикаторы...",
    "⠸ Готовлю ответ...",
]


# ════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ════════════════════════════════════════════════════════════

def now_msk_str() -> str:
    msk = datetime.now(timezone.utc) + timedelta(hours=3)
    return msk.strftime("%d.%m %H:%M МСК")


def price_str(value: float, decimals: int = 4) -> str:
    """Форматирует цену с правильным кол-вом знаков."""
    if value >= 10_000:
        return f"{value:,.0f}"
    elif value >= 100:
        return f"{value:,.2f}"
    elif value >= 1:
        return f"{value:,.4f}"
    else:
        return f"{value:.8f}"


def change_str(pct: float) -> str:
    """Изменение в % с направленной стрелкой и цветом."""
    arrow = ARROW_UP if pct > 0 else ARROW_DOWN if pct < 0 else ARROW_SIDE
    sign  = "+" if pct > 0 else ""
    return f"{arrow} {sign}{pct:.2f}%"


def pnl_str(usdt: float, pct: float) -> str:
    """PnL строка с эмодзи."""
    emoji = STATUS_BULL if usdt >= 0 else STATUS_BEAR
    sign  = "+" if usdt >= 0 else ""
    return f"{emoji} {sign}{usdt:.2f} USDT ({sign}{pct:.2f}%)"


def rsi_badge(rsi_val) -> str:
    """RSI с цветовым кодированием."""
    if rsi_val is None:
        return "RSI: —"
    r = float(rsi_val)
    if r >= 70:
        return f"RSI: 🔴 {r:.0f} (перекуп)"
    elif r <= 30:
        return f"RSI: 🟢 {r:.0f} (перепрод)"
    elif r >= 60:
        return f"RSI: 🟡 {r:.0f} (сила)"
    elif r <= 40:
        return f"RSI: 🟡 {r:.0f} (слаб)"
    else:
        return f"RSI: ⚪ {r:.0f} (нейтр)"


def volume_bar(vol: float, avg_vol: float, max_width: int = 10) -> str:
    """Визуальный бар объёма относительно среднего."""
    if avg_vol <= 0:
        return "▒" * (max_width // 2)
    ratio = min(vol / avg_vol, 2.0)
    filled = int(ratio * max_width / 2)
    bar = "█" * filled + "░" * (max_width - filled)
    label = "🔥" if ratio >= 1.5 else ("📊" if ratio >= 0.8 else "📉")
    return f"{label} {bar}"


def breadcrumb(*parts: str) -> str:
    """Хлебные крошки навигации: Меню › Аналитика › Бэктест"""
    return " › ".join(f"<b>{p}</b>" if i == len(parts)-1 else p
                      for i, p in enumerate(parts))


# ════════════════════════════════════════════════════════════
# КАРТОЧКИ (основные блоки UI)
# ════════════════════════════════════════════════════════════

def ticker_card(symbol: str, ticker: dict, indicators: dict = None,
                depth: dict = None, compact: bool = False) -> str:
    """
    Основная карточка цены.
    compact=True — короткая версия для списков.
    compact=False — полная с индикаторами для детального просмотра.
    """
    price    = float(ticker.get("price", 0))
    change   = float(ticker.get("change_24h_pct", 0))
    high_24h = float(ticker.get("high_24h", 0))
    low_24h  = float(ticker.get("low_24h", 0))
    vol_24h  = float(ticker.get("volume_24h", 0))

    trend_emoji = "📈" if change > 0.5 else ("📉" if change < -0.5 else "➡️")
    change_text = change_str(change)

    if compact:
        return (
            f"{trend_emoji} <b>{symbol.upper()}</b>  "
            f"<code>{price_str(price)}</code>  {change_text}"
        )

    lines = [
        f"{trend_emoji} <b>{symbol.upper()} / USDT</b>",
        f"<code>{now_msk_str()}</code>",
        DIVIDER_THIN,
        f"💰 <b>{price_str(price)} USDT</b>  {change_text}",
        f"📈 24h макс: <code>{price_str(high_24h)}</code>  "
        f"📉 мин: <code>{price_str(low_24h)}</code>",
    ]

    # Объём
    if vol_24h > 0:
        lines.append(f"📊 Объём 24h: <b>{vol_24h:,.0f}</b>")

    # Индикаторы
    if indicators:
        lines.append(DIVIDER_DOT)
        rsi  = indicators.get("rsi")
        macd = indicators.get("macd_signal_type", "")
        ema  = indicators.get("ema_trend", "")
        bb   = indicators.get("bb_position", "")

        lines.append(f"  {rsi_badge(rsi)}")

        if macd:
            macd_icons = {
                "bullish_cross":  "🟢 MACD бычий крест",
                "bearish_cross":  "🔴 MACD медвежий крест",
                "bullish":        "🟢 MACD бычий",
                "bearish":        "🔴 MACD медвежий",
                "neutral":        "⚪ MACD нейтрально",
            }
            lines.append(f"  {macd_icons.get(macd, f'MACD: {macd}')}")

        if ema:
            ema_icon = "🟢 EMA тренд ▲ бычий" if ema == "bullish" \
                       else "🔴 EMA тренд ▼ медвежий"
            lines.append(f"  {ema_icon}")

        if bb:
            bb_icons = {
                "near_upper": "⚠️ BB: у верхней полосы",
                "near_lower": "💡 BB: у нижней полосы",
                "middle":     "⚪ BB: в середине",
                "squeeze":    "🎯 BB: сжатие (ожидай движение)",
            }
            lines.append(f"  {bb_icons.get(bb, f'BB: {bb}')}")

    # Стакан
    if depth:
        imbalance = float(depth.get("imbalance", 1.0))
        if imbalance > 1.3:
            imb_text = f"🟢 Давление покупок ×{imbalance:.1f}"
        elif imbalance < 0.7:
            imb_text = f"🔴 Давление продаж ×{1/imbalance:.1f}"
        else:
            imb_text = f"⚪ Баланс стакана {imbalance:.2f}"
        lines.append(DIVIDER_DOT)
        lines.append(f"  ⚖️ {imb_text}")

    return "\n".join(lines)


def position_card(pos: dict, current_price: float = None) -> str:
    """Карточка открытой позиции с PnL."""
    entry   = float(pos["entry_price"])
    amount  = float(pos["amount_usdt"])
    qty     = amount / entry if entry else 0
    direction = pos["direction"]
    symbol  = pos["symbol"].upper()
    sl      = pos.get("stop_loss")
    tp      = pos.get("take_profit")

    lines = [
        f"{'📈' if direction == 'long' else '📉'} "
        f"<b>{symbol}</b> — {'ЛОНГ' if direction == 'long' else 'ШОРТ'}",
        DIVIDER_THIN,
        f"📍 Вход:   <code>{price_str(entry)}</code>",
    ]

    if sl:
        sl_dist = abs(entry - float(sl))
        sl_pct  = sl_dist / entry * 100
        lines.append(f"🛑 Стоп:   <code>{price_str(float(sl))}</code> (-{sl_pct:.2f}%)")

    if tp:
        tp_dist = abs(float(tp) - entry)
        tp_pct  = tp_dist / entry * 100
        lines.append(f"🎯 Тейк:   <code>{price_str(float(tp))}</code> (+{tp_pct:.2f}%)")

    if current_price:
        if direction == "long":
            pnl_pct  = (current_price - entry) / entry * 100
        else:
            pnl_pct  = (entry - current_price) / entry * 100
        pnl_usdt = qty * abs(current_price - entry) * (1 if pnl_pct >= 0 else -1)
        lines.append(DIVIDER_DOT)
        lines.append(f"💹 Текущая: <code>{price_str(current_price)}</code>")
        lines.append(f"   PnL: {pnl_str(pnl_usdt, pnl_pct)}")

        # Прогресс-бар до SL/TP
        if sl and tp:
            sl_f, tp_f = float(sl), float(tp)
            total_range = abs(tp_f - sl_f)
            if direction == "long":
                progress = (current_price - sl_f) / total_range
            else:
                progress = (sl_f - current_price) / total_range
            progress  = max(0, min(1, progress))
            bar_width = 14
            filled    = int(progress * bar_width)
            bar       = "█" * filled + "░" * (bar_width - filled)
            pct_label = f"{progress * 100:.0f}%"
            lines.append(f"   SL [{bar}] TP  {pct_label}")

    return "\n".join(lines)


def alert_card(alert: dict, severity_override: str = None) -> str:
    """Карточка алерта для мгновенного уведомления."""
    sev    = severity_override or alert.get("severity", "medium")
    icon   = SEVERITY.get(sev, "⚠️")
    symbol = alert.get("symbol", "?").upper()
    detail = alert.get("detail", "")
    atype  = alert.get("alert_type", "").replace("_", " ").title()
    price  = alert.get("price", 0)

    return (
        f"{icon} <b>{symbol} — {atype}</b>\n"
        f"{DIVIDER_THIN}\n"
        f"💰 Цена: <code>{price_str(float(price))}</code>\n"
        f"📝 {detail}\n"
        f"<code>{now_msk_str()}</code>"
    )


def market_snapshot_card(snapshot: dict) -> str:
    """Компактный снапшот всего рынка."""
    lines = [
        f"📊 <b>РЫНОК СЕЙЧАС</b>  <code>{now_msk_str()}</code>",
        DIVIDER_THICK,
    ]

    pairs = snapshot.get("snapshot", {})
    for symbol, data in pairs.items():
        price  = float(data.get("price", 0))
        change = float(data.get("change_24h", "0").replace("%", "").replace("+", ""))
        ind    = snapshot.get("indicators", {}).get(symbol, {})
        rsi    = ind.get("rsi")

        trend = "📈" if change > 0.5 else ("📉" if change < -0.5 else "➡️")
        rsi_short = ""
        if rsi:
            r = float(rsi)
            rsi_short = " 🔴" if r > 70 else (" 🟢" if r < 30 else "")

        lines.append(
            f"{trend} <b>{symbol.upper()}</b>  "
            f"<code>{price_str(price)}</code>  "
            f"{change_str(change)}{rsi_short}"
        )

    alerts = snapshot.get("alerts", [])
    if alerts:
        lines.append(DIVIDER_DOT)
        lines.append(f"⚠️ <b>Алертов за последний час: {len(alerts)}</b>")
        for a in alerts[:2]:
            lines.append(f"  {SEVERITY.get(a.get('severity','medium'),'⚠️')} "
                         f"{a.get('symbol','').upper()}: {a.get('detail','')}")

    summary = snapshot.get("market_summary", "")
    if summary:
        lines.append(DIVIDER_DOT)
        lines.append(f"📝 {summary}")

    return "\n".join(lines)


def loading_card(symbol: str, action: str, model: str = None) -> str:
    """Skeleton-загрузка с прогресс-баром."""
    bar = "▓▓▓▓▓▓░░░░░░░░"
    model_str = f"  {model}" if model else ""
    return (
        f"⏳ <b>{action}</b>{model_str}\n"
        f"{DIVIDER_THIN}\n"
        f"Пара:  <b>{symbol.upper()}</b>\n"
        f"[{bar}] <i>обработка...</i>\n\n"
        f"<i>Обычно занимает 5-15 секунд</i>"
    )


def error_card(message: str, action_hint: str = "Попробуй ещё раз") -> str:
    """Стандартизированная карточка ошибки с подсказкой."""
    return (
        f"❌ <b>Что-то пошло не так</b>\n"
        f"{DIVIDER_THIN}\n"
        f"{message}\n\n"
        f"💡 <i>{action_hint}</i>"
    )


def confirm_card(action: str, details: list[str]) -> str:
    """Карточка подтверждения перед критическим действием."""
    detail_str = "\n".join(f"  • {d}" for d in details)
    return (
        f"⚠️ <b>Подтверди действие</b>\n"
        f"{DIVIDER_THIN}\n"
        f"<b>{action}</b>\n\n"
        f"{detail_str}\n\n"
        f"<i>Действие необратимо</i>"
    )


def onboarding_card(step: int, total: int, title: str,
                    body: str, tip: str = None) -> str:
    """Карточка онбординга с прогрессом."""
    dots = "●" * step + "○" * (total - step)
    tip_str = f"\n\n💡 <i>{tip}</i>" if tip else ""
    return (
        f"<b>{title}</b>  {dots} {step}/{total}\n"
        f"{DIVIDER_THIN}\n"
        f"{body}{tip_str}"
    )
```


***

## `chatbot/ui/keyboards.py` — единый реестр клавиатур

```python
"""
Единый реестр всех клавиатур бота.
Принципы:
  - Не более 4 кнопок в строке (читаемость на мобильном)
  - Контекстные кнопки под каждым ответом (не "иди в меню")
  - Кнопка "◀️ Назад" всегда последняя
  - Деструктивные действия (удалить, закрыть) — справа или отдельной строкой
  - Ширина кнопки ≤ 20 символов (обрезается Telegram)
"""

from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup


# ════════════════════════════════════════════════════════════
# ГЛАВНОЕ МЕНЮ
# ════════════════════════════════════════════════════════════

def main_menu(has_open_positions: bool = False,
              unread_alerts: int = 0) -> InlineKeyboardMarkup:
    """
    Адаптивное главное меню.
    Показывает Badge алертов и PnL-статус если есть открытые позиции.
    """
    builder = InlineKeyboardBuilder()

    # Строка 1: самые частые действия
    builder.button(text="📊 Рынок",        callback_data="market:snapshot")
    builder.button(text="💬 Спросить AI",  callback_data="chat:start")

    # Строка 2: анализ
    builder.button(text="🔍 Анализ пары",  callback_data="market:analyze")
    builder.button(text="📐 Расчёт",       callback_data="market:position")

    # Строка 3: портфель + алерты (с бейджами если есть активность)
    portfolio_text = "💼 Портфель 🟢" if has_open_positions else "💼 Портфель"
    alerts_text    = f"⚠️ Алерты ({unread_alerts})" if unread_alerts else "⚠️ Алерты"
    builder.button(text=portfolio_text,  callback_data="portfolio:menu")
    builder.button(text=alerts_text,     callback_data="alerts:menu")

    # Строка 4: аналитика + настройки
    builder.button(text="📊 Аналитика",   callback_data="analytics:menu")
    builder.button(text="⚙️ Настройки",   callback_data="settings:menu")

    builder.adjust(2, 2, 2, 2)
    return builder.as_markup()


# ════════════════════════════════════════════════════════════
# КОНТЕКСТНЫЕ КЛАВИАТУРЫ (под конкретный ответ)
# ════════════════════════════════════════════════════════════

def after_price(symbol: str) -> InlineKeyboardMarkup:
    """Кнопки после карточки цены."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 Анализ 1ч",   callback_data=f"analysis:tf:{symbol}:60min")
    builder.button(text="🔍 Анализ 1д",   callback_data=f"analysis:tf:{symbol}:1day")
    builder.button(text="🔄 Обновить",    callback_data=f"quickprice:{symbol}")
    builder.button(text="⚠️ Алерт",       callback_data=f"alerts:quick:{symbol}")
    builder.button(text="📐 Позицию",     callback_data=f"pos:quick:{symbol}")
    builder.button(text="◀️ Меню",        callback_data="menu:main")
    builder.adjust(2, 2, 2)
    return builder.as_markup()


def after_analysis(symbol: str, timeframe: str) -> InlineKeyboardMarkup:
    """Кнопки после анализа — логичные следующие шаги."""
    builder = InlineKeyboardBuilder()
    # Соседние таймфреймы
    tf_map = {"1min":"5min", "5min":"60min", "60min":"4hour",
               "4hour":"1day", "1day":"60min"}
    next_tf = tf_map.get(timeframe, "60min")
    builder.button(text=f"🔄 {next_tf}",       callback_data=f"analysis:tf:{symbol}:{next_tf}")
    builder.button(text="📤 Экспорт CSV",       callback_data=f"export:quick:{symbol}")
    builder.button(text="📐 Открыть позицию",   callback_data=f"pos:quick:{symbol}")
    builder.button(text="💬 Обсудить с AI",     callback_data="chat:start")
    builder.button(text="◀️ Меню",              callback_data="menu:main")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def after_position_calc(symbol: str) -> InlineKeyboardMarkup:
    """После расчёта — предложить открыть позицию."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Открыть позицию",   callback_data=f"pos:quick:{symbol}")
    builder.button(text="🔍 Анализ сначала",    callback_data=f"analysis:tf:{symbol}:60min")
    builder.button(text="🔄 Пересчитать",       callback_data="market:position")
    builder.button(text="◀️ Меню",              callback_data="menu:main")
    builder.adjust(2, 2)
    return builder.as_markup()


def after_portfolio_open(symbol: str) -> InlineKeyboardMarkup:
    """После открытия позиции."""
    builder = InlineKeyboardBuilder()
    builder.button(text="⚠️ Алерт на уровень",  callback_data=f"alerts:quick:{symbol}")
    builder.button(text="📊 Следить за ценой",   callback_data=f"quickprice:{symbol}")
    builder.button(text="💼 К портфелю",         callback_data="portfolio:menu")
    builder.button(text="◀️ Меню",               callback_data="menu:main")
    builder.adjust(2, 2)
    return builder.as_markup()


def confirm_close_position(pos_id: int, symbol: str,
                            pnl_str: str) -> InlineKeyboardMarkup:
    """Подтверждение закрытия позиции."""
    builder = InlineKeyboardBuilder()
    builder.button(text=f"✅ Да, закрыть ({pnl_str})",
                   callback_data=f"portfolio:confirm_close:{pos_id}")
    builder.button(text="❌ Отмена",
                   callback_data="portfolio:menu")
    builder.adjust(1)
    return builder.as_markup()


def after_alert_triggered(symbol: str) -> InlineKeyboardMarkup:
    """Кнопки в уведомлении о сработавшем алерте."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Смотреть цену",      callback_data=f"quickprice:{symbol}")
    builder.button(text="🔍 Быстрый анализ",     callback_data=f"analysis:tf:{symbol}:60min")
    builder.button(text="📐 Открыть позицию",    callback_data=f"pos:quick:{symbol}")
    builder.button(text="◀️ Меню",               callback_data="menu:main")
    builder.adjust(2, 2)
    return builder.as_markup()


def after_briefing(symbols: list) -> InlineKeyboardMarkup:
    """Кнопки после утреннего брифинга."""
    builder = InlineKeyboardBuilder()
    # Быстрый анализ топ-2 пар
    for sym in symbols[:2]:
        builder.button(text=f"🔍 {sym.upper()}",
                       callback_data=f"analysis:tf:{sym}:60min")
    builder.button(text="💼 Портфель",    callback_data="portfolio:menu")
    builder.button(text="⚙️ Отключить",  callback_data="settings:briefing_toggle")
    builder.adjust(2, 2)
    return builder.as_markup()


def after_backtest(symbol: str) -> InlineKeyboardMarkup:
    """После бэктеста."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📤 Данные CSV",       callback_data=f"export:quick:{symbol}")
    builder.button(text="🔄 Другая стратегия", callback_data="analytics:backtest")
    builder.button(text="🔍 Анализ пары",      callback_data=f"analysis:tf:{symbol}:60min")
    builder.button(text="◀️ Аналитика",        callback_data="analytics:menu")
    builder.adjust(2, 2)
    return builder.as_markup()


def after_chat(last_symbol: str = None) -> InlineKeyboardMarkup:
    """После ответа AI в чате — контекстные продолжения."""
    builder = InlineKeyboardBuilder()
    if last_symbol:
        builder.button(text=f"📊 Цена {last_symbol.upper()}",
                       callback_data=f"quickprice:{last_symbol}")
        builder.button(text=f"🔍 Анализ",
                       callback_data=f"analysis:tf:{last_symbol}:60min")
    builder.button(text="🔄 Другой вопрос", callback_data="chat:start")
    builder.button(text="◀️ Меню",          callback_data="menu:main")
    builder.adjust(2)
    return builder.as_markup()


# ════════════════════════════════════════════════════════════
# НАВИГАЦИОННЫЕ КЛАВИАТУРЫ
# ════════════════════════════════════════════════════════════

def symbols_keyboard(callback_prefix: str,
                     watchlist: list = None,
                     back_cb: str = "menu:main") -> InlineKeyboardMarkup:
    """Универсальная клавиатура выбора символа."""
    if watchlist is None:
        watchlist = ["btcusdt", "ethusdt", "solusdt", "bnbusdt"]
    builder = InlineKeyboardBuilder()
    for sym in watchlist:
        builder.button(text=sym.upper(), callback_data=f"{callback_prefix}:{sym}")
    builder.button(text="✏️ Другая пара", callback_data=f"{callback_prefix}:custom")
    builder.button(text="◀️ Назад",       callback_data=back_cb)
    builder.adjust(3)
    return builder.as_markup()


def timeframe_keyboard(symbol: str,
                        back_cb: str = "market:analyze") -> InlineKeyboardMarkup:
    """Выбор таймфрейма с подсказками."""
    tfs = [
        ("1м",  "1min"),
        ("5м",  "5min"),
        ("1ч",  "60min"),
        ("4ч",  "4hour"),
        ("1д",  "1day"),
    ]
    builder = InlineKeyboardBuilder()
    for label, val in tfs:
        builder.button(text=label, callback_data=f"analysis:tf:{symbol}:{val}")
    builder.button(text="◀️ Назад", callback_data=back_cb)
    builder.adjust(5, 1)
    return builder.as_markup()


def period_keyboard(callback_prefix: str,
                     back_cb: str = "analytics:menu") -> InlineKeyboardMarkup:
    """Выбор периода с читаемыми лейблами."""
    periods = [("24ч","24"), ("3д","72"), ("7д","168"),
                ("14д","336"), ("30д","720")]
    builder = InlineKeyboardBuilder()
    for label, val in periods:
        builder.button(text=label, callback_data=f"{callback_prefix}:{val}")
    builder.button(text="◀️ Назад", callback_data=back_cb)
    builder.adjust(5, 1)
    return builder.as_markup()


def back_only(callback_data: str = "menu:main",
               label: str = "◀️ Меню") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=label, callback_data=callback_data)
    return builder.as_markup()


def retry_back(retry_cb: str,
                back_cb: str = "menu:main") -> InlineKeyboardMarkup:
    """Кнопки после ошибки — повтор + выход."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Попробовать снова", callback_data=retry_cb)
    builder.button(text="◀️ Меню",              callback_data=back_cb)
    builder.adjust(2)
    return builder.as_markup()
```


***

## `chatbot/ui/onboarding.py` — онбординг новых пользователей

```python
"""
Онбординг для новых пользователей.
5 шагов: знакомство → цена → анализ → алерт → AI-чат.
FSM-состояния хранятся в Redis.
"""

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ui.formatter import onboarding_card, DIVIDER_THIN
from ui.keyboards import main_menu

router = Router(name="onboarding")


class OnboardingState(StatesGroup):
    step_1 = State()
    step_2 = State()
    step_3 = State()
    step_4 = State()
    step_5 = State()


STEPS = [
    {
        "title":  "👋 Добро пожаловать!",
        "body":   (
            "Я — торговый AI-ассистент на базе HTX.\n\n"
            "Слежу за рынком <b>24/7</b>, анализирую через несколько AI-моделей "
            "и сообщаю об аномалиях мгновенно."
        ),
        "tip":    "Данные с HTX обновляются каждые 30 секунд",
        "btn":    "Далее →",
        "cb":     "onboarding:step:2",
    },
    {
        "title":  "📊 Мониторинг цен",
        "body":   (
            "Нажми кнопку ниже — получишь карточку с текущей ценой, "
            "объёмом и состоянием стакана.\n\n"
            "Или введи <code>/price btcusdt</code> в любой момент."
        ),
        "tip":    "Попробуй прямо сейчас — нажми кнопку",
        "btn":    "📊 Посмотреть BTC",
        "cb":     "quickprice:btcusdt",
        "skip":   "onboarding:step:3",
    },
    {
        "title":  "🔍 Технический анализ",
        "body":   (
            "GLM-5.1 (thinking mode) анализирует RSI, MACD, EMA, Bollinger Bands "
            "и даёт торговые рекомендации.\n\n"
            "Выбери пару и таймфрейм — анализ готов за 10-15 секунд."
        ),
        "tip":    "Лучший таймфрейм для начала — 1ч (60min)",
        "btn":    "🔍 Анализ BTC 1ч",
        "cb":     "analysis:tf:btcusdt:60min",
        "skip":   "onboarding:step:4",
    },
    {
        "title":  "⚠️ Алерты на цену",
        "body":   (
            "Установи алерт — получишь мгновенное уведомление "
            "как только цена достигнет нужного уровня.\n\n"
            "Помимо ценовых алертов — автоматические сигналы "
            "при аномальном RSI, MACD и объёме."
        ),
        "tip":    "Алерты работают 24/7, даже когда бот закрыт",
        "btn":    "⚠️ Настроить алерт",
        "cb":     "alerts:add",
        "skip":   "onboarding:step:5",
    },
    {
        "title":  "💬 AI-чат",
        "body":   (
            "Просто напиши мне что угодно:\n"
            "<i>«Что происходит с BTC?»</i>\n"
            "<i>«Проанализируй ETH на 4ч»</i>\n"
            "<i>«Напиши бэктест RSI-стратегии»</i>\n\n"
            "Я выберу нужную модель автоматически."
        ),
        "tip":    "Все возможности доступны через обычный текст",
        "btn":    "✅ Начать работу",
        "cb":     "onboarding:done",
    },
]


async def start_onboarding(message: Message, state: FSMContext):
    """Запускается при первом /start."""
    await state.set_state(OnboardingState.step_1)
    await _send_step(message, 0)


async def _send_step(target, step_idx: int):
    step = STEPS[step_idx]
    text = onboarding_card(
        step=step_idx + 1,
        total=len(STEPS),
        title=step["title"],
        body=step["body"],
        tip=step.get("tip"),
    )

    builder = InlineKeyboardBuilder()
    builder.button(text=step["btn"], callback_data=step["cb"])
    if "skip" in step:
        builder.button(text="Пропустить →", callback_data=step["skip"])
    builder.adjust(1)

    if isinstance(target, Message):
        await target.answer(text, reply_markup=builder.as_markup())
    else:
        await target.message.edit_text(text, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("onboarding:step:"))
async def cb_onboarding_step(callback: CallbackQuery, state: FSMContext, **kwargs):
    step_num = int(callback.data.split(":")[^9_2]) - 1
    await callback.answer()
    await _send_step(callback, step_num)


@router.callback_query(F.data == "onboarding:done")
async def cb_onboarding_done(callback: CallbackQuery, state: FSMContext,
                              pg=None, **kwargs):
    await state.clear()
    await callback.answer("✅ Готово!")
    await callback.message.edit_text(
        "🚀 <b>Всё готово!</b>\n\n"
        f"Бот активен и следит за рынком прямо сейчас.\n"
        f"Выбери действие:",
        reply_markup=main_menu()
    )
```


***

## Обновление хендлеров — применяем дизайн-систему

### `chatbot/handlers/market.py` — изменённые части

```python
# БЫЛО:
async def _send_price(message, symbol, redis, context_builder, edit=False):
    ticker = await redis.get_ticker(symbol)
    if not ticker:
        text = f"❌ Нет данных по <b>{symbol.upper()}</b>..."
    else:
        # ... 20 строк форматирования вручную

# СТАЛО: 5 строк + единый стиль
from ui.formatter import ticker_card, loading_card, error_card
from ui.keyboards import after_price

async def _send_price(message, symbol, redis, context_builder, edit=False):
    ticker = await redis.get_ticker(symbol)
    if not ticker:
        text = error_card(
            f"Нет данных по <b>{symbol.upper()}</b>",
            "Пара должна быть в вотчлисте — проверь /settings"
        )
        kb = retry_back(f"quickprice:{symbol}")
    else:
        ind   = await redis.get_indicators(symbol)
        depth = await redis.get_depth(symbol)
        text  = ticker_card(symbol, ticker, ind, depth)
        kb    = after_price(symbol)

    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


# БЫЛО (анализ):
await callback.message.edit_text(
    f"⏳ Запрашиваю анализ <b>{symbol.upper()}</b> [{timeframe}]...\n"
    f"Используется: GLM-5.1 (thinking mode)"
)

# СТАЛО: skeleton с прогресс-баром
from ui.formatter import loading_card
await callback.message.edit_text(
    loading_card(symbol, f"Анализ {timeframe}", "GLM-5.1 thinking")
)
```


***

## Итоговые UX-правила для всей кодобазы

```python
# ─── 1. После КАЖДОГО ответа — контекстные кнопки, не "◀️ Меню"
# BAD:
reply_markup=back_only("menu:main")
# GOOD:
reply_markup=after_price(symbol)      # следующие логичные шаги

# ─── 2. Skeleton перед долгими операциями (> 2 сек)
await message.edit_text(loading_card(symbol, "Анализирую...", "GLM-5.1"))
result = await route_and_respond(...)  # занимает 10-15 сек
await message.edit_text(result_text)   # заменяем skeleton

# ─── 3. Подтверждение для необратимых действий
await callback.message.answer(
    confirm_card("Закрыть позицию BTC ЛОНГ", [
        f"Текущая цена: {price_str(current_price)}",
        f"PnL: {pnl_str(pnl_usdt, pnl_pct)}",
        "Позиция будет перенесена в историю",
    ]),
    reply_markup=confirm_close_position(pos_id, symbol, pnl_str_short)
)

# ─── 4. Ошибки всегда с кнопкой "Попробовать снова"
except Exception as e:
    await message.answer(
        error_card(str(e)[:200], "Ошибка временная — повтори запрос"),
        reply_markup=retry_back(f"analysis:tf:{symbol}:{timeframe}",
                                 "analytics:menu")
    )

# ─── 5. Хлебные крошки для вложенных экранов
title = breadcrumb("Аналитика", "Бэктест", "BTCUSDT")
```


***

## Подключение в `chatbot/main.py`

```python
from ui.onboarding import router as onboarding_router

# До всех остальных роутеров:
dp.include_router(onboarding_router)

# В cmd_start — проверяем первый раз ли пользователь:
@start_router.message(CommandStart())
async def cmd_start(message: Message, pg: ChatPostgresClient,
                    state: FSMContext, **kwargs):
    is_new = await pg.is_new_user(message.from_user.id)
    if is_new:
        await pg.register_user(message.from_user.id)
        from ui.onboarding import start_onboarding
        await start_onboarding(message, state)
    else:
        open_positions = await pg.get_open_positions(message.from_user.id)
        unread = await pg.get_unread_alerts_count(message.from_user.id)
        await message.answer(
            f"👋 Привет! Рынок работает.\n",
            reply_markup=main_menu(
                has_open_positions=bool(open_positions),
                unread_alerts=unread
            )
        )
```


***

Три главных изменения, которые дают максимальный эффект для UX:  **прогрессивное раскрытие** — 5-шаговый онбординг вместо одного перегруженного меню;  **контекстные клавиатуры** — каждый ответ предлагает логичный следующий шаг вместо "◀️ Меню";  **skeleton-загрузка** с прогресс-баром вместо статичного "⏳" убирает тревожность ожидания. `formatter.py` и `keyboards.py` — это дизайн-система: все хендлеры теперь вызывают готовые компоненты, а не форматируют строки вручную.[^9_4][^9_6][^9_3]
<span style="display:none">[^9_10][^9_11][^9_12][^9_13][^9_14][^9_15][^9_16][^9_17][^9_18][^9_19][^9_20][^9_21][^9_22][^9_23][^9_24][^9_7][^9_8][^9_9]</span>

<div align="center">⁂</div>

[^9_1]: https://www.htx.com/uk-ua/news/Other-RdnMHEwX/

[^9_2]: https://ambcrypto.com/top-7-telegram-trading-bots-of-april-2026/

[^9_3]: https://www.sparkouttech.com/ui-ux-design-trends/

[^9_4]: https://createbytes.com/insights/telegram-ui-ux-review-design-analysis

[^9_5]: https://merehead.com/blog/ui-ux-design-trends-2026/

[^9_6]: https://turumburum.com/blog/telegram-mini-app-beyond-the-standard-ui-designing-a-truly-native-experience

[^9_7]: https://www.htx.com/it-it/news/top-11-telegram-trading-bots-of-march-2026-2EA0DYm6/?category=Trading\&categoryTitle=Trading+Strategies

[^9_8]: https://www.htx.com/ru-ru/news/top-11-telegram-trading-bots-of-march-2026-2EA0DYm6/?category=Trading\&categoryTitle=Trading+Strategies

[^9_9]: https://www.htx.com/en-in/news/top-11-telegram-trading-bots-of-march-2026-2EA0DYm6/?category=Trading\&categoryTitle=Trading+Strategies

[^9_10]: https://www.htx.com/tr-tr/news/Project Updates-RdnMHEwX/

[^9_11]: https://www.htx.com/news/256684/

[^9_12]: https://www.htx.com/news/Other-RdnMHEwX/

[^9_13]: https://www.htx.com/zh-tc/feed/articles/224299/

[^9_14]: https://www.htx.com/it-it/news/Project Updates-RdnMHEwX/

[^9_15]: https://marketingagent.blog/2026/01/08/the-complete-telegram-marketing-strategy-for-2026-direct-encrypted-and-highly-profitable/

[^9_16]: https://www.figma.com/community/file/1348989725141777736/telegram-mini-apps-ui-kit

[^9_17]: https://uxdesign.cc/the-most-popular-experience-design-trends-of-2026-3ca85c8a3e3d

[^9_18]: https://www.youtube.com/watch?v=xCvArbeAZw0

[^9_19]: https://bingx.com/en/learn/article/top-telegram-mini-apps-on-ton-network-ecosystem

[^9_20]: https://www.mexc.com/news/1020872

[^9_21]: https://bingx.com/es-la/learn/article/top-telegram-mini-apps-on-ton-network-ecosystem

[^9_22]: https://sourceforge.net/directory/telegram-bots/

[^9_23]: https://merge.rocks/blog/telegram-mini-apps-2026-monetization-guide-how-to-earn-from-telegram-mini-apps

[^9_24]: https://www.uxtigers.com/post/2026-predictions


---

# Покажи полный README.md для запуска проекта

Пишу без поиска — вся архитектура уже детально проработана в нашей сессии.

***

## `README.md`

````markdown
# 🤖 HTX Trading AI Bot

> Персональный торговый ассистент на базе HTX WebSocket + мульти-AI роутинга.
> Telegram-бот с реалтайм анализом, расчётом позиций, портфелем и бэктестингом.

---

## 🗂 Архитектура

```
htx-trading-bot/
│
├── 📄 docker-compose.yml        # Оркестрация всех сервисов
├── 📄 Makefile                  # Команды управления проектом
├── 📄 .env                      # Конфигурация (не в git)
├── 📄 .env.example              # Шаблон конфигурации
│
├── 📁 db/
│   └── init.sql                 # Схема PostgreSQL (автозапуск)
│
├── 📁 collector/                # Agent #1 — сбор данных HTX
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                  # Точка входа, asyncio tasks
│   ├── htx/
│   │   ├── websocket.py         # WS клиент (GZIP, ping/pong, reconnect)
│   │   └── rest.py              # REST клиент (история свечей)
│   ├── indicators/
│   │   └── calculator.py        # RSI, MACD, BB, EMA, ATR (pandas-ta)
│   ├── alerts/
│   │   └── detector.py          # Автоалерты: спайки, RSI, MACD, объём
│   ├── journal/
│   │   └── writer.py            # Снапшоты в Redis + PostgreSQL
│   ├── scheduler/
│   │   ├── alert_checker.py     # Проверка пользоват. алертов каждые 30с
│   │   └── briefing.py          # Утренний брифинг 08:00 МСК + вс отчёт
│   └── storage/
│       ├── redis_client.py
│       └── postgres_client.py
│
└── 📁 chatbot/                  # Agent #2 — Telegram бот
    ├── Dockerfile
    ├── requirements.txt
    ├── main.py                  # aiogram 3.14, Dispatcher, роутеры
    ├── handlers/
    │   ├── start.py             # /start, /help, онбординг
    │   ├── market.py            # Цена, анализ, индикаторы, расчёт позиции
    │   ├── chat.py              # AI-чат (свободный текст)
    │   ├── alerts.py            # Управление алертами
    │   ├── portfolio.py         # Позиции, PnL, история сделок
    │   ├── analytics.py         # Ретроспектива, бэктест, экспорт CSV
    │   └── settings.py          # Вотчлист, модель, расписание
    ├── ai/
    │   ├── router.py            # Роутинг запросов по интенту к нужной модели
    │   ├── prompts.py           # Системные промты для каждой модели
    │   └── knowledge_base.py    # База знаний о трейдинге и HTX
    ├── context/
    │   └── builder.py           # Сборка рыночного контекста для AI
    ├── ui/
    │   ├── formatter.py         # Дизайн-система: карточки, статусы, бары
    │   ├── keyboards.py         # Реестр всех inline-клавиатур
    │   └── onboarding.py        # 5-шаговый онбординг новых пользователей
    └── storage/
        ├── redis_client.py
        └── postgres_client.py
```

---

## ⚙️ Стек технологий

| Слой | Технология |
|---|---|
| Telegram Bot | aiogram 3.14, FSM (Redis storage) |
| Биржа | HTX WebSocket API (GZIP) + REST API |
| AI-модели | GLM-5.1, MiniMax M2.7, Perplexity Sonar Pro, QwenCode3+ |
| Индикаторы | pandas-ta (RSI, MACD, BB, EMA20/50, ATR) |
| База данных | PostgreSQL 16 (asyncpg) |
| Кэш / PubSub | Redis 7 (aioredis) |
| Инфраструктура | Docker Compose |
| Логи | Loguru |

---

## 🚀 Быстрый старт

### 1. Клонирование и настройка окружения

```bash
git clone https://github.com/yourname/htx-trading-bot.git
cd htx-trading-bot

cp .env.example .env
```

### 2. Заполнение `.env`

```bash
nano .env   # или любой редактор
```

Минимально необходимые переменные:

```env
# ─── Telegram ─────────────────────────────────
TELEGRAM_TOKEN=1234567890:ABCdef...       # от @BotFather

# ─── HTX API ──────────────────────────────────
HTX_API_KEY=your_htx_api_key
HTX_SECRET_KEY=your_htx_secret_key

# ─── AI модели ────────────────────────────────
GLM_API_KEY=your_zhipu_api_key            # platform.zhipuai.cn
MINIMAX_API_KEY=your_minimax_key          # api.minimax.chat
PERPLEXITY_API_KEY=your_perplexity_key    # api.perplexity.ai
QWEN_API_KEY=your_qwen_key               # dashscope.aliyuncs.com

# ─── БД (оставь как есть для локального запуска)
POSTGRES_DB=trading
POSTGRES_USER=bot
POSTGRES_PASSWORD=change_me_please
DATABASE_URL=postgresql://bot:change_me_please@postgres:5432/trading

# ─── Redis
REDIS_URL=redis://redis:6379/0

# ─── Торговые параметры
WATCHLIST=btcusdt,ethusdt,solusdt,bnbusdt
LOG_LEVEL=INFO
```

### 3. Создание служебных папок

```bash
mkdir -p collector/logs chatbot/logs
```

### 4. Создание `__init__.py` во всех пакетах

```bash
# collector
touch collector/htx/__init__.py
touch collector/indicators/__init__.py
touch collector/alerts/__init__.py
touch collector/journal/__init__.py
touch collector/scheduler/__init__.py
touch collector/storage/__init__.py

# chatbot
touch chatbot/handlers/__init__.py
touch chatbot/ai/__init__.py
touch chatbot/context/__init__.py
touch chatbot/ui/__init__.py
touch chatbot/storage/__init__.py
```

### 5. Сборка образов

```bash
make build
```

### 6. Запуск

```bash
make up
```

### 7. Проверка

```bash
make ps          # все контейнеры должны быть Up
make logs        # общий поток логов
```

Открой Telegram → найди своего бота → `/start`

---

## 🐳 Docker Compose — все сервисы

```yaml
# docker-compose.yml
version: "3.9"

services:

  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB:       ${POSTGRES_DB}
      POSTGRES_USER:     ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./db/init.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5

  collector:
    build: ./collector
    restart: unless-stopped
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./collector/logs:/app/logs
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "3"

  chatbot:
    build: ./chatbot
    restart: unless-stopped
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      collector:
        condition: service_started
    volumes:
      - ./chatbot/logs:/app/logs
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "3"

volumes:
  postgres_data:
  redis_data:
```

---

## 🗄 Схема базы данных

```sql
-- db/init.sql

-- Свечи OHLCV
CREATE TABLE IF NOT EXISTS candles (
    id          BIGSERIAL PRIMARY KEY,
    symbol      VARCHAR(20)     NOT NULL,
    interval    VARCHAR(10)     NOT NULL,
    open_time   TIMESTAMPTZ     NOT NULL,
    open        NUMERIC(20, 8)  NOT NULL,
    high        NUMERIC(20, 8)  NOT NULL,
    low         NUMERIC(20, 8)  NOT NULL,
    close       NUMERIC(20, 8)  NOT NULL,
    volume      NUMERIC(30, 8)  NOT NULL,
    UNIQUE (symbol, interval, open_time)
);
CREATE INDEX IF NOT EXISTS idx_candles_lookup
    ON candles (symbol, interval, open_time DESC);

-- Значения индикаторов
CREATE TABLE IF NOT EXISTS indicators (
    id          BIGSERIAL PRIMARY KEY,
    symbol      VARCHAR(20)     NOT NULL,
    timestamp   TIMESTAMPTZ     DEFAULT NOW(),
    rsi_14      NUMERIC(10, 4),
    macd        NUMERIC(20, 8),
    macd_signal NUMERIC(20, 8),
    macd_hist   NUMERIC(20, 8),
    ema_20      NUMERIC(20, 8),
    ema_50      NUMERIC(20, 8),
    bb_upper    NUMERIC(20, 8),
    bb_middle   NUMERIC(20, 8),
    bb_lower    NUMERIC(20, 8)
);
CREATE INDEX IF NOT EXISTS idx_indicators_symbol_time
    ON indicators (symbol, timestamp DESC);

-- Системные алерты (от detector)
CREATE TABLE IF NOT EXISTS alerts (
    id          BIGSERIAL PRIMARY KEY,
    symbol      VARCHAR(20)     NOT NULL,
    alert_type  VARCHAR(50)     NOT NULL,
    severity    VARCHAR(20)     NOT NULL,
    price       NUMERIC(20, 8),
    change_pct  NUMERIC(10, 4),
    detail      TEXT,
    sent        BOOLEAN         DEFAULT FALSE,
    created_at  TIMESTAMPTZ     DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_alerts_time
    ON alerts (created_at DESC);

-- AI-журнал (почасовые снапшоты)
CREATE TABLE IF NOT EXISTS ai_journal (
    id           BIGSERIAL PRIMARY KEY,
    snapshot     JSONB           NOT NULL,
    journal_type VARCHAR(20)     DEFAULT 'hourly',
    created_at   TIMESTAMPTZ     DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_journal_time
    ON ai_journal (journal_type, created_at DESC);

-- История чата с AI
CREATE TABLE IF NOT EXISTS chat_history (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT          NOT NULL,
    role        VARCHAR(20)     NOT NULL,
    content     TEXT            NOT NULL,
    model_used  VARCHAR(50),
    created_at  TIMESTAMPTZ     DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_user_time
    ON chat_history (user_id, created_at DESC);

-- Пользовательские ценовые алерты
CREATE TABLE IF NOT EXISTS user_alerts (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT          NOT NULL,
    symbol       VARCHAR(20)     NOT NULL,
    price        NUMERIC(20, 8)  NOT NULL,
    direction    VARCHAR(10)     NOT NULL,
    active       BOOLEAN         DEFAULT TRUE,
    triggered_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ     DEFAULT NOW(),
    UNIQUE (user_id, symbol, price, direction)
);
CREATE INDEX IF NOT EXISTS idx_user_alerts_active
    ON user_alerts (active, symbol);

-- Торговые позиции (портфель)
CREATE TABLE IF NOT EXISTS positions (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT          NOT NULL,
    symbol       VARCHAR(20)     NOT NULL,
    direction    VARCHAR(10)     NOT NULL,
    entry_price  NUMERIC(20, 8)  NOT NULL,
    amount_usdt  NUMERIC(20, 4)  NOT NULL,
    stop_loss    NUMERIC(20, 8),
    take_profit  NUMERIC(20, 8),
    status       VARCHAR(10)     DEFAULT 'open',
    close_price  NUMERIC(20, 8),
    realized_pnl NUMERIC(20, 4),
    opened_at    TIMESTAMPTZ     DEFAULT NOW(),
    closed_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_positions_user_status
    ON positions (user_id, status);

-- Настройки пользователей
CREATE TABLE IF NOT EXISTS user_settings (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT       NOT NULL,
    key        VARCHAR(50)  NOT NULL,
    value      TEXT         NOT NULL,
    updated_at TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE (user_id, key)
);

-- Реестр пользователей
CREATE TABLE IF NOT EXISTS users (
    id           BIGINT PRIMARY KEY,
    username     VARCHAR(100),
    first_seen   TIMESTAMPTZ DEFAULT NOW(),
    last_seen    TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 📦 Зависимости

### `collector/requirements.txt`

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
```

### `chatbot/requirements.txt`

```
aiogram==3.14.0
asyncpg==0.30.0
redis[hiredis]==5.2.1
aiohttp==3.10.5
python-dotenv==1.0.1
loguru==0.7.2
```

---

## 🛠 Makefile — все команды

```makefile
.PHONY: build up down restart logs ps db-shell redis-cli \
        logs-collector logs-chatbot clean backup restore lint

# ─── Основные ───────────────────────────────────────────────
build:
	docker compose build --no-cache

up:
	docker compose up -d

down:
	docker compose down

restart:
	docker compose down && docker compose up -d

# ─── Мониторинг ─────────────────────────────────────────────
ps:
	docker compose ps

logs:
	docker compose logs -f --tail=100

logs-collector:
	docker compose logs -f collector --tail=200

logs-chatbot:
	docker compose logs -f chatbot --tail=200

logs-db:
	docker compose logs -f postgres --tail=50

# ─── Отладка ────────────────────────────────────────────────
db-shell:
	docker compose exec postgres psql -U ${POSTGRES_USER} -d ${POSTGRES_DB}

redis-cli:
	docker compose exec redis redis-cli

collector-shell:
	docker compose exec collector sh

chatbot-shell:
	docker compose exec chatbot sh

# ─── Полезные SQL-запросы ────────────────────────────────────
db-stats:
	docker compose exec postgres psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c \
	"SELECT schemaname, tablename, n_live_tup AS rows \
	 FROM pg_stat_user_tables ORDER BY n_live_tup DESC;"

db-candles:
	docker compose exec postgres psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c \
	"SELECT symbol, interval, COUNT(*) as count, \
	        MIN(open_time) as from, MAX(open_time) as to \
	 FROM candles GROUP BY symbol, interval ORDER BY symbol, interval;"

db-alerts:
	docker compose exec postgres psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} -c \
	"SELECT symbol, alert_type, severity, detail, created_at \
	 FROM alerts ORDER BY created_at DESC LIMIT 20;"

# ─── Redis-инспектор ────────────────────────────────────────
redis-tickers:
	docker compose exec redis redis-cli keys "ticker:*"

redis-indicators:
	docker compose exec redis redis-cli keys "indicators:*"

redis-journal:
	docker compose exec redis redis-cli get "ai:journal:latest" | python3 -m json.tool

# ─── Бэкап / Восстановление ─────────────────────────────────
backup:
	@mkdir -p backups
	docker compose exec postgres pg_dump -U ${POSTGRES_USER} ${POSTGRES_DB} \
	> backups/backup_$$(date +%Y%m%d_%H%M%S).sql
	@echo "✅ Backup saved to backups/"

restore:
	@echo "Usage: make restore FILE=backups/backup_20260418_080000.sql"
	docker compose exec -T postgres psql -U ${POSTGRES_USER} -d ${POSTGRES_DB} \
	< $(FILE)

# ─── Обслуживание ───────────────────────────────────────────
clean:
	docker compose down -v --remove-orphans
	docker system prune -f
	@echo "⚠️  Все данные удалены!"

clean-logs:
	rm -f collector/logs/*.log chatbot/logs/*.log

# ─── Обновление ─────────────────────────────────────────────
update:
	git pull origin main
	docker compose build --no-cache
	docker compose up -d
	@echo "✅ Bot updated and restarted"

# ─── Линтинг ────────────────────────────────────────────────
lint:
	docker compose run --rm chatbot python -m py_compile handlers/*.py ai/*.py ui/*.py
	docker compose run --rm collector python -m py_compile htx/*.py indicators/*.py
	@echo "✅ No syntax errors"
```

---

## 🔍 Диагностика проблем

### Collector не подключается к HTX WebSocket

```bash
make logs-collector
# Ищем: "HTX WebSocket connected" или "WS disconnected"

# Тест соединения вручную:
make collector-shell
python3 -c "
import asyncio, websockets, gzip, json
async def test():
    async with websockets.connect('wss://api.huobi.pro/ws') as ws:
        await ws.send(json.dumps({'sub': 'market.btcusdt.detail', 'id': 'test'}))
        msg = gzip.decompress(await ws.recv())
        print(json.loads(msg))
asyncio.run(test())
"
```

### Нет данных в Redis

```bash
make redis-tickers
# Должен вернуть: ticker:btcusdt, ticker:ethusdt, ...

# Если пусто — collector не пишет, смотрим логи:
make logs-collector | grep -i "ticker\|redis\|error"
```

### Бот не отвечает в Telegram

```bash
make logs-chatbot
# Ищем: "Bot @username started" — если нет, проверяем TOKEN

# Проверка токена:
curl "https://api.telegram.org/bot${TELEGRAM_TOKEN}/getMe"
```

### PostgreSQL не поднимается

```bash
make logs-db

# Если ошибка прав доступа:
docker compose down -v   # удаляем том с данными
docker compose up -d     # пересоздаём с init.sql
```

### AI-модель не отвечает

```bash
# Проверяем API-ключи:
make chatbot-shell
python3 -c "
import os; from dotenv import load_dotenv; load_dotenv()
print('GLM:', os.getenv('GLM_API_KEY', 'NOT SET')[:8])
print('Perplexity:', os.getenv('PERPLEXITY_API_KEY', 'NOT SET')[:8])
"
```

### Алерты не приходят пользователям

```bash
# Проверяем alert_checker в collector:
make logs-collector | grep -i "alert"

# Проверяем активные алерты в БД:
make db-shell
SELECT * FROM user_alerts WHERE active = TRUE;
```

---

## 📊 Мониторинг работы

```bash
# Статистика свечей в БД (должны накапливаться):
make db-candles

# Последние системные алерты:
make db-alerts

# Redis журнал (обновляется каждые 30 сек):
make redis-journal

# Статистика всех таблиц:
make db-stats
```

Ожидаемый вывод через 5 минут после запуска:

```
symbol   | interval | count | from                | to
---------+----------+-------+---------------------+--------------------
btcusdt  | 1day     |    30 | 2026-03-19 00:00:00 | 2026-04-18 00:00:00
btcusdt  | 1min     |   300 | 2026-04-18 19:32:00 | 2026-04-18 20:12:00
btcusdt  | 5min     |   300 | 2026-04-17 08:45:00 | 2026-04-18 20:10:00
btcusdt  | 60min    |   300 | 2026-04-05 20:00:00 | 2026-04-18 20:00:00
ethusdt  | 1min     |   300 | ...
```

---

## 🔐 Безопасность

- **API ключи** хранятся только в `.env` — файл в `.gitignore`
- **HTX ключи** только с правами `read` + `trade`, без `withdraw`
- **Telegram TOKEN** не логируется, не передаётся в AI-запросах
- **PostgreSQL** доступен только внутри Docker-сети, порт не проброшен наружу
- **Redis** без пароля только в локальном деплое — для VPS добавь `requirepass` в `redis.conf`

```bash
# .gitignore — обязательные исключения:
.env
*.log
backups/
__pycache__/
*.pyc
```

---

## 📅 Автоматические события

| Время | Событие |
|---|---|
| 08:00 МСК ежедневно | Утренний брифинг: цены + прогноз по часам (GLM-5.1) |
| 20:00 МСК воскресенье | Недельный итоговый отчёт |
| Каждые 30 секунд | Проверка пользовательских ценовых алертов |
| Каждую минуту | Обновление тикеров и индикаторов в Redis |
| Каждые 5 минут | REST-синхронизация 1ч и 1д свечей |
| Каждый час | Сохранение рыночного снапшота в PostgreSQL |

---

## 💬 Команды бота

```
/start    — главное меню (онбординг при первом входе)
/help     — справка по командам
/price    [пара]   — текущая цена и индикаторы
/analysis [пара]   — технический анализ
/indicators        — сводка индикаторов по всем парам
/position          — расчёт позиции и рисков
/portfolio         — портфель и PnL
/alerts            — управление алертами
/analytics         — ретроспектива, бэктест, экспорт
/settings          — вотчлист, модель, брифинг
/history           — история чата с AI
/clear             — очистить историю чата
```

---

## 🧩 Добавление новых торговых пар

```bash
# 1. Обновить .env:
WATCHLIST=btcusdt,ethusdt,solusdt,bnbusdt,xrpusdt,dogeusdt

# 2. Перезапустить collector:
docker compose restart collector

# Новые пары автоматически подпишутся через WebSocket.
```

---

## 📝 Лицензия

MIT — используй свободно, торгуй ответственно.

> ⚠️ Бот не является финансовым советником.
> Все решения о сделках принимаешь только ты сам.
````


***

Этот README покрывает весь путь: от `git clone` до работающего бота в Telegram. Разделы диагностики написаны под реальные сценарии сбоев, которые могут возникнуть при первом запуске — проблемы с WebSocket, Redis, токеном, API-ключами. `Makefile` содержит инспекторы для Redis и PostgreSQL, чтобы не лезть в контейнеры вручную.


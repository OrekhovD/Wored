# WORED — HTX Crypto AI Trading Bot

> Telegram-бот с AI-аналитикой крипторынка, сбором данных с HTX и мультимодельной архитектурой.

## Архитектура

```
┌─────────────────────────────────────────────────┐
│                 Docker Compose                  │
│                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
│  │ Postgres │  │  Redis   │  │              │  │
│  │   :5432  │◄─┤  :6379   │◄─┤  Collector   │  │
│  │          │  │  (cache)  │  │  (scheduler) │  │
│  └────┬─────┘  └────┬─────┘  └──────────────┘  │
│       │              │                          │
│       │              │        ┌──────────────┐  │
│       │              │        │   Chatbot     │  │
│       └──────────────┼───────►│  (aiogram 3)  │  │
│                      └───────►│              │  │
│                               └──────┬───────┘  │
│                                      │          │
└──────────────────────────────────────┼──────────┘
                                       │
                              ┌────────▼────────┐
                              │   Telegram API  │
                              └────────┬────────┘
                                       │
                              ┌────────▼────────┐
                              │  ZhipuAI (GLM)  │
                              │  DashScope(Qwen)│
                              │  Gemini / etc.  │
                              └─────────────────┘
```

## Сервисы

| Сервис | Назначение | Технологии |
|--------|-----------|------------|
| **Collector** | Сбор тикеров HTX каждые 60с, детекция спайков, кэширование | Python 3.9, httpx, asyncpg, redis, APScheduler |
| **Chatbot** | Telegram-бот с AI-аналитикой и интерактивным меню | Python 3.9, aiogram 3.x, openai SDK |
| **PostgreSQL** | Персистентное хранение тикеров, алертов, торговой истории | PostgreSQL 16 |
| **Redis** | Кэш актуальных цен (TTL 5 мин), pub/sub для алертов | Redis 7 |

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Главное меню с persistent-клавиатурой |
| `📊 Рынок` | Текущие цены BTC и ETH + inline-кнопки анализа |
| `📈 Аналитика` | Выбор монеты → AI-анализ (GLM 5.1) |
| `🔔 Алерты` | Последние спайки по BTC/ETH |
| `⚙️ Система` | Статус: модель, collector, watchlist |
| Свободный текст | Диалог с AI на любую тему |

## Быстрый старт

```bash
# 1. Клонировать и настроить
cp .env.example .env
# Отредактировать .env: TELEGRAM_TOKEN, API-ключи

# 2. Запустить
docker-compose up --build -d

# 3. Проверить
docker-compose logs -f chatbot
docker-compose logs -f collector
```

## Переменные окружения

| Переменная | Описание |
|-----------|----------|
| `TELEGRAM_TOKEN` | Токен бота от @BotFather |
| `TELEGRAM_ADMIN_ID` | User ID администратора |
| `GLM_API_KEY` | Ключ ZhipuAI BigModel |
| `GLM_MODEL` | Модель GLM (по умолчанию `glm-5.1`) |
| `DASHSCOPE_API_KEY` | Ключ Alibaba DashScope (резерв) |
| `WATCHLIST` | Отслеживаемые пары (через запятую) |
| `ALERT_SPIKE_THRESHOLD` | Порог алерта в % |
| `DATABASE_URL` | Строка подключения PostgreSQL |
| `REDIS_URL` | Строка подключения Redis |

## Структура проекта

```
D:\WORED\
├── docker-compose.yml
├── .env
├── db/
│   └── init.sql                # Схема БД
├── collector/
│   ├── Dockerfile
│   ├── main.py                 # Scheduler entry point
│   ├── htx/rest.py             # HTX REST API клиент
│   ├── scheduler/
│   │   ├── alert_checker.py    # Детекция спайков
│   │   └── briefing.py         # Утренний брифинг (WIP)
│   └── storage/
│       ├── postgres_client.py  # Async PG writer
│       └── redis_client.py     # Async Redis cache
├── chatbot/
│   ├── Dockerfile
│   ├── main.py                 # aiogram 3.x polling
│   ├── ai/
│   │   ├── router.py           # AI provider proxy
│   │   └── prompts.py          # System prompts
│   ├── handlers/
│   │   ├── start.py            # /start + persistent keyboard
│   │   ├── menu.py             # Обработка кнопок меню
│   │   ├── callbacks.py        # Inline callback router
│   │   ├── market.py           # Цены рынка
│   │   ├── analytics.py        # AI-анализ
│   │   ├── alerts.py           # Спайк-алерты
│   │   ├── portfolio.py        # Watchlist
│   │   ├── settings.py         # Статус системы
│   │   └── chat.py             # Свободный чат с AI
│   └── storage/
│       ├── postgres_client.py  # Async PG reader
│       └── redis_client.py     # Async Redis reader
└── hypercube/                  # Gateway API (отдельный подпроект)
```

## Лицензия

Private. All rights reserved.

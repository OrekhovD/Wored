# WORED — Архитектура агентов, ботов и ресурсов

**Дата:** 27.06.2026  
**Версия:** 1.0  
**Статус:** Production (частично деградирован)

---

## 1. АРХИТЕКТУРА ПРОЕКТА

WORED — крипто-трейдинг бот для HTX (Huobi), 5 Docker-сервисов:

| Сервис       | Роль                          | Порт      | Стек                    |
|-------------|-------------------------------|-----------|-------------------------|
| `chatbot`   | Telegram UI + AI routing      | —         | aiogram 3, OpenAI SDK   |
| `collector` | HTX WebSocket + индикаторы     | —         | websockets, pandas      |
| `webui`     | FastAPI дашборд               | 8080→8000 | FastAPI, TradingView    |
| `postgres`  | Данные (алерты, журнал, прогнозы) | 5432  | PostgreSQL 16           |
| `redis`     | Кэш тикеров, pub/sub          | 6379      | Redis 7                 |

---

## 2. AI-АГЕНТЫ (МОДЕЛИ)

### 2.1. Три уровня (Tiers)

| Tier      | Роль                | Модель по умолчанию     | Провайдер      | Max токенов | Timeout |
|-----------|---------------------|------------------------|----------------|-------------|---------|
| **Worker**| Классификация, парсинг | `deepseek-v4-flash`   | Ollama Cloud   | 256         | 15s     |
| **Analyst**| Анализ, прогнозы    | `deepseek-v4-pro`     | Ollama Cloud   | 2048        | 60s     |
| **Premium**| Стратегия, deep research | `glm-5.2`        | Ollama Cloud   | 4096        | 90s     |

### 2.2. Цепочки fallback

**WORKER_MODEL_CHAIN** (классификация/парсинг):
```
worker_ollama → omniroute_execution → worker → worker_qwen35 → worker_qwen_legacy 
→ worker_deepseek → worker_deepseek_or → worker_glm → worker_gemini
```

**ANALYST_MODEL_CHAIN** (анализ):
```
analyst_ollama → omniroute_reasoning → analyst → analyst_qwen27b 
→ analyst_qwen_extra → analyst_deepseek → analyst_deepseek_or → analyst_glm
```

**PREMIUM_MODEL_CHAIN** (стратегия):
```
premium_ollama → omniroute_reasoning → premium → premium_qwen35b 
→ analyst_deepseek_or → premium_glm
```

### 2.3. Статус провайдеров (27.06.2026)

| Провайдер           | Статус  | Причина                          |
|---------------------|---------|----------------------------------|
| **Ollama Cloud**    | ✅ ЖИВ   | Единственный рабочий              |
| DashScope (Qwen)    | ❌ МЁРТВ | Arrearage (просрочка оплаты)      |
| DeepSeek API        | ❌ МЁРТВ | 402 Insufficient Balance         |
| OpenRouter          | ❌ МЁРТВ | 402 Insufficient Credits         |
| GLM (Zhipu)         | ❌ МЁРТВ | API key not set                  |
| Google Gemini       | ❌ МЁРТВ | API key not set                  |
| NVIDIA (MiniMax)    | ❌ МЁРТВ | 403 Forbidden                    |
| OmniRoute Gateway   | ❌ МЁРТВ | API key not set                  |

**Вывод:** Все AI-запросы идут через Ollama Cloud. Остальные провайдеры не оплачены.

### 2.4. Суб-агенты (Intents)

| Intent          | Модель Tier 1     | Модель Tier 2     | Задача                                    |
|-----------------|-------------------|-------------------|-------------------------------------------|
| `price`         | Worker (flash)    | —                 | Текущая цена, изменение                   |
| `analysis`      | Analyst           | Premium (review)  | Технический анализ одной монеты            |
| `deep_analysis` | Premium           | —                 | Глубокий разбор, стратегия, сценарии       |
| `comparison`    | Analyst           | —                 | Сравнение двух активов                    |
| `simple`        | Worker (quick)    | —                 | Простые вопросы, термины                  |
| `chat`          | Worker (chat)     | —                 | Общий диалог                              |
| `trade_plan`    | Worker (flash)    | Analyst/Premium   | Торговый план: нормализация → анализ       |
| `trade_sim`     | Regex (приоритет) | Worker (flash)    | Симуляция фьючерсов: парсинг → сделка      |

**Важно:** `trade_sim` теперь использует regex-first подход — парсинг команд без AI, мгновенно.

---

## 3. TELEGRAM-БОТЫ

| Бот              | @username       | ID         | Токен в .env              | Статус |
|------------------|-----------------|------------|---------------------------|--------|
| **WORED**        | @W_W_O_O_bot    | 8686265741 | `WORED_TELEGRAM_TOKEN`    | 🟢 Up  |
| **RACHELLO**     | @RACHELLO_BOT   | 8343265724 | `TELEGRAM_BOT_TOKEN`      | 🔴 Off |

### 3.1. WORED Bot (@W_W_O_O_bot) — основной

**Функционал:**
- `/start` — приветствие, меню
- Меню-кнопки: 📊 Рынок, 🧠 Аналитика, 🔮 Прогнозы, 🗂 Портфель, 🔔 Алерты, ⚙️ Система
- Свободный ввод: цена, анализ, сравнение, торговый план, симуляция фьючерсов
- AI-управляемые позиции (команда «торгуй»)
- WebApp Dashboard (кнопка Command Deck)

**Модели:** Ollama Cloud (`deepseek-v4-flash` для worker, `deepseek-v4-pro` для analyst, `glm-5.2` для premium)

**Docker:** `chatbot_wored` (отдельный контейнер, `.env.wored`)

### 3.2. RACHELLO Bot (@RACHELLO_BOT) — инженерный

**Функционал:** Hermes-агент для инженерных задач, мониторинг, деплой

**Модели:** Hermes Agent (отдельная система, не WORED)

**Docker:** `chatbot` (основной контейнер, `.env`)

---

## 4. СЕРВИСЫ И РЕСУРСЫ

### 4.1. Collector (сбор данных)

| Компонент              | Файл                                    | Задача                              |
|------------------------|-----------------------------------------|-------------------------------------|
| HTX WebSocket          | `collector/htx/websocket.py`            | Real-time тикеры (цена, объём)      |
| HTX REST               | `collector/htx/rest.py`                 | Исторические данные, klines          |
| Индикаторы             | `collector/indicators/calculator.py`    | RSI, MACD, скользящие средние       |
| Детектор алертов       | `collector/alerts/detector.py`          | Пробой уровней, дивергенции         |
| Scheduler              | `collector/main.py`                     | Периодические задачи                |
| Монитор симуляции      | `collector/scheduler/sim_monitor.py`    | Проверка ликвидаций симулированных позиций |
| Брифинг                | `collector/scheduler/briefing.py`       | Ежедневный отчёт                    |
| AI-журнал              | `collector/journal/writer.py`           | Запись прогнозов в БД               |
| Оценка прогнозов       | `collector/predictions/evaluator.py`    | Сравнение прогнозов с фактом        |

### 4.2. Chatbot (AI + Telegram)

| Компонент              | Файл                                    | Задача                              |
|------------------------|-----------------------------------------|-------------------------------------|
| AI Models              | `chatbot/ai/models.py`                  | Конфигурация 20+ моделей            |
| Dispatcher             | `chatbot/ai/dispatcher.py`              | Классификация intent (AI + regex)   |
| Router                 | `chatbot/ai/router.py`                  | Маршрутизация intent → модель       |
| Prompts                | `chatbot/ai/prompts.py`                 | Системные промпты (8 типов)         |
| Resilience             | `chatbot/ai/resilience.py`              | Retry, circuit breaker              |
| Context Builder        | `chatbot/ai/context_builder.py`         | Сборка контекста из Redis/Postgres  |
| Knowledge Base         | `chatbot/ai/knowledge_base.py`          | Термины, FAQ                        |
| Sim Engine             | `chatbot/services/sim_engine.py`        | Движок симуляции фьючерсов          |
| Handlers (10 шт.)      | `chatbot/handlers/*.py`                 | Telegram message handlers           |
| Redis Client           | `chatbot/storage/redis_client.py`       | Кэш тикеров, pub/sub                |
| Postgres Client        | `chatbot/storage/postgres_client.py`    | Алерты, журнал, позиции             |

### 4.3. WebUI (дашборд)

| Компонент              | Файл                                    | Задача                              |
|------------------------|-----------------------------------------|-------------------------------------|
| FastAPI App            | `webui/app.py`                          | REST API + WebSocket                |
| TradingView Charts     | `webui/static/`                         | Графики цен                         |
| Alerts UI              | `webui/templates/`                      | Управление алертами                 |
| Prediction Lab         | `webui/templates/`                      | Тестирование прогнозов              |
| AI Journal             | `webui/templates/`                      | Просмотр AI-журнала                 |

### 4.4. Базы данных

**PostgreSQL** (порт 5432):
- `alerts` — ценовые алерты
- `ai_journal` — история AI-прогнозов
- `forecast` — прогнозы
- `sim_positions` — симулированные позиции
- `historical_data` — исторические данные

**Redis** (порт 6379):
- `ticker:{symbol}` — текущие цены (JSON)
- `market_alerts` — pub/sub канал алертов
- `realtime_snapshots` — снапшоты рынка

---

## 5. ТЕКУЩИЕ ПРОБЛЕМЫ

| Проблема                          | Статус | Решение                              |
|-----------------------------------|--------|--------------------------------------|
| Все провайдеры кроме Ollama мертвы | 🔴     | Пополнить баланс / перейти на Ollama |
| Flash-модели возвращают кривой JSON | 🟡    | Regex-first парсинг (исправлено)     |
| RACHELLO бот конфликтует с WORED   | 🟢     | Разнесены по контейнерам            |
| Collector missed jobs              | 🟡     | Интервал `check_sim_positions` велик |
| Нет Telegram-уведомлений о ликвидациях | 🟡 | `TELEGRAM_ADMIN_ID` не задан       |

---

## 6. КЛЮЧЕВЫЕ ФАЙЛЫ

| Файл                              | Размер    | Назначение                           |
|-----------------------------------|-----------|--------------------------------------|
| `chatbot/ai/models.py`            | 9 KB      | 20+ конфигураций моделей             |
| `chatbot/ai/router.py`            | 27 KB     | Маршрутизация + trade_sim + trade_plan |
| `chatbot/ai/dispatcher.py`        | 5 KB      | Классификация (AI + regex fallback)  |
| `chatbot/ai/prompts.py`           | 15 KB     | 8 системных промптов                 |
| `chatbot/services/sim_engine.py`  | ~8 KB     | Движок симуляции фьючерсов           |
| `chatbot/main.py`                 | 6 KB      | Точка входа, scheduler, монитор      |
| `collector/main.py`              | 3 KB      | Точка входа collector                |
| `collector/scheduler/sim_monitor.py` | ~2 KB  | Монитор ликвидаций                   |
| `docker-compose.yml`              | 3 KB      | 6 сервисов (chatbot, chatbot_wored, collector, webui, postgres, redis) |
| `.env`                            | ~1 KB     | Токены, ключи API                    |
| `.env.wored`                      | ~1 KB     | Токен WORED бота                     |

---

## 7. КОМАНДЫ УПРАВЛЕНИЯ

```bash
# Сборка
make build

# Запуск WORED бота
docker compose up -d chatbot_wored

# Запуск RACHELLO бота
docker compose up -d chatbot

# Логи
docker compose logs -f chatbot_wored

# Тест симуляции в контейнере
docker compose exec chatbot_wored python3 -c "
import asyncio
from ai.router import route_request
async def t():
    print(await route_request('фьючерсы кросс 200x лонг btc на 30\$'))
asyncio.run(t())
"
```

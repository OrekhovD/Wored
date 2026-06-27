# WORED Telegram Bot — Полное руководство по управлению

**Версия:** 1.0  
**Дата:** 28.06.2026  
**Бот:** @W_W_O_O_bot (chatbot_wored)  
**Админ:** @DVOlgd (ID 5249526259)

---

## 1. Главное меню (Reply Keyboard)

При запуске `/start` бот показывает постоянную клавиатуру:

| Кнопка | Тип | Действие |
|--------|-----|----------|
| 🎛️ Command Deck | WebApp | Открывает Mini App (Daily Session dashboard) |
| 🔑 HER Console | WebApp | Открывает HER Console (управление моделями) |
| 📊 Рынок | Text | Обзор рынка: цены, изменение%, inline-кнопки аналитики |
| 🧠 Аналитика | Text | Меню анализа: выбор монеты → AI analysis |
| 🔮 Прогнозы | Text | Forecast Lab: выбор монеты → горизонт → AI прогноз |
| 🗂 Портфель | Text | Текущие позиции и история симуляций |
| 🔔 Алерты | Text | Последние алерты по watchlist (BTC, ETH) |
| ⚙️ Система | Text | Системные настройки: модели, circuit breakers |

---

## 2. Slash-команды

| Команда | Описание | Доступ |
|---------|----------|--------|
| /start | Приветствие + главное меню | Все |
| /market | Обзор рынка (цены из Redis) | Все |
| /alerts | Последние алерты (5 шт) | Все |
| /analytics | Меню аналитики | Все |
| /predictions | Forecast Lab | Все |
| /portfolio | Портфель: открытые + закрытые позиции | Все |
| /trader | Меню криптотрейдера (6 кнопок) | Все |
| /models | Доступные AI модели + выбор | Все |
| /settings | Системные настройки | Все |
| /session | Статус Daily Pipeline сессии | Все |
| /admin | Админ-панель (inline кнопки) | Админ |

---

## 3. Daily Pipeline (управление сессией)

### 3.1. Запуск сессии

| Фраза | Описание |
|-------|----------|
| старт сессии | Запуск с дефолтами: 100 USDT, balanced |
| старт сессии 200 | Бюджет 200 USDT |
| старт сессии 200 aggressive | Бюджет 200, режим aggressive |
| старт сессии 50 defensive | Бюджет 50, режим defensive |
| начать сессию | Синоним |
| запусти сессию | Синоним |
| старт торговли | Синоним |
| daily session | Синоним (EN) |

Параметры:
- Бюджет: число + USDT/$/дол (по умолчанию 100)
- Риск: aggressive / balanced / defensive (по умолчанию balanced)
- Symbol: BTCUSDT (фиксировано)
- Duration: 8 часов (фиксировано)

### 3.2. Остановка сессии

| Фраза | Описание |
|-------|----------|
| остановить сессию | Закрыть все позиции, FSM → STOPPED |
| стоп сессия | Синоним |
| закрыть сессию | Синоним |
| stop session | Синоним (EN) |

### 3.3. Статус и информация

| Фраза | Описание |
|-------|----------|
| статус сессии | Полный статус: ID, state, plan v, бюджет, метрики |
| сессия статус | Синоним |
| состояние сессии | Синоним |
| session status | Синоним (EN) |
| активный план | Текущий план: thesis, regime, scenarios, entries |
| текущий план | Синоним |
| план сессии | Синоним |
| последняя ревизия | Последняя revision команда + timestamp |
| ревизия плана | Синоним |
| мои позиции | Список сделок сессии (open/closed) |
| позиции сессии | Синоним |
| pnl сессии | Финансовый результат: PnL, W/L, drawdown |
| прибыль сессии | Синоним |
| результат сессии | Синоним |

### 3.4. Revision команды (через Mini App)

Кнопки в Mini App (Daily Session page):

| Команда | Описание | FSM переход |
|---------|----------|-------------|
| continue | Возобновить торговлю | PAUSED → ARMED |
| tighten | Ужесточить risk (аудит) | ARMED → ARMED |
| reduce | Снизить risk (аудит) | ARMED → ARMED |
| pause | Пауза торговли | ARMED → PAUSED |
| close_all | Закрыть все позиции | ANY → STOPPED |

### 3.5. Детали сделок

| Фраза | Описание |
|-------|----------|
| почему вход {id} | Обоснование входа в позицию |
| почему выход {id} | Обоснование выхода из позиции |
| детали позиции {id} | Детальная информация по сделке |

---

## 4. AI Chat (свободный ввод текста)

Любое текстовое сообщение (не команда) маршрутизируется через AI dispatcher:

### 4.1. Intent классификация

| Intent | Триггеры | Модель | Пример |
|--------|----------|--------|--------|
| price | "цена", "price", "курс", "сколько стоит" | Redis (мгновенно) | "цена btc" |
| simple | Простой вопрос по крипте | Worker (flash) | "что такое децентрализация" |
| chat | Общение | Worker (flash) | "привет" |
| analysis | Анализ монеты | Analyst | "анализ eth" |
| deep_analysis | Глубокий разбор | Premium (glm-5.2) | "детальный анализ btc с рассуждениями" |
| comparison | Сравнение активов | Analyst | "сравни btc и eth" |
| trade_plan | Торговый план | Premium (glm-5.2) | "торговый план для btc" |
| trade_sim | Симуляция фьючерсов | Worker→Analyst | "фьючерсы кросс 200x лонг btc на 30$" |

### 4.2. Симуляция фьючерсов (trade_sim)

| Фраза | Описание |
|-------|----------|
| фьючерсы кросс 200x лонг btc на 30$ | Открыть позицию: cross, 200x, long, BTC, $30 |
| фьючерсы изолированный 125x шорт eth на 50$ | isolated, 125x, short, ETH, $50 |
| торгуй | AI управление открытой позицией |
| мои позиции | Список открытых позиций |
| закрой позицию #N | Закрыть позицию по ID |
| история позиций | История закрытых позиций |

Формат: `фьючерсы [маржа] [плечо]x [направление] [монета] на [сумма]$`
- Маржа: кросс / изолированный (cross / isolated)
- Плечо: 100x, 125x, 150x, 200x
- Направление: лонг / шорт (long / short)
- Сумма: число + $ / USDT

---

## 5. Inline-кнопки (Callback Query)

### 5.1. Рынок (/market)

| Кнопка | Callback | Действие |
|--------|----------|----------|
| 🧠 BTCUSDT | analytics:btcusdt | AI анализ BTC |
| 🧠 ETHUSDT | analytics:ethusdt | AI анализ ETH |
| 🔮 BTCUSDT | prediction_symbol:btcusdt | Прогноз BTC |
| 🔮 ETHUSDT | prediction_symbol:ethusdt | Прогноз ETH |
| 📋 Forecast Lab | prediction_menu | Меню прогнозов |
| 🔄 Обновить | refresh_market | Обновить цены |

### 5.2. Аналитика

| Кнопка | Callback | Действие |
|--------|----------|----------|
| ⚖️ Второе мнение | second_opinion:{symbol} | Повторный анализ другой моделью |
| 🔮 Прогноз | prediction_symbol:{symbol} | Перейти к прогнозу |
| 🔄 Обновить анализ | analytics:{symbol} | Пересделать анализ |
| ◀️ К рынку | back_to_market | Вернуться к рынку |

### 5.3. Прогнозы (Forecast Lab)

| Кнопка | Callback | Действие |
|--------|----------|----------|
| BTCUSDT / ETHUSDT | prediction_symbol:{sym} | Выбор монеты |
| 4h / 12h / 24h / 48h | prediction_run:{sym}:{h} | Запуск прогноза |
| 📋 Последний | prediction_latest:{sym} | Последний прогноз |
| 🔄 Статус | prediction_status:{id} | Статус прогноза |
| 🌐 Matrix | URL link | Открыть в WebUI |
| 🔄 Обновить обзор | prediction_menu | Обновить меню |

### 5.4. Криптотрейдер (/trader)

| Кнопка | Callback | Действие |
|--------|----------|----------|
| 📊 Анализ BTC/USDT | trader_analyze | AI анализ BTC |
| 📈 Симуляция фьючерсов | trader_sim | Инструкция по симуляции |
| 📋 Результаты симуляций | trader_results | Открытые + закрытые позиции |
| 🧠 Стратегия | trader_strategy | AI стратегия |
| 📓 Журнал агента | trader_journal | AI журнал |
| 🤖 Выбор модели | trader_models | Выбор AI модели |

### 5.5. Модели (/models)

| Кнопка | Callback | Действие |
|--------|----------|----------|
| ⚡ Auto | model_auto | Авто-роутинг (gateway) |
| 🏃 Worker | model_worker_ollama | Быстрая модель |
| 🧠 Analyst | model_analyst_ollama | Аналитическая модель |
| 👑 Premium | model_premium_ollama | Премиум модель (glm-5.2) |

### 5.6. Админ-панель (/admin, только админ)

| Кнопка | Callback | Действие |
|--------|----------|----------|
| 🏥 Health Check | admin_health | Проверка Redis, Postgres, Collector |
| 🔍 Active Route | admin_route | Текущий AI route |
| 📊 Quota Dashboard | admin_quotas | Quotas по моделям |
| 🔬 Model Lab: Probe All | admin_probe | Прозвон всех моделей |
| 🔄 Rotate Active Slot | admin_rotate | Ротация активной модели |

---

## 6. Mini App (Telegram WebApp)

### 6.1. Command Deck (Daily Session)

Открывается через кнопку 🎛️ Command Deck или ссылку.

Страница /daily-session:

| Блок | Содержание |
|------|------------|
| Session | ID, symbol, status, riskmode, failed entries, start/end, plan v, last command |
| Execution Control | 5 кнопок: Continue, Tighten, Reduce, Pause, Close All |
| Metrics | Trades, W/L, liquidations, PnL, PnL%, max DD, profit factor, time in market |
| Active Plan | Thesis, regime, primary/alternative scenarios, no-trade condition, session risk |
| Planned Entries | Таблица: side, zone, SL, TP, leverage, share, status |
| Executed Trades | Таблица: side, leverage, entry/exit, PnL, reason, status |
| Last Revision | Команда + timestamp |
| Event Log | Live SSE поток execution events |

### 6.2. API Endpoints

| Endpoint | Метод | Описание |
|----------|-------|----------|
| /api/daily-session/start | POST | Запуск сессии |
| /api/daily-session/active | GET | Unified snapshot |
| /api/daily-session/revision | POST | Команда (continue/tighten/reduce/pause/close_all) |
| /api/daily-session/events | GET | Polling events |
| /api/daily-session/events/stream | GET | SSE live stream |

---

## 7. Hermes Admin (только админ, ID 5249526259)

Hermes-команды обрабатываются отдельным router-ом:

| Тип | Описание |
|-----|----------|
| /hermes * | Hermes CLI команды |
| Текст без / | Свободный ввод → Hermes bridge (route_hermes_intent) |

Зарезервированные команды: определяются HERMES_RESERVED_COMMANDS в config.

---

## 8. Router Priority (порядок обработки)

Сообщения обрабатываются в строгом порядке:

| # | Router | Перехватывает |
|---|--------|---------------|
| 1 | start_router | /start |
| 2 | menu_router | Кнопки главного меню (📊 Рынок, 🧠 Аналитика, ...) |
| 3 | callbacks_router | Inline-кнопки (analytics, second_opinion, refresh) |
| 4 | market_router | /market |
| 5 | alerts_router | /alerts |
| 6 | analytics_router | /analytics |
| 7 | portfolio_router | /portfolio |
| 8 | predictions_router | /predictions + callback queries |
| 9 | settings_router | /settings |
| 10 | trader_router | /trader + callback queries |
| 11 | models_router | /models + callback queries |
| 12 | admin_router | /admin + callback queries |
| 13 | pipeline_router | /session + текстовые intents (старт сессии, статус, ...) |
| 14 | hermes_admin_router | Hermes команды (только админ) |
| 15 | chat_router | Любой текст → AI dispatcher (LAST) |

---

## 9. FSM States (Daily Pipeline)

| State | Описание | Переход |
|-------|----------|---------|
| IDLE | Нет сессии | → ARMED (start) |
| ARMED | Торговля активна, ищет входы | → IN_POSITION / PAUSED / STOPPED |
| IN_POSITION | Позиция открыта | → COOLDOWN / STOPPED |
| COOLDOWN | Ожидание после стопа | → ARMED |
| PAUSED | Пауза (команда pause) | → ARMED (continue) |
| STOPPED | Остановлена (close_all) | → COMPLETED |
| COMPLETED | Сессия завершена | terminal |

---

## 10. Risk Modes

| Режим | Max DD | Max Fails | Cooldown | Max Positions |
|-------|--------|-----------|----------|---------------|
| defensive | 5% | 2 | 30 мин | 1 |
| balanced | 10% | 3 | 20 мин | 1 |
| aggressive | 20% | 4 | 10 мин | 1 |

Во всех режимах: stoptradingafterliquidation = true

---

## 11. WebUI Pages

| URL | Описание |
|-----|----------|
| / | Dashboard (главная) |
| /alerts | Алерты |
| /predictions | Прогнозы |
| /journal | AI журнал |
| /futures | Futures Lab |
| /daily-session | Daily Session Mini App |
| /strategy | Стратегия |
| /models | Модели |

---

## 12. Сценарии использования

### 12.1. Быстрый старт сессии

1. Отправить "старт сессии" в @W_W_O_O_bot
2. Бот отвечает "Сессия запущена!"
3. Открыть Mini App (🎛️ Command Deck)
4. Наблюдать live events, метрики, план
5. При необходимости → кнопки контроля (pause, close_all)

### 12.2. Управление через Telegram (без Mini App)

1. "старт сессии 200 aggressive" — запуск
2. "статус сессии" — проверка состояния
3. "активный план" — текущий план AI
4. "мои позиции" — сделки
5. "pnl сессии" — результат
6. "остановить сессию" — завершение

### 12.3. Аналитика и прогнозы

1. /market — обзор цен
2. 🧠 BTCUSDT — AI анализ
3. ⚖️ Второе мнение — повторный анализ
4. 🔮 BTCUSDT → 24h — прогноз на 24 часа
5. 🌐 Matrix — открыть в WebUI

### 12.4. Симуляция фьючерсов

1. /trader → 📈 Симуляция фьючерсов
2. "фьючерсы кросс 200x лонг btc на 30$"
3. "торгуй" — AI управление
4. "мои позиции" — проверка
5. "закрой позицию #5" — закрытие

### 12.5. Админ-панель

1. /admin → 🏥 Health Check
2. 🔍 Active Route — текущий AI route
3. 🔬 Model Lab: Probe All — прозвон моделей
4. 🔄 Rotate Active Slot — ротация

---

## 13. Коды ошибок

| Код | Сценарий |
|-----|----------|
| 200 | Успешный GET |
| 201 | Создание сессии (POST /start) |
| 400 | Ошибка структуры запроса |
| 401 | Telegram WebApp auth невалидна |
| 403 | Invalid internal API token |
| 404 | Сессия не найдена |
| 409 | Конфликт FSM (STOPPED/COMPLETED) |
| 422 | Невалидная revision команда |
| 500 | Внутренняя ошибка |
# Архитектура проекта

## Общий runtime-поток

```text
Telegram User
    -> chatbot (aiogram 3)
        -> Redis: live market cache, alerts, pub/sub events
        -> Postgres: alerts, AI journal, system state
        -> AI providers

Browser
    -> webui (FastAPI)
        -> Redis: live watchlist snapshot
        -> Postgres: alerts, AI journal
        -> HTX REST data

HTX WebSocket / REST
    -> collector
        -> Redis: ticker cache, market alerts
        -> Postgres: alert rows and AI journal
```

## Основные модули

### `collector`

Ответственен за сбор данных из HTX, расчёт индикаторов, мониторинг рынка, события и scheduler. Это одна из ключевых runtime-составляющих проекта.

### `chatbot`

Телеграм-бот, который работает с состоянием рынка, принимает команды пользователя, поддерживает меню, свободный текст и AI-подсказки.

### `webui`

Браузерная панель управления на базе FastAPI с визуализацией свечей, объёмов, RSI, MACD и журналом AI-аналитики.

### `db`

Содержит схему данных, bootstrap и миграционные элементы для Postgres.

### `docs`

Документация корневого проекта: архитектура, статус, ссылки на внутренние документы и ТЗ.

### `hypercube`

Отдельный подпроект AI gateway, который может иметь собственную архитектуру, конфигурацию и документацию.

### `paper_trading`

Симуляционный trading layer для сравнительных сценариев и paper trading без реальных биржевых ордеров.

## Слои проекта

1. Data ingestion — HTX и внешние источники
2. Processing — индикаторы, правила алертов, логика сигналов
3. Application services — Telegram, WebUI, API
4. Persistence — Redis и Postgres
5. AI providers — Ollama Cloud, OpenRouter, NVIDIA NIM, TokenRouter и др.

## Важные принципы

- local-first runtime
- работа без обязательного облачного backend
- использование Redis как hot cache
- Postgres как источник более длинного/структурированного состояния
- provider fallback и AI routing для resilience

## Дополнительно

- [Quick Start](Quick-Start.md)
- [AI Stack](AI-Stack.md)
- [README](../../README.md)

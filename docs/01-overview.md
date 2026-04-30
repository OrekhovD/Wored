# Обзор проекта WORED

## Назначение

WORED — это Telegram-first криптобот, который совмещает:

- живой сбор рынка с HTX,
- горячее состояние рынка в Redis,
- историю алертов и AI-журнала в Postgres,
- AI-анализ с маршрутизацией между несколькими моделями,
- браузерную панель для графиков и операционного обзора,
- локальный Docker Compose как основной способ запуска.

Главный пользовательский интерфейс — Telegram. Для более глубокого просмотра рынка, индикаторов и операционного состояния теперь есть отдельный `webui` по адресу `http://localhost:8080`.

## Что это за проект

- Локально разворачиваемый крипто-ассистент с AI-анализом.
- Небольшая multi-service система, а не один Python-скрипт.
- Корневой runtime, который нужно рассматривать отдельно от вложенного `hypercube`.

## Чем проект не является

- Это не полноценный торговый движок биржи.
- Это не универсальный OpenRouter-like gateway на уровне корня репозитория.
- Это не то же самое, что `D:\WORED\hypercube`.

## Границы активного runtime

В активный корневой runtime входят только:

- `postgres`
- `redis`
- `collector`
- `chatbot`
- `webui`

Следующие каталоги существуют в репозитории, но не входят в корневой runtime path:

- `hypercube/`
- `skills/`
- `анализ/`
- `TASOCHKI/`
- `DockerDesktopWSL/`

## Основные пользовательские сценарии

### Сценарий 1: Обзор рынка в Telegram

1. Пользователь открывает Telegram-бота.
2. Нажимает `📊 Рынок`.
3. `chatbot` читает `ticker:*` из Redis.
4. Бот показывает текущую цену и дневное изменение по символам из `WATCHLIST`.

### Сценарий 2: AI-анализ в Telegram

1. Пользователь нажимает `📈 Аналитика` или пишет свободный текст.
2. `chatbot` классифицирует намерение через worker-модель.
3. `chatbot` дополняет запрос контекстом из Redis и Postgres.
4. `chatbot` вызывает предпочтительный AI-tier и при ошибке уходит в fallback.
5. Ответ возвращается в Telegram с короткой служебной меткой.

### Сценарий 3: Push-алерты

1. `collector` получает обновления тикеров из HTX WebSocket.
2. `collector` каждые 5 минут проверяет spike-условия.
3. При превышении порога `collector` пишет строку в Postgres и публикует Redis-событие.
4. `chatbot` слушает Redis pub/sub и отправляет алерт администратору.

### Сценарий 4: Исторический AI-журнал

1. Каждые 15 минут `collector` получает историю свечей через HTX REST.
2. `collector` рассчитывает индикаторы и пишет снимок рынка в `ai_journal`.
3. `chatbot` читает свежие записи журнала для обычного и глубокого анализа.

### Сценарий 5: Browser control room

1. Оператор открывает `http://localhost:8080`.
2. `webui` читает live watchlist из Redis и состояние контура из Redis/Postgres.
3. Для выбранного символа `webui` запрашивает свечи HTX REST и строит candlestick, volume, RSI и MACD.
4. `webui` подмешивает последние `alerts` и записи `ai_journal`, чтобы показать рыночный контекст без Telegram и SQL-консоли.

## Текущее состояние функциональности

Реально реализовано и используется:

- polling Telegram-бот на `aiogram 3`,
- HTX WebSocket ingestion,
- Redis ticker cache,
- Postgres alert history,
- Postgres AI journal,
- AI intent classification,
- AI context enrichment,
- fallback chain с timeout, retry и circuit breaker,
- browser dashboard для графиков и операционного обзора,
- контейнерные тесты для `chatbot` и `webui`.

Реализовано в коде, но не находится на главном runtime path:

- `market_tickers` как реальное хранилище истории,
- `ai_usage_log`,
- `collector/htx/rest.py:get_all_tickers`,
- `collector/storage/postgres_client.py:save_tickers`.

Присутствует как заглушка или legacy:

- `chatbot/context/builder.py`
- `chatbot/ui/formatter.py`
- `chatbot/ui/keyboards.py`
- `chatbot/ui/onboarding.py`
- `collector/alerts/detector.py`
- `collector/scheduler/briefing.py`
- `chatbot/loader.py`

## Для кого сейчас подходит проект

Текущая версия подходит одному оператору, которому нужен:

- локальный запуск через Docker,
- Telegram как основной frontend,
- браузерная панель для глубокого просмотра графиков и истории,
- AI-комментарий по небольшому watchlist,
- прозрачное состояние в Redis/Postgres,
- ограниченное число движущихся частей.

# Playbook: Fix Collector

## Цель
Диагностика и починка HTX data collector.

## Что проверять

### HTX WebSocket
- Подключение к `wss://api.huobi.pro/ws`
- GZIP decompression работает
- Кастомный ping/pong протокол (не стандартный WS ping)
- Auto-reconnect при разрыве

### Redis
- Ticker cache: `ticker:{symbol}` — TTL 60s
- Indicators: `indicators:{symbol}:{interval}` — TTL 120s
- Depth: `depth:{symbol}` — TTL 30s
- AI Journal: `ai:journal:latest` — TTL 120s
- Candles: `candles:{symbol}:{interval}` — list, до 500

### PostgreSQL
- Таблица candles — новые записи
- Таблица ai_journal — записи с текущей датой

### Scheduler
- APScheduler jobs: check_alerts, record_ai_journal, evaluate_due_forecasts, cleanup_old_tickers
- Missed runs — допустимы до ~30s, критично > 60s

## Шаги диагностики

### 1. Проверить логи collector
```
/lc
```
Искать: WS disconnect, GZIP errors, reconnect loops, scheduler warnings.

### 2. Проверить ticker cache
```
/tickers
```
Ожидание: минимум btcusdt + ethusdt, TTL > 0.

### 3. Проверить AI Journal
```
/journal
```
Ожидание: свежая запись (TTL > 0).

### 4. Проверить БД
```
/dbstats
```
Ожидание: ai_journal count растёт.

### 5. Проверить алерты
```
/alerts
```

### 6. Проверить staleness
```bash
docker compose exec -T redis redis-cli ttl ai:journal:latest
docker compose exec -T redis redis-cli --scan --pattern 'ticker:*'
```
- TTL -2 = ключ не существует → collector stale
- TTL -1 = ключ без истечения → нестандартно
- TTL > 0 = живой

## Типовые проблемы

### Collector не подключается к HTX WS
1. Проверить DNS/сеть: `docker compose exec collector python -c "import asyncio, websockets; asyncio.run(websockets.connect('wss://api.huobi.pro/ws'))"`
2. Проверить логи на GZIP ошибки
3. Restart: `docker compose restart collector`

### Redis пустой, но collector жив
1. Проверить Redis connectivity из collector
2. Проверить env: REDIS_HOST, REDIS_PORT
3. `docker compose exec collector python -c "import redis; r=redis.Redis(host='redis',port=6379); print(r.ping())"`

### Scheduler лагает
- Missed runs < 30s — нормально для нагруженной системы
- Missed runs > 60s — проверить CPU/memory контейнера
- `docker stats htx_trading_bot_collector`

## Файлы
### Можно трогать (с подтверждения)
- `collector/htx/websocket.py` — WS клиент
- `collector/htx/rest.py` — REST клиент
- `collector/indicators/calculator.py`
- `collector/alerts/detector.py`
- `collector/journal/writer.py`
- `collector/storage/redis_client.py`
- `collector/storage/postgres_client.py`
- `collector/scheduler/` — scheduler jobs

### Нельзя трогать без явного запроса
- `collector/main.py` — точка входа (критична)
- `collector/predictions/` — Prediction Lab engine

## Когда остановиться и спросить владельца
- WS протокол изменён
- Добавлены новые символы/интервалы
- Изменена логика индикаторов
- Изменён scheduler (интервалы, джобы)
- Требуется пересборка Docker образа

## Критерии готовности
- WS подключен и принимает данные
- Redis имеет свежие тикеры (TTL > 0)
- ai:journal:latest существует
- APScheduler работает (missed < 30s)
- Новые candles пишутся в PostgreSQL

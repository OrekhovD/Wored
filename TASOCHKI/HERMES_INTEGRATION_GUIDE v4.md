ТЗ: WORED Hermes Runtime Intelligence Layer
1. Назначение

Создать слой инструментов для Hermes Agent, который позволяет:

1. Быстро собирать runtime snapshot проекта.
2. Понимать, какие данные есть локально, а какие нужно получать on-demand.
3. Получать исторические свечи через API без постоянного хранения длинной истории.
4. Строить market context перед AI-анализом.
5. Искать паттерны поведения рынка по дате, времени, индикаторам и событиям.
6. Проверять AI-провайдеров без раскрытия секретов.
7. Проверять WebUI на визуально-функциональные регрессии.
8. Управлять retention policy безопасно, через dry-run по умолчанию.

Главный принцип:

WORED не является data warehouse для всей рыночной истории.

WORED хранит:
- собственные решения системы;
- alerts;
- AI journal;
- forecasts;
- forecast evaluations;
- пользовательские действия;
- короткую hot/short историю для UI и диагностики.

Длинная рыночная история получается on-demand через HTX/API и пересчитывается при анализе.
2. Область работ
2.1 Активный runtime path

Разрешённая зона изменений:

scripts/
docs/
docs/hermes/
docs/hermes/playbooks/
webui/app.py
webui/templates/*
webui/static/*
collector/htx/rest.py
collector/indicators/*
2.2 Caution / legacy зоны

Не трогать без отдельного подтверждения:

chatbot/loader.py
chatbot/context/*
chatbot/ui/*
collector/alerts/detector.py
collector/scheduler/briefing.py
2.3 Запрещено
- Не печатать секреты.
- Не читать/выводить .env целиком.
- Не выполнять destructive cleanup без подтверждения.
- Не выполнять docker compose down -v.
- Не удалять durable product data.
- Не менять AI-routing chatbot без отдельного задания.
- Не переписывать WebUI с нуля.
- Не заменять app.js целиком.
3. Архитектурная модель данных
3.1 Hot cache

Redis:

ticker:{symbol}                 TTL 60s
indicators:{symbol}:{interval}  TTL 120s
ai:journal:latest               TTL 120s
candles:{symbol}:{interval}     TTL 1–6h
alerts:recent                   TTL/list recent
3.2 Short local market history

Postgres:

candles                         1–7 дней для активных символов
indicators                      1–7 дней или пересчёт on-demand

Назначение: WebUI, диагностика, быстрый локальный анализ.

3.3 Durable product history

Хранить долго:

alerts
ai_journal
forecast_requests
forecast_points
forecast_evaluations
admin_actions / audit logs, если есть
user alert state
ack / reopen события

Причина: эти данные нельзя восстановить через внешнее market API.

3.4 On-demand external history

Получать по запросу:

HTX REST candles
external event calendar
manual market events yaml
future news/API adapter
4. Модули P5
P5-01: Runtime Snapshot Bundle
Цель

Дать Hermes одну безопасную команду, которая собирает состояние проекта в отдельную папку без секретов.

Файлы
scripts/runtime_snapshot.sh
docs/hermes/playbooks/runtime-snapshot.md
docs/hermes/README.md
Quick command

В ~/.hermes/config.yaml:

snapshot:
  type: exec
  command: bash scripts/runtime_snapshot.sh
Поведение

Команда создаёт папку:

runtime_snapshot/YYYYMMDD_HHMMSS/

Внутри:

compose_ps.txt
git_status.txt
webui_routes.txt
redis_summary.txt
postgres_summary.txt
recent_errors.txt
latest_alerts.txt
latest_forecasts.txt
metadata.json
README.txt
Содержимое

compose_ps.txt:

docker compose ps

git_status.txt:

git branch --show-current
git status --short
git diff --stat

webui_routes.txt:

curl -fsS http://localhost:8080/
curl -fsS http://localhost:8080/alerts
curl -fsS http://localhost:8080/predictions
curl -fsS http://localhost:8080/journal

Но сохранять не весь HTML, а только статус:

/ OK
/alerts OK
/predictions OK
/journal OK

redis_summary.txt:

redis ping
ticker count
ai:journal:latest ttl
sample keys without values

postgres_summary.txt:

\dt
row counts по ключевым таблицам

recent_errors.txt:

docker compose logs --tail=400 | grep -Ei 'error|exception|traceback|failed|timeout|refused'

latest_alerts.txt:

SELECT id, symbol, alert_type, severity, status, created_at
FROM alerts
ORDER BY created_at DESC
LIMIT 20;

latest_forecasts.txt:

SELECT id, symbol, horizon_hours, status, created_at
FROM forecast_requests
ORDER BY created_at DESC
LIMIT 20;

Если таблицы отличаются — скрипт не должен падать. Он пишет:

forecast_requests unavailable
Security requirements

Snapshot не должен содержать:

.env
API keys
Bearer tokens
Telegram token
полные дампы БД
полные значения Redis keys с секретами
персональные Telegram данные

Добавить маскирование:

sed -E 's/(API_KEY|TOKEN|SECRET|PASSWORD)=([^[:space:]]+)/\1=***MASKED***/g'
Definition of Done
- bash scripts/runtime_snapshot.sh создаёт папку snapshot.
- Скрипт завершается exit 0 даже при пустых Redis/Postgres таблицах.
- Секреты не попадают в snapshot.
- Hermes quick command /snapshot работает.
- Документ runtime-snapshot.md объясняет, что внутри snapshot.
P5-02: Data Availability Map
Цель

Дать Hermes и WebUI карту доступности данных: что есть в Redis/Postgres, что stale, что нужно добрать через API.

Файлы
webui/app.py
docs/data-retention.md
docs/hermes/playbooks/data-map.md
Endpoint
GET /api/runtime/data-map
Ответ
{
  "redis": {
    "status": "ok",
    "ticker_count": 2,
    "journal_ttl": 84,
    "sample_ticker_keys": ["ticker:btcusdt", "ticker:ethusdt"]
  },
  "postgres": {
    "status": "ok",
    "tables": {
      "alerts": {
        "rows": 120,
        "latest": "2026-05-01T05:00:00Z"
      },
      "ai_journal": {
        "rows": 48,
        "latest": "2026-05-01T04:00:00Z"
      },
      "forecast_requests": {
        "rows": 18,
        "latest": "2026-05-01T04:30:00Z"
      },
      "candles": {
        "rows": 1000,
        "ranges": [
          {
            "symbol": "BTCUSDT",
            "interval": "60min",
            "from": "2026-04-30T00:00:00Z",
            "to": "2026-05-01T00:00:00Z"
          }
        ]
      }
    }
  },
  "external": {
    "htx_rest": "not_checked",
    "mode": "on_demand"
  },
  "policy": {
    "long_market_history": "on_demand",
    "durable_product_history": "postgres"
  }
}
Требования
- Endpoint не должен падать, если таблица отсутствует.
- Endpoint не должен возвращать секреты.
- Endpoint должен иметь быстрый timeout.
- Ошибка Redis/Postgres должна возвращаться как status=error, а не 500.
Definition of Done
- curl /api/runtime/data-map возвращает JSON.
- При Redis down endpoint возвращает redis.status=error.
- При пустых таблицах endpoint возвращает rows=0.
- WebUI не ломается.
P5-03: Historical Fetch on Demand
Цель

Получать рыночную историю по запросу, не сохраняя всё постоянно.

Файлы
scripts/fetch_history.py
docs/hermes/playbooks/fetch-history.md
CLI
python scripts/fetch_history.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 7 \
  --mode preview
Аргументы
--symbol            BTCUSDT / ETHUSDT
--period            1min, 5min, 15min, 60min, 4hour, 1day
--from              optional ISO date
--to                optional ISO date
--lookback-days     optional integer
--mode              preview | json | cache | store
--limit             optional max candles
Режимы

preview:

Показывает, что будет получено, без записи.

json:

Печатает нормализованные candles в JSON.

cache:

Кладёт результат в Redis с TTL.

store:

Пишет в Postgres short-retention candles.
Только после явного указания --mode store.
Источник

Использовать существующий HTX REST-клиент, если он уже есть на активном runtime path. Если его интерфейс неудобен — сделать thin wrapper в scripts/fetch_history.py, но не ломать collector.

Нормализованный формат candle
{
  "symbol": "BTCUSDT",
  "period": "60min",
  "open_time": "2026-05-01T00:00:00Z",
  "open": 62000.0,
  "high": 62500.0,
  "low": 61800.0,
  "close": 62300.0,
  "volume": 1234.56,
  "source": "htx_rest"
}
Definition of Done
- mode preview не пишет в Redis/Postgres.
- mode json возвращает валидный JSON.
- mode cache пишет только временный Redis key с TTL.
- mode store не запускается случайно.
- Ошибка HTX API выводится понятно.
P5-04: Market Context Fetcher
Цель

Собрать для Hermes компактный аналитический market context по символу/периоду.

Файлы
scripts/market_context.py
docs/hermes/playbooks/market-context.md
CLI
python scripts/market_context.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 14 \
  --format markdown
Источники

Порядок:

1. Локальный Postgres, если есть нужные candles.
2. Redis cache, если подходит.
3. HTX REST через fetch_history.
4. Пересчёт индикаторов локально.
Что считать
- SMA20/SMA50
- RSI14
- MACD 12/26/9
- volume average
- volume spike ratio
- volatility
- max drawdown
- trend direction
- high/low range
- часовые/дневные группировки
Patterns

Минимальный набор:

trend_up
trend_down
sideways
volume_spike
rsi_overbought
rsi_oversold
sma_bull_cross
sma_bear_cross
macd_bull_cross
macd_bear_cross
volatility_expansion
drawdown
rebound
Output JSON
{
  "symbol": "BTCUSDT",
  "period": "60min",
  "range": {
    "from": "2026-04-20T00:00:00Z",
    "to": "2026-05-01T00:00:00Z"
  },
  "data_source": {
    "local_candles": 120,
    "fetched_from_htx": 144,
    "mode": "on_demand"
  },
  "latest": {
    "close": 62300.0,
    "rsi14": 62.4,
    "macd": 120.5,
    "macd_signal": 80.2,
    "sma20": 61800.0,
    "sma50": 60400.0
  },
  "patterns": [
    {
      "type": "volume_spike",
      "time": "2026-04-29T14:00:00Z",
      "strength": 2.4
    }
  ],
  "summary": "BTCUSDT had bullish trend with two volume spikes and no oversold zones."
}
Output Markdown

Для Hermes:

# Market Context: BTCUSDT 60min

Range: 2026-04-20 → 2026-05-01  
Source: local 120 candles, HTX 144 candles

## Latest
- Close: 62300
- RSI14: 62.4
- MACD: bullish
- SMA20 > SMA50

## Patterns
- 2026-04-29 14:00 UTC: volume_spike x2.4
- 2026-04-30 09:00 UTC: RSI overbought 74.1

## Summary
Bullish trend with volatility expansion near 14:00 UTC.
Definition of Done
- Работает без записи в БД по умолчанию.
- Может вернуть JSON и Markdown.
- Если данных мало — пишет insufficient_data.
- Не требует запуска collector.
P5-05: Pattern Lab
Цель

Дать Hermes возможность исследовать повторяющиеся паттерны поведения рынка.

Файлы
scripts/pattern_lab.py
docs/pattern-lab.md
docs/hermes/playbooks/pattern-lab.md
CLI
python scripts/pattern_lab.py \
  --symbol BTCUSDT \
  --period 60min \
  --pattern volume_spike \
  --forward-hours 8 \
  --lookback-days 90 \
  --group-by hour_of_day
Поддерживаемые pattern
volume_spike
rsi_overbought
rsi_oversold
macd_bull_cross
macd_bear_cross
sma_bull_cross
sma_bear_cross
large_green_candle
large_red_candle
volatility_expansion
Поддерживаемые group-by
none
hour_of_day
day_of_week
session
month
volatility_regime
Метрики
samples
avg_forward_change_pct
median_forward_change_pct
win_rate_up
win_rate_down
max_forward_gain_pct
max_forward_drawdown_pct
stddev_forward_change
best_group
worst_group
Output
{
  "symbol": "BTCUSDT",
  "period": "60min",
  "pattern": "volume_spike",
  "samples": 42,
  "forward_hours": 8,
  "group_by": "hour_of_day",
  "overall": {
    "avg_forward_change_pct": 0.82,
    "median_forward_change_pct": 0.41,
    "win_rate_up": 0.61,
    "stddev_forward_change": 1.8
  },
  "groups": [
    {
      "hour_utc": 14,
      "samples": 8,
      "avg_forward_change_pct": 1.21,
      "win_rate_up": 0.75
    }
  ],
  "notes": [
    "Volume spikes during 13-16 UTC had stronger follow-through."
  ]
}
Definition of Done
- Работает через on-demand history.
- Не пишет в БД по умолчанию.
- Возвращает insufficient_samples при малой выборке.
- Группировки не падают на пустых данных.
P5-06: Retention Policy & Cleanup
Цель

Формализовать, что хранить, сколько хранить и как чистить безопасно.

Файлы
docs/data-retention.md
scripts/retention_cleanup.py
docs/hermes/playbooks/retention-cleanup.md
Политика
Redis:
- ticker:* TTL 60s
- indicators:* TTL 120s
- ai:journal:latest TTL 120s
- candles cache TTL 1h–6h

Postgres short:
- candles: 7 дней
- indicators: 7 дней

Postgres durable:
- alerts: 180 дней или больше
- ai_journal: 90 дней или больше
- forecast_requests: 180 дней или больше
- forecast_points: 180 дней или больше
- forecast_evaluations: 180 дней или больше
CLI
python scripts/retention_cleanup.py --dry-run
python scripts/retention_cleanup.py --apply
Требования
- По умолчанию dry-run.
- --apply требует --confirm.
- Durable tables не чистить без отдельного флага.
- Писать отчёт: сколько строк будет удалено.
Безопасный apply
python scripts/retention_cleanup.py --apply --confirm short-market-history
Definition of Done
- dry-run работает без изменений БД.
- apply невозможен без confirm.
- durable data не удаляются случайно.
- отчёт понятен Hermes и человеку.
P5-07: AI Provider Diagnostics
Цель

Проверять доступность AI-провайдеров без раскрытия ключей.

Файлы
scripts/ai_provider_doctor.py
docs/hermes/playbooks/ai-provider-doctor.md
CLI
python scripts/ai_provider_doctor.py
Проверки
- presence check: DASHSCOPE_API_KEY
- presence check: GLM_API_KEY / ZAI_API_KEY
- presence check: MINIMAX_API_KEY
- endpoint reachable
- minimal ping prompt
- latency
- fallback order
- last provider errors from logs
Output
{
  "providers": [
    {
      "name": "qwen",
      "key_present": true,
      "status": "ok",
      "latency_ms": 812
    },
    {
      "name": "glm",
      "key_present": true,
      "status": "ok",
      "latency_ms": 640
    },
    {
      "name": "minimax",
      "key_present": true,
      "status": "degraded",
      "error": "timeout"
    }
  ],
  "fallback_chain": ["qwen", "glm", "minimax"],
  "secrets_printed": false
}
Security
- Не печатать ключи.
- Не печатать request headers.
- Не печатать raw bearer token.
- Timeout короткий.
- Prompt минимальный.
Definition of Done
- script показывает provider health.
- ключи не выводятся.
- timeout не подвешивает Hermes.
- отсутствие ключа отображается как missing, не traceback.
P5-08: WebUI Visual Regression Light
Цель

Быстро проверять, что WebUI не сломан после патчей.

Файлы
scripts/webui_check.sh
docs/hermes/playbooks/webui-check.md
Проверки
Routes:
- /
- /alerts
- /predictions
- /journal

Markers:
- WORED Control Room
- Alerts
- Prediction Lab
- AI Journal

Chart containers:
- priceChart
- volumeChart
- rsiChart
- macdChart

Negative markers:
- Traceback
- Internal Server Error
- TemplateNotFound
CLI
bash scripts/webui_check.sh
Definition of Done
- exit 0 на здоровом WebUI.
- exit 1 при пропаже chart container.
- exit 1 при 500/Traceback.
- отчёт показывает, какой route/marker упал.
P5-09: External Context Adapter
Цель

Позволить Hermes сопоставлять рыночные паттерны с внешними событиями.

Файлы
docs/market_events.yaml
scripts/external_context.py
docs/hermes/playbooks/external-context.md
Первый этап

Без web/news API. Только локальный curated файл.

docs/market_events.yaml:

events:
  - date: "2026-05-01"
    time_utc: "18:00"
    type: "macro"
    title: "FOMC rate decision"
    expected_impact: "high"
    assets: ["BTCUSDT", "ETHUSDT"]
    notes: "Manual entry"
CLI
python scripts/external_context.py --date 2026-05-01 --symbol BTCUSDT
python scripts/external_context.py --from 2026-04-20 --to 2026-05-01 --topic crypto
Output
{
  "range": {
    "from": "2026-04-20",
    "to": "2026-05-01"
  },
  "symbol": "BTCUSDT",
  "events": [
    {
      "date": "2026-05-01",
      "time_utc": "18:00",
      "type": "macro",
      "title": "FOMC rate decision",
      "expected_impact": "high"
    }
  ]
}
Definition of Done
- Работает с пустым YAML.
- Фильтрует по date/range/symbol/topic.
- Не требует интернета.
- Позже можно добавить web/news adapter.
5. Quick commands для Hermes

Добавить после реализации соответствующих скриптов:

snapshot:
  type: exec
  command: bash scripts/runtime_snapshot.sh

data-map:
  type: exec
  command: curl -fsS http://localhost:8080/api/runtime/data-map | python3 -m json.tool

history-preview:
  type: exec
  command: python scripts/fetch_history.py --symbol BTCUSDT --period 60min --lookback-days 7 --mode preview

market-context:
  type: exec
  command: python scripts/market_context.py --symbol BTCUSDT --period 60min --lookback-days 14 --format markdown

pattern-volume:
  type: exec
  command: python scripts/pattern_lab.py --symbol BTCUSDT --period 60min --pattern volume_spike --forward-hours 8 --lookback-days 90 --group-by hour_of_day

retention-dry-run:
  type: exec
  command: python scripts/retention_cleanup.py --dry-run

ai-doctor:
  type: exec
  command: python scripts/ai_provider_doctor.py

webui-check:
  type: exec
  command: bash scripts/webui_check.sh

external-context:
  type: exec
  command: python scripts/external_context.py --date "$(date +%F)" --symbol BTCUSDT
6. Общий порядок внедрения
1. P5-01 Runtime Snapshot Bundle
2. P5-08 WebUI Visual Regression Light
3. P5-02 Data Availability Map
4. P5-03 Historical Fetch on Demand
5. P5-04 Market Context Fetcher
6. P5-05 Pattern Lab
7. P5-06 Retention Policy & Cleanup
8. P5-07 AI Provider Diagnostics
9. P5-09 External Context Adapter

Почему так:

- Сначала диагностика и регрессия.
- Потом карта данных.
- Потом on-demand история.
- Потом аналитика и паттерны.
- Потом cleanup.
- Потом AI-provider health.
- Потом внешний контекст.
7. Тестовая стратегия
7.1 Уровни тестов
Unit tests:
- pure-функции парсинга, расчёта индикаторов, pattern detection.

Script smoke tests:
- каждый scripts/*.py запускается с --help.
- dry-run режимы не меняют БД.
- ошибки внешних сервисов не дают traceback.

Integration tests:
- Redis/Postgres/WebUI через docker compose.
- endpoint /api/runtime/data-map.
- scripts/runtime_snapshot.sh.
- scripts/webui_check.sh.

Regression tests:
- WebUI routes.
- chart markers.
- no secrets in outputs.
- no destructive commands.
8. Детальные тест-кейсы
TEST-P5-01: Runtime Snapshot создаётся

Команда:

bash scripts/runtime_snapshot.sh

Ожидание:

- exit code 0
- создана папка runtime_snapshot/<timestamp>
- есть compose_ps.txt
- есть git_status.txt
- есть webui_routes.txt
- есть redis_summary.txt
- есть postgres_summary.txt
- есть metadata.json
TEST-P5-02: Snapshot не содержит секреты

Команда:

grep -RIE \
  "API_KEY=|TOKEN=|SECRET=|PASSWORD=|Bearer [A-Za-z0-9]" \
  runtime_snapshot/ || true

Ожидание:

- нет реальных секретов
- если встречаются имена переменных, значения замаскированы

Более строгий вариант:

grep -RIE \
  "(sk-[A-Za-z0-9]|nvapi-|xoxb-|[0-9]{8,}:[A-Za-z0-9_-]{20,})" \
  runtime_snapshot/ && exit 1 || echo "OK"
TEST-P5-03: WebUI check проходит

Команда:

bash scripts/webui_check.sh

Ожидание:

- exit code 0
- / OK
- /alerts OK
- /predictions OK
- /journal OK
- chart containers OK
TEST-P5-04: Data-map endpoint работает

Команда:

curl -fsS http://localhost:8080/api/runtime/data-map | python3 -m json.tool

Ожидание:

- валидный JSON
- есть redis.status
- есть postgres.status
- есть policy.long_market_history
- нет секретов
TEST-P5-05: Data-map переживает пустые таблицы

Предусловие:

Postgres доступен, но некоторые таблицы пустые или отсутствуют.

Ожидание:

- endpoint возвращает status degraded/empty/unavailable
- HTTP 200
- нет traceback
TEST-P5-06: fetch_history preview ничего не пишет

Команда:

python scripts/fetch_history.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 7 \
  --mode preview

Ожидание:

- exit code 0
- выводит planned request
- не создаёт новых строк в candles
- не создаёт Redis cache key

Проверка до/после:

BEFORE=$(docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT COUNT(*) FROM candles;"' 2>/dev/null || echo 0)

python scripts/fetch_history.py --symbol BTCUSDT --period 60min --lookback-days 7 --mode preview

AFTER=$(docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT COUNT(*) FROM candles;"' 2>/dev/null || echo 0)

test "$BEFORE" = "$AFTER"
TEST-P5-07: fetch_history json возвращает валидный JSON

Команда:

python scripts/fetch_history.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 1 \
  --mode json | python3 -m json.tool >/dev/null

Ожидание:

- JSON валиден
- элементы содержат open/high/low/close/volume
TEST-P5-08: market_context JSON работает

Команда:

python scripts/market_context.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 7 \
  --format json | python3 -m json.tool

Ожидание:

- валидный JSON
- есть symbol
- есть latest
- есть patterns
- есть summary или insufficient_data
TEST-P5-09: market_context markdown работает

Команда:

python scripts/market_context.py \
  --symbol BTCUSDT \
  --period 60min \
  --lookback-days 7 \
  --format markdown

Ожидание:

- содержит заголовок Market Context
- содержит Latest или insufficient_data
- не содержит traceback
TEST-P5-10: pattern_lab insufficient_samples

Команда:

python scripts/pattern_lab.py \
  --symbol BTCUSDT \
  --period 60min \
  --pattern volume_spike \
  --forward-hours 24 \
  --lookback-days 1

Ожидание:

- exit code 0
- status insufficient_samples или samples < threshold
- нет traceback
TEST-P5-11: pattern_lab group-by работает

Команда:

python scripts/pattern_lab.py \
  --symbol BTCUSDT \
  --period 60min \
  --pattern volume_spike \
  --forward-hours 8 \
  --lookback-days 30 \
  --group-by hour_of_day | python3 -m json.tool

Ожидание:

- валидный JSON
- есть overall
- есть groups или insufficient_samples
TEST-P5-12: retention dry-run безопасен

Команда:

python scripts/retention_cleanup.py --dry-run

Ожидание:

- exit code 0
- показывает, что было бы удалено
- ничего не удаляет

Проверка:

BEFORE=$(docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT COUNT(*) FROM alerts;"' 2>/dev/null || echo 0)

python scripts/retention_cleanup.py --dry-run

AFTER=$(docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT COUNT(*) FROM alerts;"' 2>/dev/null || echo 0)

test "$BEFORE" = "$AFTER"
TEST-P5-13: retention apply без confirm запрещён

Команда:

python scripts/retention_cleanup.py --apply

Ожидание:

- exit code != 0
- сообщение: confirmation required
- ничего не удалено
TEST-P5-14: ai_provider_doctor не печатает ключи

Команда:

python scripts/ai_provider_doctor.py | tee /tmp/ai_doctor.out

Проверка:

grep -E "sk-|nvapi-|Bearer|API_KEY=.*[A-Za-z0-9]{8,}|TOKEN=.*[A-Za-z0-9]{8,}" /tmp/ai_doctor.out && exit 1 || echo "OK"

Ожидание:

- нет ключей
- есть key_present true/false
- есть provider status
TEST-P5-15: external_context работает с пустым YAML

Команда:

cp docs/market_events.yaml /tmp/market_events.bak 2>/dev/null || true
printf "events: []\n" > docs/market_events.yaml

python scripts/external_context.py --date 2026-05-01 --symbol BTCUSDT | python3 -m json.tool

mv /tmp/market_events.bak docs/market_events.yaml 2>/dev/null || true

Ожидание:

- валидный JSON
- events=[]
- нет traceback
TEST-P5-16: quick commands не содержат destructive

Команда:

grep -nE "down -v|rm -rf|docker volume rm|cat .*\.env|printenv|env " ~/.hermes/config.yaml && exit 1 || echo "OK"

Ожидание:

OK
9. Общий acceptance test

После реализации всех P5 модулей:

docker compose config >/dev/null
docker compose up -d --build
docker compose ps

bash scripts/webui_check.sh
bash scripts/runtime_snapshot.sh

curl -fsS http://localhost:8080/api/runtime/data-map | python3 -m json.tool >/dev/null

python scripts/fetch_history.py --symbol BTCUSDT --period 60min --lookback-days 1 --mode preview
python scripts/market_context.py --symbol BTCUSDT --period 60min --lookback-days 7 --format json | python3 -m json.tool >/dev/null
python scripts/pattern_lab.py --symbol BTCUSDT --period 60min --pattern volume_spike --forward-hours 8 --lookback-days 7 | python3 -m json.tool >/dev/null
python scripts/retention_cleanup.py --dry-run
python scripts/ai_provider_doctor.py
python scripts/external_context.py --date "$(date +%F)" --symbol BTCUSDT | python3 -m json.tool >/dev/null

Ожидание:

- Все команды завершаются без traceback.
- WebUI routes работают.
- Snapshot создан.
- Data-map JSON валиден.
- Preview/dry-run ничего не удаляют и не пишут опасно.
- Секреты не выводятся.
10. Готовый промпт для Hermes
Задача: P5 Hermes Runtime Intelligence Layer.

Цель:
сделать WORED удобным для работы Hermes как технического и аналитического агента.

Порядок:
1. P5-01 Runtime Snapshot Bundle
2. P5-08 WebUI Visual Regression Light
3. P5-02 Data Availability Map
4. P5-03 Historical Fetch on Demand
5. P5-04 Market Context Fetcher
6. P5-05 Pattern Lab
7. P5-06 Retention Policy & Cleanup
8. P5-07 AI Provider Diagnostics
9. P5-09 External Context Adapter

Ограничения:
- не ломать runtime;
- не трогать legacy без подтверждения;
- не печатать секреты;
- не менять AI-routing chatbot;
- не переписывать WebUI;
- все destructive/cleanup операции только dry-run по умолчанию;
- длинную market history получать on-demand;
- Postgres использовать для product events, alerts, AI journal, forecasts и evaluations.

Начни с P5-01.
Сначала дай:
PLAN
FILES
RISK
TESTS

После патча выполни:
bash scripts/runtime_snapshot.sh

И дай REPORT.
11. Definition of Done всего P5

P5 считается завершённым, когда:

1. Hermes может собрать runtime snapshot одной командой.
2. Hermes видит data availability map.
3. Market history получается on-demand.
4. Market context строится в JSON/Markdown.
5. Pattern Lab умеет анализировать хотя бы volume_spike, RSI и MACD patterns.
6. Retention cleanup безопасен и dry-run по умолчанию.
7. AI provider doctor не раскрывает ключи.
8. WebUI check ловит поломку маршрутов и chart containers.
9. External context adapter работает с локальным events yaml.
10. Все acceptance tests проходят.

Начинать лучше с P5-01 и P5-08: они дадут страховку перед остальными изменениями.
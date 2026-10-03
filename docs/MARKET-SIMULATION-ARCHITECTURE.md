# MARKET-SIMULATION-ARCHITECTURE — WORED V3

**Фаза:** P0 (инвентаризация / RFC)
**Дата:** 2026-09-28
**База:** Git SHA `7e5b66bdd17779d785674ac56cbeb381a0800aa6`
**Грязные файлы, сохранённые без перезаписи:** `chatbot/main.py`, `docker-compose.yml` (watchdog/poll-health из другой задачи), `docs/QODER-MARKET-FORECAST-SIMULATION-V3-TZ-20260928.md`
**Статус:** RFC на согласование. Код не меняется до аппрува этой фазы (см. §9 п.1 TZ).

---

## 1. Цель и границы

Один экран `/workspace` отвечает на пять вопросов: **что с рынком сейчас; что прогнозируется на каждом горизонте; что сделала симуляция; сколько заработал/потерял каждый счёт; насколько устойчив результат на истории.** График — главный объект. Подготовка сессии, позиции, отчёт — контекст графика.

Не вход в объём: отправка заявок на реальную биржу; подмена активного AI-роутинга (Qwen3.8-Flash — только модель разработки); отказ от существующих маршрутов `/`, `/alerts`, `/predictions`, `/journal`, `/trader`, `/results`, `/trading-day` до доказательства паритета.

## 2. Инвентарь текущего checkout

### 2.1 Рынок / котировки

| Источник | Путь | Что даёт | Проблема |
|---|---|---|---|
| Spot REST | `collector/htx/rest.py`, `webui/app.py:2640` (`/api/candles`), `trader_api.py` (`/api/trader/candles`) | OHLCV spot-свечей | **Не perpetual.** Для paper-исполнения по mark/bid/ask это другой инструмент. |
| Perpetual WS | `collector/htx/linear_swap_ws.py` → `collector/htx/perpetual_market.py` → Redis `market:perpetual:htx:BTC-USDT` | bid/ask/last/mark/index, funding, `component_times` | Только ticker; история не пишется в PG. |
| Perpetual история | `collector/htx/history_loader.py` (314 строк) | linear-swap kline | Не выставлено через API webui; `/api/candles` — spot. |
| Валидатор | `webui/paper_market.py:parse_live_snapshot` | строгий контракт (venue=htx, funding_rate non-null) | Только для paper-домена, не переиспользуется как read-model. |
| Контракт источника | `paper_trading/market.py:PerpetualSnapshot` (dataclass, `market.py:66`) | поля quote + `component_times` + `quality` | Привязан к paper-домену, не выставлен в HTTP. |

**Вывод:** сегодня `spot` и `perpetual` сосуществуют, `/api/candles` — spot. Это ровно тот mix, который запрещает §4 TZ. Нужен единый perpetual read-model.

### 2.2 Прогноз

| Артефакт | Путь | Значение |
|---|---|---|
| Точка прогноза | `webui/prediction_engine.py` (46 KB), `trader_api.py` | `predicted_price`, `low`, `high` от ансамбля ролей |
| Схема | `webui/forecast_schema.py` | строки БД `forecast_*` (legacy) |
| Отображение | `templates/predictions.html`, `static/ui/forecast-chart.js` | **open=close=avg(models), high=max(low), low=min(low)** — не валидный OHLC |
| Горизонты | `prediction_timeframes.py` | список `horizon_hours` |

**Вывод:** `predicted_low/high` унаследованы от разброса моделей, а не от внутрисвечных экстремумов. TZ §4 требует разделить: `predicted_open/high/low` (whick-смысл) и `band_low/band_high` (стат. интервал). Сейчас ни то, ни другое в явном виде не хранится.

### 2.3 Симуляция / домен paper

| Слой | Путь | Роль |
|---|---|---|
| Контракты | `paper_trading/contracts.py` | `AccountKind{manual,auto}`, `DayState`, `CommandType`, `PositionStatus{open,closed,liquidated}` (liquidated **есть в enum**, обработчика нет) |
| Repository | `paper_trading/repository.py` (1452 строки) | 18 таблиц `paper_v2_*`: owners, accounts, days, commands, signals, orders, fills, positions, postings, decisions, leases, heartbeats, reports, strategy_versions, evaluations, cutovers, events |
| Runner | `paper_trading/runner.py` (2273 строки) | poll `paper_v2_commands`, fill/position lifecycle, recovery. `grep liquidat` в runner → **0 совпадений**. Реализован только `calculate_liquidation_price` в `risk.py:202`. |
| Риск | `paper_trading/risk.py` (567 строк) | лимиты, проверка стопа/цели перед ликвидацией |
| API | `webui/paper_api.py` (49 KB) | команды `/api/paper/*`, позиции, ledger |
| UI | `templates/trading_day.html`, `static/ui/trading-day.js` (33 KB) | карточки счетов, кнопка команды |
| Workspace | `webui/workspace_read.py`, `workspace_presenters.py`, `templates/workspace.html` (64 строки), `static/ui/workspace-state.js`, `workspace-actions.js` | статусная строка, stepper, accounts panel, attention list, command drawer. **Нет графика, прогноза, таблицы позиций, session context.** |

**Вывод:** `PositionStatus.liquidated` — «висячий» enum. TZ §6 требует полноценного state-machine.

### 2.4 Отчёт

`paper_trading/service.py` собирает итоги. Пробел по TZ §7:
- `liquidated` не включается в метрики;
- используется `current_balance`, который меняется после следующего дня → нет иммутабельности;
- нет отдельного `equity_snapshots` по времени;
- нет `SessionReportV1` vs `AccountReportV1` split;
- нет `no_trade`/`buy_and_hold` benchmark;
- нет multi-window replay с train/holdout split.

### 2.5 QA / staging

`docker-compose.staging.yml` сеет синтетическую цену `64250` каждые 2с с `PAPER_MARKET_MODE=live`. TZ §1 требует называть это **synthetic staging**, никогда «live HTX».

## 3. Архитектурное решение

### 3.1 Принципы

1. **Единый read-model на инструмент.** Все HTTP-ответы (quote, candles, forecast, position, report) ссылаются на `instrument_key` — запись реестра, а не свободную строку.
2. **Perpetual ≠ spot, никогда в одном ответе.** Валидатор.reject если venue/market_type расходятся между quote и candles.
3. **Decimal-строки везде.** Числа в JSON — строки с фиксированной точностью. Время — ISO-8601 UTC.
4. **Разделение «факт» / «прогноз» / «предложение».** Три типа свечей на графике: closed (fact), forming (fact), predicted (не факт, затемнённая область, отдельная легенда).
5. **Иммутабельность после закрытия.** Plan → `plan_hash`, Report → `as_of` + source quality + hashes. Никаких UPDATE после `closed`.
6. **AI = черновик.** Все предложения агента проходят через тот же `SimulationPlanV1.validate()`, что и ручная форма. Агент не запускает сессию.
7. **Fail-closed на stale.** Stale quote → запрет `order.submit`; close/liquidation работают по отдельным правилам.
8. **Команда подтверждается domain-событием, а не HTTP 202.** UI показывает `accepted → processing → completed|failed|unknown`.

### 3.2 Read-model контракты (Python dataclass → JSON)

#### `InstrumentRegistryEntry`
```
instrument_key: str        # "htx:linear-swap:BTC-USDT"
venue: str                 # "htx"
market_type: str           # "linear-swap" | "spot"
contract_code: str         # "BTC-USDT"
price_tick: Decimal-str
quantity_step: Decimal-str
contract_size: Decimal-str
settlement_currency: str   # "USDT"
fee_schedule_version: str
status: "active" | "deprecated"
```

Первая обязательная запись — `htx:linear-swap:BTC-USDT`. Реестр — JSON-файл `config/instrument_registry.json`, при старте webui загружается и валидируется.

#### `MarketStateV1` — ответ `/api/v3/market/{instrument_key}/state`
```
instrument_key: str
quote:
  bid, ask, last, mark, index: Decimal-str
  funding_rate: Decimal-str
  next_funding_at: ISO-8601
  source_at, received_at: ISO-8601
  component_times: {ticker, mark, index, funding}: ISO-8601
spread: Decimal-str          # ask - bid
quality:
  ticker: "live" | "stale" | "missing"
  mark:   "live" | "stale" | "missing"
  index:  "live" | "stale" | "missing"
  funding: "live" | "stale" | "missing"
  worst: "live" | "stale" | "missing"   # convenience
source: str                  # "htx-ws" | "synthetic-staging" | "demo"
sequence: int                # monotonic per instrument
snapshot_id: uuid
capabilities:
  can_enter: bool            # False при quality.worst != "live"
  can_close: bool            # fail-open для защитных действий
  reason_code: str | null
```

#### `CandleV1` — element of `/api/v3/market/{instrument_key}/candles?period=1m&limit=300`
```
start_at, end_at: ISO-8601 UTC
open, high, low, close, volume: Decimal-str
closed: bool                 # False для формирующейся свечи
source: str
quality: "live" | "replayed" | "synthetic"
sequence: int
```

Инварианты валидатора (`webui/market_workspace.py:_validate_candle`):
- `low <= min(open, close) <= max(open, close) <= high`
- все `Decimal > 0`
- строго возрастающее `start_at`, `end_at = start_at + period`
- нет дублей по `start_at`
- один `venue`+`market_type` в выборке
- `volume >= 0`

При разрыве истории: HTTP 503 + `{ reason_code, last_good_end_at }`, **не** пустые/демо-свечи.

#### `ForecastIntervalPredictionV1` — элемент ответа `/api/v3/forecasts`
```
forecast_id: uuid
instrument_key: str
base_snapshot_id: uuid
base_time: ISO-8601              # end_of last closed fact candle
base_price: Decimal-str          # close of last closed fact candle
period: str                      # "1m" | "5m" | "15m" | "1h" | "4h" | "1d"
horizon: str                     # "15m" | "1h" | "4h" | "24h"
model_id, role, prompt_version: str
generated_at, target_at, expires_at: ISO-8601
execution_state: "pending" | "partial" | "completed" | "failed" | "stale"
error_code: str | null
coverage: float                  # 0..1, доля заполненных интервалов
intervals: list[ForecastCandleV1]
band_method: str                  # "quantile_regr" | "ensemble_std" | ...
confidence_kind: str              # "calibrated" | "raw" | "uncalibrated"
rationale: str                    # text from AI
origin: "model" | "derived"
```

`ForecastCandleV1`:
```
target_start_at, target_end_at: ISO-8601
predicted_close: Decimal-str          # required
predicted_open: Decimal-str | null    # first open = base_price
predicted_high: Decimal-str | null    # expected intra-candle extreme
predicted_low: Decimal-str | null
band_low, band_high: Decimal-str      # statistical interval (NOT wick)
has_valid_ohlc: bool                  # True → candle, False → line+band
```

**Правило отрисовки:** `has_valid_ohlc=False` → рисовать горизонтальную линию `predicted_close` + полосу `band_low..band_high`, **не** свечу с нулевым фитилём.

#### `SimulationPlanV1` — ядро P3
```
plan_version: int
instrument_key: str
mode: "live_paper" | "historical_replay" | "scenario"
start_at, end_at: ISO-8601 UTC
timezone: str                          # IANA, только для отображения
manual_budget: Decimal-str USDT
auto_budget: Decimal-str USDT
strategy_id, strategy_version: str
style: str                              # только из реализованного каталога
allowed_sides: list["long" | "short"]
max_leverage, max_risk_per_order: Decimal-str
max_daily_loss, max_session_loss, max_total_exposure: Decimal-str
max_open_positions: int
cooldown_minutes: int
trading_hours: str                      # "00:00-24:00" | "RTH" | ...
stop_policy, take_profit_policy: str    # "off" | "fixed_pct:2" | "atr_mult:1.5"
close_at_end: bool
fee_schedule_version: str
slippage_model_version: str
funding_model_version: str
market_data_policy: str                 # "strict_live" | "replay_ok" | ...
seed: int | null                        # для replay
plan_hash: str                          # sha256(canonical_json)
```

Все поля — с ranges/units/validators в `paper_trading/session_policy.py`. `validate()` возвращает нормализованный план + `violations[]` + `max_allowed_risk` + snapshot + hash.

#### `SessionReportV1` / `AccountReportV1` — P6
```
report_id, session_id, owner_id, as_of
source: {venue, market_type, quality}
strategy/policy/model_versions
plan_hash, replay_data_hash
reconciliation: {status: "ok" | "mismatch", reason}
metrics:                                # per account
  initial_equity, final_equity
  realized_gross, entry_fees, exit_fees, funding, slippage
  net_pnl, roi, max_drawdown, time_in_market
  trades, wins, losses, liquidations
  win_rate, expectancy, profit_factor   # null при insufficient_sample
  rejected_entries: {reason_code: count}
benchmark: { no_trade, buy_and_hold }
replay_windows: [ {start, end, roi, dd, ...} ]  # при >=3 non-overlapping
median_roi, worst_dd, iqr_roi
sample_size_note: str
```

### 3.3 Новая БД-схема (только additive)

**Миграция** `migrations/v3_simulation_schema.sql`. Ничего не дропается, `paper_v2_*` не меняется.

```sql
CREATE TABLE IF NOT EXISTS instruments (
  instrument_key   TEXT PRIMARY KEY,
  venue            TEXT NOT NULL,
  market_type      TEXT NOT NULL,
  contract_code    TEXT NOT NULL,
  price_tick       NUMERIC(20,8) NOT NULL,
  quantity_step    NUMERIC(20,8) NOT NULL,
  contract_size    NUMERIC(20,8) NOT NULL,
  settlement_currency TEXT NOT NULL,
  fee_schedule_version TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'active',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (venue, market_type, contract_code)
);

CREATE TABLE IF NOT EXISTS simulation_sessions (
  session_id       UUID PRIMARY KEY,
  owner_id         UUID NOT NULL REFERENCES paper_v2_owners(id),
  instrument_key   TEXT NOT NULL REFERENCES instruments(instrument_key),
  mode             TEXT NOT NULL CHECK (mode IN ('live_paper','historical_replay','scenario')),
  planned_start_at TIMESTAMPTZ NOT NULL,
  planned_end_at   TIMESTAMPTZ NOT NULL,
  timezone         TEXT NOT NULL DEFAULT 'Etc/UTC',
  status           TEXT NOT NULL CHECK (status IN
    ('draft','approved','starting','running','closing',
     'settlement_pending','closed','rejected','expired')),
  approved_plan_id UUID,                              -- FK to simulation_plan_versions
  status_reason    TEXT,
  status_changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (planned_end_at > planned_start_at),
  CHECK (EXTRACT(EPOCH FROM (planned_end_at - planned_start_at))/3600 BETWEEN 1 AND 720)
);
CREATE INDEX IF NOT EXISTS ix_sim_sessions_owner_status
  ON simulation_sessions(owner_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS simulation_plan_versions (
  plan_id          UUID PRIMARY KEY,
  session_id       UUID NOT NULL REFERENCES simulation_sessions(session_id),
  plan_version     INT  NOT NULL,
  plan_json        JSONB NOT NULL,
  plan_hash        TEXT NOT NULL,
  origin           TEXT NOT NULL CHECK (origin IN ('manual','ai_proposed')),
  proposal_id      UUID,
  approved_by      UUID NOT NULL REFERENCES paper_v2_owners(id),
  approved_at      TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (session_id, plan_version),
  UNIQUE (session_id, plan_hash)
);

CREATE TABLE IF NOT EXISTS simulation_equity_snapshots (
  snapshot_id      BIGSERIAL PRIMARY KEY,
  session_id       UUID NOT NULL REFERENCES simulation_sessions(session_id),
  account_id       UUID NOT NULL REFERENCES paper_v2_accounts(id),
  ts               TIMESTAMPTZ NOT NULL,
  equity           NUMERIC(20,8) NOT NULL,
  unrealized_pnl   NUMERIC(20,8) NOT NULL,
  mark_price       NUMERIC(20,8) NOT NULL,
  quality          TEXT NOT NULL,
  UNIQUE (session_id, account_id, ts)
);
CREATE INDEX IF NOT EXISTS ix_sim_equity_series
  ON simulation_equity_snapshots(session_id, account_id, ts);

CREATE TABLE IF NOT EXISTS simulation_reports (
  report_id        UUID PRIMARY KEY,
  session_id       UUID NOT NULL REFERENCES simulation_sessions(session_id),
  account_id       UUID REFERENCES paper_v2_accounts(id),   -- NULL = сводный
  as_of            TIMESTAMPTZ NOT NULL,
  report_json      JSONB NOT NULL,
  report_hash      TEXT NOT NULL,
  reconciliation_status TEXT NOT NULL CHECK (reconciliation_status IN ('ok','mismatch')),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (session_id, account_id, as_of),
  UNIQUE (report_id, report_hash)
);

CREATE TABLE IF NOT EXISTS forecast_interval_predictions (
  forecast_id      UUID NOT NULL,
  interval_index   INT  NOT NULL,
  instrument_key   TEXT NOT NULL REFERENCES instruments(instrument_key),
  base_snapshot_id UUID NOT NULL,
  base_time        TIMESTAMPTZ NOT NULL,
  base_price       NUMERIC(20,8) NOT NULL,
  period           TEXT NOT NULL,
  horizon          TEXT NOT NULL,
  model_id         TEXT NOT NULL,
  role             TEXT NOT NULL,
  prompt_version   TEXT NOT NULL,
  generated_at     TIMESTAMPTZ NOT NULL,
  expires_at       TIMESTAMPTZ NOT NULL,
  execution_state  TEXT NOT NULL CHECK
    (execution_state IN ('pending','partial','completed','failed','stale')),
  error_code       TEXT,
  coverage         NUMERIC(4,3),
  target_start_at  TIMESTAMPTZ NOT NULL,
  target_end_at    TIMESTAMPTZ NOT NULL,
  predicted_close  NUMERIC(20,8) NOT NULL,
  predicted_open   NUMERIC(20,8),
  predicted_high   NUMERIC(20,8),
  predicted_low    NUMERIC(20,8),
  band_low         NUMERIC(20,8),
  band_high        NUMERIC(20,8),
  band_method      TEXT NOT NULL,
  confidence_kind  TEXT NOT NULL,
  rationale        TEXT NOT NULL DEFAULT '',
  origin           TEXT NOT NULL CHECK (origin IN ('model','derived')),
  PRIMARY KEY (forecast_id, interval_index),
  CHECK (target_end_at > target_start_at),
  CHECK (band_high IS NULL OR band_low IS NULL OR band_high >= band_low),
  CHECK (predicted_high IS NULL OR predicted_low IS NULL OR predicted_high >= predicted_low)
);
CREATE INDEX IF NOT EXISTS ix_forecast_instr_horizon
  ON forecast_interval_predictions(instrument_key, horizon, generated_at DESC);

-- Добавляем link к существующим дням (день = кусок сессии, сессия = верхний объект)
ALTER TABLE paper_v2_days
  ADD COLUMN IF NOT EXISTS session_id UUID
  REFERENCES simulation_sessions(session_id);
CREATE INDEX IF NOT EXISTS ix_paper_v2_days_session
  ON paper_v2_days(session_id) WHERE session_id IS NOT NULL;
```

**Откат:** `DROP TABLE IF EXISTS forecast_interval_predictions, simulation_reports, simulation_equity_snapshots, simulation_plan_versions, simulation_sessions, instruments;` и `ALTER TABLE paper_v2_days DROP COLUMN IF EXISTS session_id;` — безопасен только если нет production-данных в новых таблицах; на staging/QA — всегда ок.

**Backfill существующих прогнозов (legacy):** старые записи `forecast_*` остаются как есть; при чтении через V3 они попадут как `has_valid_ohlc=false` + `predicted_close=avg(models)` + `band_low/high = {min,max}`. Явного UPDATE legacy-таблиц нет.

### 3.4 HTTP API — карта

| Метод | Путь | Назначение | Фаза |
|---|---|---|---|
| GET | `/api/v3/instruments` | список `InstrumentRegistryEntry` | P1 |
| GET | `/api/v3/market/{key}/state` | `MarketStateV1` | P1 |
| GET | `/api/v3/market/{key}/candles?period&limit&until` | `CandleV1[]` | P1 |
| GET | `/api/v3/market/{key}/stream` | SSE `MarketStateV1` (backpressure + `Last-Event-ID`) | P1 |
| GET | `/api/v3/forecasts?instrument&period&horizon` | последние прогнозы | P2 |
| POST | `/api/v3/forecasts` `{request_id,...}` | асинхронная команда генерации | P2 |
| GET | `/api/v3/forecasts/{forecast_id}` | `ForecastIntervalPredictionV1` | P2 |
| POST | `/api/v3/simulation/plans/validate` | dry-run `SimulationPlanV1` | P3 |
| POST | `/api/v3/simulation/plans` | сохранить draft | P3 |
| POST | `/api/v3/simulation/plans/{id}/approve` | зафиксировать hash | P3 |
| POST | `/api/v3/simulation/sessions` | создать сессию из approved | P3 |
| POST | `/api/v3/simulation/plan-proposals` | анкета → AI proposal | P4 |
| GET | `/api/v3/simulation/proposals/{id}` | status + draft | P4 |
| GET | `/api/v3/positions?status&account&session&page` | фильтрация | P5 |
| GET | `/api/v3/reports/{session_id}` | итоговый отчёт | P6 |
| GET | `/api/v3/reports/{session_id}/export?fmt=json|csv|html` | экспорт | P6 |

Все эндпоинты отдают `Cache-Control: no-store`, требуют авторизации (существующая session-auth webui), имеют owner scope.

### 3.5 UI архитектура

**Один layout `/workspace` с тремя режимами (Обзор / Симуляция / Итоги), state сохраняется в URL** (`?instrument=...&period=...&mode=...&session=...&account=...`).

Desktop 1440×900:
```
┌─ status strip: quality ticker (bid/mark/funding ages) ─┐
├──────────── price+volume chart (≥55% × 420px) ─┬── forecasts panel (15m/1h/4h/24h) ──┤
│                                                 │  · numbers, band, model, age         │
│                                                 │  · rationale (read-only)             │
├─────────────────────────────────────────────────┴──────────────────────────────────────┤
│ plan form (S3) | accounts + positions table (S5/S6)                                     │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

Mobile 390×844: вертикальный стек. Quote-strip → chart (min 280px) → forecasts (гориз. скролл внутри блока, но не страницы) → accounts → positions. Все tap-targets ≥ 44×44 px.

**Компоненты JS** (все ES modules, без переписывания `app.js`):
- `market-workspace.js` — контроллер режима, URL state, fetch/SSE
- `market-chart.js` — Lightweight Charts, подписывает fact candles + forecast overlay + position markers
- `forecast-candles.js` — логика: candle vs line+band по `has_valid_ohlc`
- `session-plan-form.js` — S3 constructor
- `position-table.js` — фильтры open/pending/closed/liquidated

Существующие `workspace-state.js` и `workspace-actions.js` остаются, но делегируют графики/сессию в новые модули.

### 3.6 Realtime

P1 = **SSE** (проще, чем WS; проходит существующий `webui/uvicorn`; `text/event-stream`, `id:` = sequence, `Last-Event-ID` → resync).

При разрыве: client fallback на polling `/api/v3/market/{key}/state` с backoff (1s→30s); при восстановлении resync по `sequence`. UI показывает connection status в strip.

### 3.7 Liquidation state machine (P5)

Новый модуль `paper_trading/liquidation.py`:
```
PositionStatus.open
  ↓ (mark crosses liq threshold, see below)
LiquidationTriggered
  ├─ if executable price available:
  │   → CloseOrder submitted at bid (long) / ask (short) with slippage model
  │   → on fill: atomically record fill + postings + status=PositionStatus.liquidated + reason="mark_breach"
  └─ else:
      → status stays open, subfield liquidation_pending=true, block new entries
```

Threshold: `mark_price <= liquidation_price(side=long, entry, leverage, maintenance_margin_rate)` или симметрично для short. `trading_math.liquidation_price()` — базовая формула; maintenance margin + close cost добавляются как отдельные константы с `fee_schedule_version`.

Runner hook: после каждого `run_cycle()` с новым mark — проход по `open` позициям, вызов `liquidation.maybe_trigger(...)`. Идемпотентно: `idempotency_key = f"liq:{position_id}:{trigger_ts.round('s')}"` — повтор не создаёт второй fill.

### 3.8 Forecast semantics (P2)

`webui/forecast_candles.py`:
- вход: сырой `ForecastIntervalPredictionV1`
- выход: только те интервалы, где `has_valid_ohlc=True` и `low<=min(o,c)<=max(o,c)<=high`; остальные — в band-only
- никогда не дополняет отсутствующий фитиль нулём

`base_time` = end_of last closed fact candle (из `market_workspace`), `base_price` = close этой свечи. `open` первого прогнозного интервала = `base_price`, далее = предыдущий `predicted_close`.

### 3.9 Session reports (P6)

`paper_trading/session_report.py` собирает `SessionReportV1`/`AccountReportV1` **только** из `paper_v2_postings`, `paper_v2_fills`, `paper_v2_positions` в границах `session_id`. Никаких `current_balance` запросов. Хэш отчета = sha256(canonical JSON).

Экспорт JSON/CSV/HTML нормализует Decimal как строки, ISO-8601 UTC, один и тот же set полей.

## 4. Дерево файлов и ответственность

```
webui/market_workspace.py           # P1: BFF state+candles, freshness, quality map
webui/forecast_candles.py           # P2: OHLC validation, candle-vs-line+band
webui/simulation_api.py             # P3/P4: plans, proposals, sessions, owner scope
webui/reports_api.py                # P6: GET report + exports
webui/instrument_registry.py        # P1: load config/instrument_registry.json, validate
webui/templates/workspace.html      # расширен: 3 режима, слот графика
webui/static/ui/market-workspace.js # P1: URL state, SSE client, mode switch
webui/static/ui/market-chart.js     # P1: Lightweight Charts, axes, crosshair
webui/static/ui/forecast-candles.js # P2: render logic
webui/static/ui/session-plan-form.js# P3: full SimulationPlanV1 form
webui/static/ui/position-table.js   # P5: filter/sort, markers sync
paper_trading/session_policy.py     # P3: validate/ranges/immutable hash
paper_trading/liquidation.py        # P5: state machine
paper_trading/session_report.py     # P6: ledger-derived immutable reports
paper_trading/repository.py         # additive: session/report/equity queries
paper_trading/runner.py             # additive: session link, forced close, liq loop
collector/htx/history_loader.py     # только если нужно: расширить API чтения
migrations/v3_simulation_schema.sql # P0-P1: таблицы выше
config/instrument_registry.json     # P1: первая запись BTC-USDT linear swap
tests/test_forecast_candles.py
tests/test_simulation_plan.py
tests/test_session_report.py
tests/test_liquidation.py
tests/paper_trading/test_liquidation_integration.py
tests/paper_trading/test_session_replay.py
tests/ui/test_market_workspace.py
docs/MARKET-SIMULATION-ARCHITECTURE.md  # этот файл
docs/MARKET-SIMULATION-OPERATIONS.md    # P1+
docs/MARKET-SIMULATION-ACCEPTANCE.md    # P6
```

## 5. Порядок реализации, выходные критерии

| Фаза | Объём работ | MC-выход | Комментарий |
|---|---|---|---|
| **P0** | Инвентарь + RFC + schema + rollback | — | Этот документ |
| **P1** | Instruments registry, `/api/v3/market/*`, SSE, `market-workspace.js`, chart, freshness | MC-01…MC-04 | ~3 рабочих дня |
| **P2** | Forecast candle validation, 4 горизонта, forecast-vs-fact, calibration | MC-05…MC-08 | ~4 дня |
| **P3** | `SimulationPlanV1` validator, approve flow, UI form | MC-09…MC-11 | ~5 дней |
| **P4** | AI proposal, stub LLM, deterministic gate, fail-safe | MC-12…MC-13 | ~2 дня |
| **P5** | Liquidation state machine, forced close, position filters | MC-14…MC-17 | ~6 дней |
| **P6** | Immutable reports, exports, replay windows, holdout | MC-18…MC-22 | ~5 дней |

Итого оценка: **~25 рабочих дней** чистого кода + testing. Это не помещается в одну сессию и не должно: §11 TZ явно требует раздельных ответов по фазам.

## 6. Миграции: rollback и backfill

- **Rollout:** только additive (`CREATE TABLE`, `ALTER TABLE ADD COLUMN`). Существующие `paper_v2_*` не трогаются.
- **Rollback:** `DROP TABLE` новых + `ALTER TABLE paper_v2_days DROP COLUMN session_id`. Безопасен при отсутствии продакшн-данных в новых таблицах.
- **Backfill:** legacy `forecast_*` остаются немигрированными; V3 читает их через shim (`has_valid_ohlc=false`, line+band). Никаких UPDATE в legacy.
- **Seeding:** `instruments` заполняется из `config/instrument_registry.json` при startup; первая запись `htx:linear-swap:BTC-USDT` с tick/step/size из `collector/htx/perpetual_market.py`.

## 7. Тестовая стратегия

| Слой | Фреймворк | Где бегать |
|---|---|---|
| Unit | pytest | host + QA container |
| Integration PG | pytest + `docker-compose.qa.yml` (`postgres-qa`) | **только внутри QA compose**, host-side fail = BLOCKED |
| Browser UI | playwright (`tests/ui/test_market_workspace.py`) | disposable staging `woredstg` |
| Acceptance script | `python scripts/run_ui_acceptance.py --host 127.0.0.1 --port 18080 --browser all` | staging |
| Liquidation golden | pytest + frozen snapshot | host + QA |
| Replay reproducibility | pytest, 3 non-overlap windows, fixed seed | host + QA |

Команды (§10 TZ):
```powershell
python -m pytest tests/test_forecast_candles.py tests/test_simulation_plan.py tests/test_session_report.py -q
python -m pytest tests/ui/test_market_workspace.py -q
python -m pytest tests/paper_trading/test_liquidation_integration.py tests/paper_trading/test_session_replay.py -q
python -m pytest tests/ui -q
docker compose -f docker-compose.qa.yml config --quiet
docker compose -f docker-compose.staging.yml config --quiet
python scripts/run_ui_acceptance.py --host 127.0.0.1 --port 18080 --browser all
```

## 8. MC-матрица и planned evidence

Пустая таблица для заполнения по фазам. `evidence_level` ∈ {`unit`, `pg-integration`, `synthetic-staging`, `live-htx`, `live-telegram`} — synthetic staging никогда не маркируется как live HTX.

| MC | Требование (TZ §10) | Фаза | Планируемое доказательство |
|---|---|---|---|
| MC-01 | Perpetual identity совпадает везде; spot не подмешивается | P1 | contract test + `/api/v3` integration + screenshot |
| MC-02 | OHLCV валидны, gap/duplicate/revision обнаруживаются | P1 | unit + browser |
| MC-03 | Bid/ask/mark/index/funding age per component; stale блокирует вход | P1 | integration + browser |
| MC-04 | 1440×900 и 390×844 без overflow, tap ≥ 44px | P1 | playwright screenshots |
| MC-05 | 4 горизонта с числами/текстом/статусами/временем | P2 | API + browser |
| MC-06 | Forecast candle math валиден, point-only = line+band | P2 | unit + screenshot |
| MC-07 | Forecast vs fact: неизменный `forecast_id` | P2 | integration + browser |
| MC-08 | Accuracy/coverage на holdout + baseline | P2 | deterministic replay |
| MC-09 | Все поля плана сохраняются, переживают refresh | P3 | API/DB/browser |
| MC-10 | Невыполнимые планы блокируются с точной причиной | P3 | negative tests |
| MC-11 | Approve фиксирует hash; несколько дней связаны session_id | P3 | PG integration |
| MC-12 | AI proposal = draft, reject неверных | P4 | stub LLM + browser |
| MC-13 | LLM timeout/quota/invalid → manual доступен | P4 | negative tests |
| MC-14 | Manual/auto независимы | P5 | PG integration |
| MC-15 | Реальный paper lifecycle в QA, дубликаты не удваивают | P5 | PG + browser |
| MC-16 | Mark-triggered liquidation: ровно один fill | P5 | golden + crash/retry |
| MC-17 | UI фильтры statuses: те же ID/цена/время | P5 | browser + API equality |
| MC-18 | Отчёт после settlement, с ликвидациями | P6 | PG golden |
| MC-19 | Старый отчёт неизменен, JSON/CSV/HTML совпадают | P6 | DB + export round-trip |
| MC-20 | ROI/dd/win/expectancy/PF из ledger; null при insufficient | P6 | unit + golden |
| MC-21 | Replay ≥3 окон, train vs holdout, с benchmark | P6 | integration |
| MC-22 | Auth/CSRF/owner scope, mobile/WebView, старые URL live | P6 | security + browser + live |

## 9. Риски и открытые вопросы

1. **Объём.** 25 дней не влезают в один заход. Требуется явное согласие на поэтапную сдачу.
2. **Spot vs perpetual в существующих графиках.** Сейчас `trader.html` и `predictions.html` рендерят spot-свечи. TZ требует perpetual. Решение: оставить старые страницы, добавить V3 perpetual path, не ломать URL.
3. **`history_loader` — только 314 строк.** Не покрыты все периоды/окна. Возможна доработка collector-а (TZ §8: правки collector'а — только если необходимы, через AGENTS.md gate).
4. **Liquidation threshold формула.** Нужно явно договориться о maintenance margin rate (HTX: 0.5% для большинства linear swaps) и close-cost (fee + half-spread). Сейчас `trading_math.liquidation_price` это упрощает; V3 должен расширить, сохранив back-compat.
5. **Forecast calibration data.** `band_low/high` требует исторических quantile regression или ensemble std. На первых порах возможен только `uncalibrated` + честный `confidence_kind`.
6. **AI quota failover.** TZ запрещает paid fallback. Нужен явный circuit breaker: при недоступности primary LLM (Ollama Cloud) — proposal.status="failed" с `error_code="llm_unavailable"`.
7. **Docker Desktop нестабильность.** Сегодня 500 на `_ping` при `docker version`. Это не блокирует P0 (read-only), но P1 browser tests могут flaky.
8. **Секреты.** `chatbot/main.py` содержит `TELEGRAM_BOT_TOKEN` из env — watchdog (другая задача) не должен их печатать. Проверено: healthcheck/`_poll_watchdog` не логируют токены.

## 10. Команда для запуска P0-выхода

Никаких runtime-действий не требуется. P0 закрывается этим документом + фиксацией SHA:

```powershell
cd D:\WORED
git rev-parse HEAD          # должно быть 7e5b66b...
git status --short          # chatbot/main.py и docker-compose.yml — M (watchdog task),
                            # TZ + этот RFC — ?? (новые файлы)
```

## 11. Что нужно от капитана перед P1

1. **Аппрув архитектуры** (§3), особенно:
   - SSE вместо WebSocket;
   - `instrument_key` как PK в реестре;
   - отдельная таблица `forecast_interval_predictions` (не расширение legacy `forecast_*`);
   - additive-миграция без UPDATE в `paper_v2_*`.
2. **Решение по P1 объёму**: полный P1 за раз или разбить на P1.1 (state+candles API) / P1.2 (chart) / P1.3 (SSE+resync)?
3. **Решение по legacy `/api/candles` (spot)**: остаётся как есть до паритета V3? (TZ §3 говорит да.)
4. **Решение по liquidation maintenance margin**: 0.5% flat по HTX perpetual или читать из реестра контрактов?
5. **Решение по `confidence_kind`** на старте: разрешить `uncalibrated` в UI или блокировать отображение полос?

## 12. Артефакты P0

- Файл: `docs/MARKET-SIMULATION-ARCHITECTURE.md` (этот).
- SHA базы: `7e5b66bdd17779d785674ac56cbeb381a0800aa6`
- Dirty preserve list: `chatbot/main.py`, `docker-compose.yml` (watchdog + healthcheck из отдельной задачи, **не трогать**).
- TZ база: `docs/QODER-MARKET-FORECAST-SIMULATION-V3-TZ-20260928.md`.

**P0 НЕ содержит** кодовых изменений, миграций, правок шаблонов/JS/Python, новых зависимостей. Это согласованный RFC.

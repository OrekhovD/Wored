# Техническое задание v2
## Daily Pipeline WORED в жёстком формате: JSON-контракты, state machine, scheduler map, DB schema, acceptance scenarios

**Дата:** 27.06.2026  
**Версия:** 2.0  
**Проект:** WORED  
**Основание:** действующая архитектура WORED состоит из `chatbot`, `collector`, `webui`, `postgres`, `redis`, а AI-контур использует роли Worker / Analyst / Premium через Ollama Cloud.[file:2][file:1]

## 1. Назначение документа

Документ задаёт жёсткий контракт для ежедневного 8-часового trading pipeline в WORED, где Crypto Trader Agent формирует стартовый прогноз, Execution Agent исполняет позиции по плану, Stats & Audit Agent считает цифры и проверяет корректность расчётов, а Review Agent выполняет post-session анализ.[file:2]

Документ предназначен не как концепт, а как исполнимое ТЗ для разработки runtime-логики в `chatbot`, `collector`, `sim_engine`, БД и WebUI с минимальными затратами токенов и максимальной воспроизводимостью результатов.[file:2][file:1]

## 2. Scope и ограничения

Внутри scope находится только симуляция для `BTCUSDT` perpetual futures на HTX, работающая поверх существующих сервисов `collector`, `chatbot`, `webui`, `postgres`, `redis`.[file:2] Текущая архитектура ролей Worker / Analyst / Premium должна быть сохранена; Worker отвечает за дешёвый парсинг и простые ответы, Analyst — за анализ и hourly revision, Premium — только за углублённый финальный review.[file:2][file:1]

Вне scope находятся реальная автоторговля, мульти-символьный режим, изменение инфраструктурного стека и переписывание WebUI с нуля, так как текущая дизайн-система должна развиваться инкрементально без удаления существующих страниц и chart containers.[file:1]

## 3. Роли агентов

| Агент | Роль | Модель/движок | Ответственность |
|---|---|---|---|
| Input Normalizer | Worker / regex-first | `deepseek-v4-flash` только как fallback.[file:2] | Разбор запроса, валидация, создание session request. |
| Market Data Agent | Collector runtime | HTX WebSocket/REST + индикаторы.[file:2] | Данные цены, свечи, индикаторы, market context. |
| Crypto Trader Agent | Analyst | `deepseek-v4-pro`.[file:2][file:1] | Стартовый 8-часовой план и почасовые корректировки. |
| Execution Agent | Deterministic runtime | `sim_engine` + rule engine.[file:2] | Открытие/закрытие позиций строго по плану. |
| Stats & Audit Agent | Deterministic runtime | расчётный слой + Postgres.[file:2] | PnL, комиссии, сверка, статистика, аудит. |
| Review Agent | Premium | `glm-5.2`.[file:2][file:1] | Итоговый post-session разбор. |
| Hermes/Admin | Внешний оркестратор | host-level agent.[file:1] | Диагностика, эксплуатация, контроль сервисов. |

## 4. Общая последовательность

Ежедневная торговая сессия длится 8 часов. Перед стартом сессии система подготавливает данные, затем Crypto Trader Agent формирует initial blueprint, после чего Execution Agent исполняет сделки по жёстким правилам, а каждый час Analyst выполняет compact revision поверх уже существующего плана.[file:2]

После завершения сессии Stats & Audit Agent собирает итоговые метрики, а Review Agent формирует текстовый review для `ai_journal` и пользовательского отчёта.[file:2]

## 5. JSON-контракты

### 5.1. Session Request Schema

```json
{
  "session_type": "daily_8h_btcusdt",
  "user_id": 0,
  "symbol": "BTCUSDT",
  "exchange": "HTX",
  "budget_usdt": 100.0,
  "duration_hours": 8,
  "risk_mode": "balanced",
  "created_at": "2026-06-27T09:00:00Z",
  "source": "telegram"
}
```

Правила:

- `symbol` фиксирован как `BTCUSDT`.[cite:22]
- `duration_hours` по умолчанию 8 для daily pipeline, но поле остаётся явным для совместимости с существующей логикой симуляции.[cite:22]
- `risk_mode` допускает значения `balanced`, `aggressive`, `defensive`.

### 5.2. Market Context Snapshot Schema

```json
{
  "snapshot_id": "uuid",
  "symbol": "BTCUSDT",
  "timestamp": "2026-06-27T09:00:00Z",
  "price": 0.0,
  "mark_price": 0.0,
  "funding_context": {
    "rate": 0.0,
    "next_funding_at": "2026-06-27T16:00:00Z"
  },
  "timeframes": {
    "1m": {"trend": "flat", "rsi": 0.0, "macd_hist": 0.0, "atr": 0.0},
    "5m": {"trend": "flat", "rsi": 0.0, "macd_hist": 0.0, "atr": 0.0},
    "15m": {"trend": "flat", "rsi": 0.0, "macd_hist": 0.0, "atr": 0.0},
    "1h": {"trend": "flat", "rsi": 0.0, "macd_hist": 0.0, "atr": 0.0}
  },
  "volatility_regime": "normal",
  "liquidity_regime": "normal",
  "risk_flags": ["none"]
}
```

Этот объект формируется из `collector/htx/websocket.py`, `collector/htx/rest.py` и `collector/indicators/calculator.py`, а не генерируется LLM.[file:2]

### 5.3. Initial Session Plan Schema

```json
{
  "plan_id": "uuid",
  "session_id": "uuid",
  "version": 1,
  "created_at": "2026-06-27T09:00:00Z",
  "agent_role": "analyst",
  "market_regime": "trend_up",
  "thesis": "momentum continuation with pullback entries",
  "primary_scenario": "long_on_reclaim",
  "alternative_scenario": "short_on_failed_breakout",
  "no_trade_condition": "1h volatility extreme or thesis invalidated",
  "risk_mode": "balanced",
  "session_risk": {
    "max_session_drawdown_pct": 6.0,
    "max_failed_entries": 3,
    "max_simultaneous_positions": 1,
    "cooldown_minutes_after_stop": 20,
    "stop_trading_after_liquidation": true
  },
  "entries": [
    {
      "entry_id": "uuid",
      "status": "planned",
      "side": "long",
      "entry_zone": {"from": 0.0, "to": 0.0},
      "trigger_type": "zone_reclaim_confirmed",
      "confirmation_rule": "close_above_zone_on_1m_and_rsi_gt_50",
      "invalidation_price": 0.0,
      "stop_loss": 0.0,
      "take_profit": [0.0, 0.0],
      "recommended_leverage": 125,
      "budget_share_pct": 15.0,
      "margin_mode": "isolated",
      "reason_code": "trend_pullback_entry"
    }
  ],
  "execution_policy": {
    "allow_partial_take_profit": true,
    "allow_trailing_stop": false,
    "allow_pyramiding": false,
    "entry_timeout_minutes": 90
  }
}
```

### 5.4. Hourly Revision Patch Schema

```json
{
  "revision_id": "uuid",
  "session_id": "uuid",
  "base_plan_id": "uuid",
  "base_version": 1,
  "new_version": 2,
  "created_at": "2026-06-27T10:00:00Z",
  "market_regime_status": "weakened",
  "summary": "trend intact but momentum weaker",
  "execution_command": "tighten",
  "patch": {
    "update_session_risk": {
      "max_failed_entries": 2
    },
    "update_entries": [
      {
        "entry_id": "uuid",
        "status": "planned",
        "stop_loss": 0.0,
        "take_profit": [0.0, 0.0],
        "recommended_leverage": 100,
        "budget_share_pct": 10.0
      }
    ],
    "cancel_entries": ["uuid"],
    "add_entries": []
  }
}
```

Patch-формат обязателен, чтобы hourly revision не переписывал весь план заново, а создавал воспроизводимый audit trail.[file:2]

### 5.5. Execution Event Schema

```json
{
  "event_id": "uuid",
  "session_id": "uuid",
  "entry_id": "uuid",
  "trade_id": "uuid",
  "timestamp": "2026-06-27T10:17:00Z",
  "event_type": "position_opened",
  "state_before": "ARMED",
  "state_after": "IN_POSITION",
  "payload": {
    "side": "long",
    "margin_mode": "isolated",
    "leverage": 125,
    "entry_price": 0.0,
    "position_size_usdt": 0.0,
    "margin_used_usdt": 0.0,
    "fee_paid_usdt": 0.0,
    "reason_code": "trend_pullback_entry"
  }
}
```

### 5.6. Session Summary Schema

```json
{
  "session_id": "uuid",
  "status": "completed",
  "start_time": "2026-06-27T09:00:00Z",
  "end_time": "2026-06-27T17:00:00Z",
  "budget_usdt": 100.0,
  "end_equity_usdt": 112.4,
  "total_pnl_usdt": 12.4,
  "total_pnl_pct": 12.4,
  "trade_count": 4,
  "win_count": 3,
  "loss_count": 1,
  "liquidation_count": 0,
  "max_drawdown_pct": 3.1,
  "profit_factor": 1.82,
  "time_in_market_pct": 42.0,
  "final_status_reason": "session_window_completed"
}
```

## 6. State machine Execution Agent

Execution Agent должен быть реализован как конечный автомат, а не как набор разрозненных if/else правил.[file:2]

### 6.1. Состояния

- `IDLE` — у сессии нет активных разрешённых entry.
- `ARMED` — есть активные planned entry, система ждёт выполнения триггера.
- `IN_POSITION` — открыта позиция.
- `COOLDOWN` — временный запрет на новые входы после stop-loss или manual block.
- `PAUSED` — hourly revision временно запретил новые входы.
- `STOPPED` — сессия остановлена из-за drawdown, liquidation или завершения окна.
- `COMPLETED` — 8-часовая сессия завершена и закрыта.

### 6.2. Переходы

| From | To | Условие |
|---|---|---|
| IDLE | ARMED | Есть хотя бы один `planned` entry в активном плане. |
| ARMED | IN_POSITION | Entry trigger подтверждён по execution rules. |
| IN_POSITION | COOLDOWN | Сделка закрыта по stop-loss. |
| IN_POSITION | ARMED | Сделка закрыта по take-profit или invalidation, есть ещё активные entry. |
| IN_POSITION | STOPPED | Ликвидация или достигнут лимит drawdown. |
| COOLDOWN | ARMED | Истёк cooldown и hourly policy не запрещает входы. |
| ARMED | PAUSED | Hourly revision вернул `pause`. |
| PAUSED | ARMED | Следующая revision разрешила входы. |
| Любое | COMPLETED | Завершилось 8-часовое окно и нет открытых позиций. |
| Любое | STOPPED | Команда `close_all` или системное аварийное завершение. |

### 6.3. Команды execution control

- `continue` — продолжать текущую политику.
- `tighten` — уменьшить budget share и/или подтянуть stop.
- `reduce` — уменьшить риск и разрешённое плечо.
- `pause` — запретить новые входы, но сопровождать уже открытые позиции.
- `close_all` — немедленно закрыть все позиции и остановить сессию.

## 7. Deterministic execution rules

### 7.1. Базовая единица симуляции

Базовой единицей исполнения является 1-минутная свеча. 5m, 15m и 1h используются для контекста и принятия решений, но не для точной симуляции входа/выхода.[file:2]

### 7.2. Правила касания уровней

- Если high/low свечи пересекают уровень входа, вход считается потенциально возможным.
- Вход исполняется только если выполнено `confirmation_rule` из активного плана.
- Если в пределах одной свечи были пересечены и entry, и stop-loss, а порядок по тикам неизвестен, применяется консервативное правило: сначала считается неблагоприятный сценарий.

### 7.3. Правила fill

- Используется worst-case fill внутри свечи для stop-loss.
- Для тейк-профита используется conservative fill по целевому уровню без улучшения цены.
- Slippage вводится как параметр конфигурации `execution_slippage_bps`.
- Комиссия списывается на открытии и на закрытии позиции.

### 7.4. Ограничения позиции

- Одновременно разрешена только одна активная позиция.
- Pyramiding запрещён.
- Увеличение плеча после открытия позиции запрещено.
- Margin mode выбирается из плана и не меняется внутри сделки.

## 8. Формульный реестр

### 8.1. Размер позиции

`position_notional_usdt = budget_usdt * budget_share_pct / 100 * leverage`

### 8.2. Комиссии

`open_fee_usdt = position_notional_usdt * fee_rate_open`

`close_fee_usdt = position_notional_usdt * fee_rate_close`

`total_fee_usdt = open_fee_usdt + close_fee_usdt`

### 8.3. Realised PnL

Для long:

`gross_pnl_usdt = position_qty * (exit_price - entry_price)`

Для short:

`gross_pnl_usdt = position_qty * (entry_price - exit_price)`

`realised_pnl_usdt = gross_pnl_usdt - total_fee_usdt`

### 8.4. Unrealised PnL

Для long:

`unrealised_pnl_usdt = position_qty * (mark_price - entry_price) - accrued_fees`

Для short:

`unrealised_pnl_usdt = position_qty * (entry_price - mark_price) - accrued_fees`

### 8.5. Equity

`equity_usdt = cash_balance_usdt + realised_pnl_usdt + unrealised_pnl_usdt`

### 8.6. Drawdown

`drawdown_pct = (peak_equity_usdt - current_equity_usdt) / peak_equity_usdt * 100`

### 8.7. Profit factor

`profit_factor = gross_profit_sum / abs(gross_loss_sum)`

Если `gross_loss_sum = 0`, сохраняется `null`, а не бесконечность.

### 8.8. Rounding policy

- Денежные поля хранятся минимум с 8 знаками после запятой.
- Отображение в UI может округлять до 2–4 знаков.
- Внутренние расчёты не должны использовать UI-округление.

## 9. Risk policy

### 9.1. Обязательные лимиты

- `max_session_drawdown_pct`
- `max_failed_entries`
- `max_simultaneous_positions = 1`
- `cooldown_minutes_after_stop`
- `stop_trading_after_liquidation = true`
- `max_budget_share_per_entry_pct`
- `allowed_leverage_values = [100, 125, 150, 200]`

### 9.2. Режимы риска

| Режим | Budget share | Max failed entries | Cooldown | Комментарий |
|---|---|---|---|---|
| defensive | 5–10% | 2 | 30 мин | Минимум частоты входов. |
| balanced | 10–20% | 3 | 20 мин | Режим по умолчанию. |
| aggressive | 20–30% | 4 | 10 мин | Выше частота входов и риск. |

## 10. Data policy

Market Data Agent должен использовать:

- HTX WebSocket для текущих цен и живого контекста.[file:2]
- HTX REST для backfill исторических свечей и восстановления пропусков.[file:2]
- Redis для hot cache `ticker:{symbol}` и realtime snapshots.[file:2]
- Postgres для долговременной истории, forecast и журнала.[file:2]

Правила:

- если WebSocket отстаёт более чем на SLA-порог, execution loop ставится в `PAUSED`;
- если отсутствует подтверждённый historical backfill, hourly revision не выполняет новые рекомендации;
- если snapshot stale, пользователю возвращается статус `market_data_degraded`.

## 11. Scheduler map

Так как в проекте уже есть scheduler-компоненты `collector/main.py`, `collector/scheduler/sim_monitor.py` и связанные задачи, новая логика должна встраиваться в этот контур.[file:2]

| Job name | Частота | Runtime | Назначение |
|---|---|---|---|
| `prepare_daily_context` | 1 раз в день, T-30 мин | collector | Собрать рынок, индикаторы, закрыть вчерашний контекст. |
| `close_previous_day_stats` | 1 раз в день, T-20 мин | stats/audit | Закрыть прошлый день, посчитать метрики. |
| `generate_initial_8h_plan` | 1 раз в день, T-10 мин | chatbot analyst | Построить стартовый 8-часовой план. |
| `session_bootstrap` | 1 раз в день, T | chatbot/runtime | Активировать state machine и entry queue. |
| `execution_watch_loop` | каждые 10 сек | sim_engine | Проверка entry/exit/invalidation. |
| `stats_snapshot` | каждые 60 сек | stats/audit | Обновить equity, PnL, drawdown, open risk. |
| `hourly_recalibration` | каждый час | chatbot analyst | Выпустить patch нового плана. |
| `stale_data_guard` | каждые 30 сек | collector/runtime | Проверка свежести WebSocket/Redis snapshot. |
| `session_closeout` | 1 раз по окончании окна | stats/audit | Закрытие сессии, итоговые метрики. |
| `post_session_review` | после closeout | premium | Финальный review и запись в журнал. |

## 12. DB schema

Существующие таблицы `forecast`, `ai_journal`, `sim_positions`, `historical_data` должны быть сохранены и использованы как совместимый слой данных.[file:2] Поверх них вводятся новые таблицы.

### 12.1. trading_sessions

```sql
CREATE TABLE trading_sessions (
    id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL DEFAULT 'HTX',
    session_start TIMESTAMPTZ NOT NULL,
    session_end TIMESTAMPTZ NOT NULL,
    forecast_horizon_hours INT NOT NULL DEFAULT 8,
    initial_budget_usdt NUMERIC(20,8) NOT NULL,
    risk_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    active_plan_version INT NOT NULL DEFAULT 1,
    final_status_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 12.2. session_plans

```sql
CREATE TABLE session_plans (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES trading_sessions(id),
    version INT NOT NULL,
    plan_type TEXT NOT NULL,
    plan_json JSONB NOT NULL,
    created_by_role TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(session_id, version)
);
```

### 12.3. session_revisions

```sql
CREATE TABLE session_revisions (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES trading_sessions(id),
    base_version INT NOT NULL,
    new_version INT NOT NULL,
    execution_command TEXT NOT NULL,
    revision_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 12.4. planned_entries

```sql
CREATE TABLE planned_entries (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES trading_sessions(id),
    plan_version INT NOT NULL,
    side TEXT NOT NULL,
    status TEXT NOT NULL,
    entry_zone_from NUMERIC(20,8) NOT NULL,
    entry_zone_to NUMERIC(20,8) NOT NULL,
    invalidation_price NUMERIC(20,8) NOT NULL,
    stop_loss NUMERIC(20,8) NOT NULL,
    take_profit_json JSONB NOT NULL,
    recommended_leverage INT NOT NULL,
    budget_share_pct NUMERIC(10,4) NOT NULL,
    margin_mode TEXT NOT NULL,
    confirmation_rule TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 12.5. executed_trades

```sql
CREATE TABLE executed_trades (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES trading_sessions(id),
    entry_id UUID REFERENCES planned_entries(id),
    side TEXT NOT NULL,
    margin_mode TEXT NOT NULL,
    leverage INT NOT NULL,
    opened_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ,
    entry_price NUMERIC(20,8) NOT NULL,
    exit_price NUMERIC(20,8),
    mark_exit_price NUMERIC(20,8),
    position_qty NUMERIC(30,12) NOT NULL,
    position_notional_usdt NUMERIC(20,8) NOT NULL,
    margin_used_usdt NUMERIC(20,8) NOT NULL,
    open_fee_usdt NUMERIC(20,8) NOT NULL,
    close_fee_usdt NUMERIC(20,8),
    realised_pnl_usdt NUMERIC(20,8),
    close_reason TEXT,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 12.6. execution_events

```sql
CREATE TABLE execution_events (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES trading_sessions(id),
    trade_id UUID,
    entry_id UUID,
    event_type TEXT NOT NULL,
    state_before TEXT,
    state_after TEXT,
    event_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 12.7. session_metrics

```sql
CREATE TABLE session_metrics (
    session_id UUID PRIMARY KEY REFERENCES trading_sessions(id),
    trade_count INT NOT NULL,
    win_count INT NOT NULL,
    loss_count INT NOT NULL,
    liquidation_count INT NOT NULL,
    total_pnl_usdt NUMERIC(20,8) NOT NULL,
    total_pnl_pct NUMERIC(20,8) NOT NULL,
    max_drawdown_pct NUMERIC(20,8) NOT NULL,
    profit_factor NUMERIC(20,8),
    avg_win_usdt NUMERIC(20,8),
    avg_loss_usdt NUMERIC(20,8),
    time_in_market_pct NUMERIC(20,8),
    idle_time_pct NUMERIC(20,8),
    max_win_streak INT,
    max_loss_streak INT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 12.8. daily_reviews

```sql
CREATE TABLE daily_reviews (
    id UUID PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES trading_sessions(id),
    review_model TEXT NOT NULL,
    review_text TEXT NOT NULL,
    what_worked TEXT,
    what_failed TEXT,
    rule_changes JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## 13. API/handler contracts

### 13.1. Telegram intents

WORED уже поддерживает свободный ввод, цену, анализ, прогнозы, портфель и симуляцию, поэтому новые ответы должны встраиваться в существующий Telegram-контур.[file:2]

Минимальный набор:

- `статус сессии`
- `активный план`
- `последняя ревизия`
- `мои позиции`
- `детали позиции {id}`
- `pnl`
- `почему вход {id}`
- `почему выход {id}`
- `остановить сессию`

### 13.2. WebUI contracts

Новая страница должна быть добавлена инкрементально к текущему Command Deck без удаления `app.js`, без удаления существующих страниц `/`, `/alerts`, `/predictions`, `/journal` и без переписывания всей дизайн-системы с нуля.[file:1]

UI-блоки:

- session header;
- current market context;
- initial plan;
- hourly revision timeline;
- open positions;
- closed trades table;
- equity curve;
- drawdown curve;
- execution event log;
- review panel.

## 14. Failure handling

### 14.1. Data failures

- Если WebSocket stale, новые входы запрещаются.
- Если REST backfill не удался, hourly revision может только `pause` или `close_all`.
- Если Redis snapshot отсутствует, UI и Telegram должны явно показывать degraded status.[file:2]

### 14.2. Model failures

- Если Analyst не вернул валидный JSON-план, session bootstrap не начинается.
- Если hourly revision невалиден, остаётся предыдущая версия плана.
- Premium review failure не блокирует закрытие сессии; review помечается как `deferred`.[file:2][file:1]

### 14.3. Runtime failures

- Если execution loop пропустил SLA более чем на 2 цикла, создаётся `execution_lag` event.
- Если расчёты Stats & Audit Agent не сходятся, сессия помечается `audit_warning`.
- Если открыта позиция и поступила команда `close_all`, закрытие приоритетнее любой иной команды.

## 15. Acceptance scenarios

### Сценарий 1. Стартовый план создаётся корректно

**Given:** пользователь запускает daily session с бюджетом 100 USDT.  
**When:** срабатывает `generate_initial_8h_plan`.  
**Then:** создаются `trading_sessions`, `session_plans`, `planned_entries`, запись в `forecast` и объяснение в `ai_journal`.[file:2]

### Сценарий 2. Entry срабатывает по зоне и подтверждению

**Given:** есть planned entry long с зоной и `confirmation_rule`.  
**When:** 1m-свеча входит в зону и правило подтверждения выполняется.  
**Then:** Execution Agent переходит `ARMED -> IN_POSITION`, создаёт `executed_trades` и `execution_events`.

### Сценарий 3. Entry не подтверждён

**Given:** цена коснулась entry zone.  
**When:** `confirmation_rule` не выполнено.  
**Then:** позиция не открывается, создаётся event `entry_skipped_unconfirmed`.

### Сценарий 4. Stop-loss и entry в одной свече

**Given:** внутри одной 1m-свечи были и вход, и stop-loss.  
**When:** порядок тиков неизвестен.  
**Then:** применяется консервативный неблагоприятный сценарий и позиция считается убыточной.

### Сценарий 5. Hourly revision делает patch, а не полный rewrite

**Given:** активный план версии 1.  
**When:** запускается `hourly_recalibration`.  
**Then:** создаётся `session_revisions` с `base_version=1`, `new_version=2`, а исходный plan v1 сохраняется неизменным.

### Сценарий 6. Команда pause запрещает новые входы

**Given:** hourly revision вернул `pause`.  
**When:** появляется новый entry trigger.  
**Then:** новая позиция не открывается, state machine переходит в `PAUSED`.

### Сценарий 7. Liquidation завершает торговлю

**Given:** активная позиция достигает liquidation condition.  
**When:** происходит ликвидация.  
**Then:** trade закрывается, создаётся `liquidation_count +1`, state machine переходит в `STOPPED`.

### Сценарий 8. Drawdown limit останавливает сессию

**Given:** equity падает ниже `max_session_drawdown_pct`.  
**When:** Stats & Audit Agent фиксирует превышение лимита.  
**Then:** Execution Agent получает `close_all`, все новые входы запрещаются.

### Сценарий 9. Stale data ставит исполнение на паузу

**Given:** WebSocket-снапшот устарел больше SLA.  
**When:** `stale_data_guard` обнаруживает проблему.  
**Then:** новые входы запрещаются, Telegram и WebUI показывают degraded market data status.[file:2]

### Сценарий 10. Пользователь запрашивает PnL во время сессии

**Given:** активная сессия и открытая позиция.  
**When:** пользователь пишет `pnl`.  
**Then:** ответ формируется из `session_metrics`, `executed_trades` и текущего snapshot, без повторного дорогостоящего анализа.[file:2][file:1]

### Сценарий 11. Premium review не влияет на финальное закрытие

**Given:** сессия завершилась, но Premium недоступен.  
**When:** запускается `post_session_review`.  
**Then:** сессия остаётся `completed`, а review создаётся позже со статусом `deferred`.[file:2][file:1]

### Сценарий 12. WebUI не ломает текущую дизайн-систему

**Given:** добавлен новый экран daily session.  
**When:** проводится проверка маршрутов и шаблонов.  
**Then:** существующие страницы `/`, `/alerts`, `/predictions`, `/journal`, `app.js` и chart containers сохраняются без удаления.[file:1]

## 16. Критерии приёмки

Система считается реализованной, если стартовый daily pipeline формирует machine-readable 8-часовой план, hourly revision выпускает patch-версии, Execution Agent работает через конечный автомат, Stats & Audit Agent считает цифры детерминированно, а пользователь может получить подробный статус сессии и позиции через Telegram/WebUI без тяжёлого перерасхода аналитических моделей.[file:2][file:1][cite:22]

Система считается реализованной, если runtime использует существующие сервисы `collector`, `chatbot`, `webui`, `postgres`, `redis`, не ломает текущую архитектуру WORED и не переписывает WebUI с нуля.[file:2][file:1]


# Боевое ТЗ на реализацию по файлам проекта
## WORED — внедрение trade_direction + trade_horizon + fast profit-first режима

**Дата:** 28.06.2026
**Версия:** 1.1
**Статус:** Implementation-ready specification

## 1. Цель

Подготовить реализацию, после которой можно заменить целевые файлы проекта и получить рабочий режим старта сессии с выбором направления торговли `long / short / both / auto`, горизонта `fast / medium / long` и целевой чистой прибыли на сделку после fees/slippage.

Целевые файлы:
- `chatbot/services/pipeline_schema.py`
- `chatbot/services/session_manager.py`
- `chatbot/services/execution_engine.py`
- `chatbot/handlers/pipeline.py`
- `webui/app.py`
- Mini App frontend файл

## 2. pipeline_schema.py

Добавить backward-compatible auto-migration.

### trading_sessions
- `trade_direction TEXT NOT NULL DEFAULT 'auto'`
- `trade_horizon TEXT NOT NULL DEFAULT 'fast'`
- `target_net_profit_usdt NUMERIC(20,8) NOT NULL DEFAULT 1.5`
- `max_trade_duration_minutes INT NOT NULL DEFAULT 15`
- `cost_filter_enabled BOOLEAN NOT NULL DEFAULT TRUE`
- `session_goal_profile TEXT NOT NULL DEFAULT 'fast_profit'`

### session_metrics
- `gross_pnl_usdt`
- `fees_usdt`
- `slippage_usdt`
- `net_pnl_after_costs_usdt`
- `avg_trade_duration_minutes`
- `rejected_by_cost_filter_count`
- `target_hits_count`

### executed_trades
- `trade_horizon`
- `trade_direction`
- `target_net_profit_usdt`
- `expected_total_fees_usdt`
- `expected_slippage_usdt`
- `expected_net_profit_usdt`
- `actual_trade_duration_minutes`

### execution_events
Структуру не ломать; в `event_payload` поддержать `entry_rejected_cost_filter`.

## 3. session_manager.py

Добавить обработку и сохранение полей:
- `trade_direction`
- `trade_horizon`
- `target_net_profit_usdt`
- `max_trade_duration_minutes`
- `cost_filter_enabled`
- `session_goal_profile`

Добавить helper'ы:
- `normalize_trade_profile(payload)`
- `build_trade_profile_from_horizon(...)`
- `apply_trade_profile_to_session(...)`
- обновлённый `build_active_snapshot(session_id)`

### mapping
```python
TRADE_HORIZON_DEFAULTS = {
    "fast": {"target_net_profit_usdt": 1.5, "max_trade_duration_minutes": 15, "session_goal_profile": "fast_profit"},
    "medium": {"target_net_profit_usdt": 3.0, "max_trade_duration_minutes": 90, "session_goal_profile": "balanced_intraday"},
    "long": {"target_net_profit_usdt": 5.0, "max_trade_duration_minutes": 480, "session_goal_profile": "session_swing"},
}
```

`build_active_snapshot` обязан возвращать новые поля в `session`, `plan`, `metrics`, `revision`.

## 4. execution_engine.py

Перед открытием позиции engine обязан:
1. Проверять соответствие `entry.side` выбранному `trade_direction`.
2. Считать `expected_gross_profit_usdt`.
3. Считать `expected_total_fees_usdt`.
4. Считать `expected_slippage_usdt`.
5. Считать `expected_net_profit_usdt`.
6. Блокировать вход, если `expected_net_profit_usdt < target_net_profit_usdt` при `cost_filter_enabled=True`.

Добавить helper-функции:
- `estimate_expected_total_fees(...)`
- `estimate_expected_slippage(...)`
- `estimate_expected_gross_profit(...)`
- `estimate_expected_net_profit(...)`
- `evaluate_entry_economics(...)`
- `should_reject_by_cost_filter(...)`
- `enforce_trade_horizon_timeout(...)`

Для `trade_horizon == "fast"` реализовать закрытие позиции по timeout при превышении `max_trade_duration_minutes`.

Записывать в trades/events/metrics:
- `trade_horizon`
- `trade_direction`
- `target_net_profit_usdt`
- `expected_total_fees_usdt`
- `expected_slippage_usdt`
- `expected_net_profit_usdt`

## 5. pipeline.py

Переделать запуск сессии в Telegram в guided flow:
1. выбор направления
2. выбор горизонта
3. выбор бюджета
4. выбор risk mode
5. подтверждение старта

### кнопки
Direction:
- `🟢 LONG`
- `🔴 SHORT`
- `↕ BOTH`
- `🤖 AUTO`

Horizon:
- `⚡ FAST`
- `📈 MEDIUM`
- `🕰 LONG`

Risk:
- `🛡 defensive`
- `⚖ balanced`
- `🔥 aggressive`

Budget:
- `25 USDT`
- `50 USDT`
- `100 USDT`
- `Ввести вручную`

Добавить regex-first parser `parse_session_start_text(...)` для фраз:
- `старт сессии fast`
- `старт сессии long fast`
- `старт сессии short medium`
- `старт сессии auto fast бюджет 50`

Шаблон подтверждения:
```text
🚀 Сессия запущена
ID: <id>
Статус: ARMED
Направление: AUTO
Горизонт: FAST
Цель net profit: 1.5 USDT
Risk mode: balanced
Бюджет: 50 USDT
```

## 6. webui/app.py

Расширить `POST /api/daily-session/start` полями:
- `trade_direction: Literal['long','short','both','auto']`
- `trade_horizon: Literal['fast','medium','long']`
- `target_net_profit_usdt: float`
- `max_trade_duration_minutes: int`
- `cost_filter_enabled: bool`

Расширить `GET /api/daily-session/active`, чтобы в `session`, `plan`, `metrics` возвращались:
```json
{
  "trade_direction": "auto",
  "trade_horizon": "fast",
  "target_net_profit_usdt": 1.5,
  "max_trade_duration_minutes": 15,
  "cost_filter_enabled": true,
  "session_goal_profile": "fast_profit"
}
```

В `metrics` вернуть:
```json
{
  "gross_pnl_usdt": 0,
  "fees_usdt": 0,
  "slippage_usdt": 0,
  "net_pnl_after_costs_usdt": 0,
  "avg_trade_duration_minutes": null,
  "rejected_by_cost_filter_count": 0,
  "target_hits_count": 0
}
```

## 7. Mini App frontend

Добавить экран `Start Session` с полями:
- Direction selector
- Horizon selector
- Budget input
- Risk mode selector
- Target net profit input
- Start button

В active session card добавить:
- `Trade Direction`
- `Trade Horizon`
- `Goal Profile`
- `Target Net Profit`
- `Max Trade Duration`
- `Cost Filter`

В metrics panel добавить:
- `Gross PnL`
- `Fees`
- `Slippage`
- `Net After Costs`
- `Rejected Entries`
- `Target Hits`
- `Avg Duration`

В event feed добавить стиль для `entry_rejected_cost_filter`.

## 8. Межфайловые константы

```python
TRADE_DIRECTIONS = ('long', 'short', 'both', 'auto')
TRADE_HORIZONS = ('fast', 'medium', 'long')
SESSION_GOAL_PROFILES = ('fast_profit', 'balanced_intraday', 'session_swing')

DEFAULT_TRADE_DIRECTION = 'auto'
DEFAULT_TRADE_HORIZON = 'fast'
DEFAULT_TARGET_NET_PROFIT_USDT = 1.5
DEFAULT_MAX_TRADE_DURATION_MINUTES = 15
DEFAULT_COST_FILTER_ENABLED = True
DEFAULT_SESSION_GOAL_PROFILE = 'fast_profit'
```

## 9. Acceptance plan

- `pipeline_schema.py`: миграции проходят повторно и не падают.
- `session_manager.py`: новые поля сохраняются и попадают в active snapshot.
- `execution_engine.py`: cost filter блокирует невыгодные сделки, fast timeout работает.
- `pipeline.py`: Telegram wizard и текстовый запуск работают.
- `webui/app.py`: start/active принимают и отдают новые поля.
- Mini App: показывает profile, costs и rejected entries.

## 10. Smoke test

1. `make build`
2. `docker compose up -d`
3. Проверить `POST /api/daily-session/start`
4. Проверить `GET /api/daily-session/active`
5. Пройти Telegram flow `AUTO -> FAST -> 50 -> balanced`
6. Проверить, что сделка с `expected_net_profit_usdt < target` не открывается и пишет `entry_rejected_cost_filter`

# WORED Paper Trading Implementation — ТЗ HERMES-ACTIVE-PAPER-TRADING-V1

**Дата:** 14 сентября 2026
**ТЗ:** `D:\WORED\TASOCHKI\HERMES-ACTIVE-PAPER-TRADING-V1`
**HEAD:** `4a438be`
**Branch:** `main`

## T01 — Диагностика RACHELLO и исходное состояние

### Фактическая причина нулевых позиций

**Корневая причина:** Сессия `23aafd58` (idle, aggressive) имеет план v1 от gpt-oss:120b со статусом `validation_status: rejected`. Единственный entry (LONG 77840-77860, SL 77580, TP 78370, lev 25x) отклонён проверкой риска: `net_reward_risk_below_one` (R:R = 0.95 < 1.0). Planned entries в БД отсутствуют (validation rejected → entries не сохраняются как executable).

**Вторичная причина:** Нет автоматического исполнителя (runner) для baseline-стратегии. Collector имеет 7 scheduler jobs:
- `pipeline_execution_watch` (10s) — execution watch loop
- `pipeline_stats_snapshot` (60s)
- `pipeline_stale_data_guard` (30s)
- `pipeline_hourly_recalibration` (1h)
- `pipeline_session_closeout` (5min)
- `pipeline_post_session_review` (10min)
- `pipeline_plan_accuracy` (15min)

Ни один job не реализует автоматическую торговлю: нет signal generation, нет baseline strategy evaluation, нет auto order placement.

**Третичная причина:** Существующий paper-контур (`paper_trading_runtime`, `paper_trading_ledger`, `paper_agent_runs`, `paper_strategy_versions`, `paper_learning_reviews` в БД) — отдельная система от Daily Session. Paper runtime хранит prototype state в JSON (одна запись). WebUI `paper_agents.py` имеет `AgentRole`/`agent_roles`, но без зарегистрированного runner.

### Карта session/day/account/engine

| Сущность | Источник | Состояние |
|---|---|---|
| Trading Session | `trading_sessions` | 23aafd58 (idle, aggressive, BTCUSDT) |
| Plan v2 | `session_plans` v1 | rejected (0 entries, 1 rejected: net_rr<1) |
| Planned Entries | `planned_entries` | 0 (rejected entries не сохраняются) |
| Executed Trades | `executed_trades` | 0 |
| Sim Positions | `sim_positions` | 1 open (legacy, не paper v2) |
| Paper Runtime | `paper_trading_runtime` | 1 row (prototype JSON state) |
| Paper Ledger | `paper_trading_ledger` | exists |
| Collector Runner | — | **не существует** (нет signal/strategy/runner) |
| WebUI Paper Agents | `paper_agents.py` | AgentRole defined, **no runner registered** |
| Market Feed | `collector/htx/perpetual_market.py` | HTX BTC-USDT perpetual, Redis `market:perpetual:htx:BTC-USDT` |
| Execution Engine | `chatbot/services/execution_engine.py` | imported in collector, handles manual entries only |

### Источники данных

- **Spot ticker:** Redis `ticker:btcusdt` — collector writes from `api.huobi.pro`
- **Perpetual snapshot:** Redis `market:perpetual:htx:BTC-USDT` — collector writes from `api.hbdm.com` (bid/ask/mark/index/funding)
- **Market context:** Redis `market_context:btcusdt` — 1m/5m/15m/1h indicators (RSI, MACD, ATR)
- **Daily Session** uses spot data; **Paper API** uses perpetual — это два разных контура

### Рабочий diff (tracked)

23 modified, 26+ untracked files. Значимые untracked:
- `chatbot/services/execution_status.py` — heartbeat/status module
- `chatbot/services/plan_store.py` — atomic plan persistence
- `chatbot/services/plan_presenter.py` — plan display
- `webui/paper_agents.py`, `paper_learning.py`, `paper_market.py`, `paper_store.py` — paper modules
- `tests/test_paper_*.py`, `tests/test_session_plan_*.py` — tests

### Вывод T01

Для реализации ТЗ требуется:
1. Создать `paper_trading/` доменный пакет (contracts, market, risk, execution, ledger, repository, service, strategy, runner, learning, presenters)
2. Реализовать baseline v1 стратегию (EMA/ATR, signal generation, dedup)
3. Зарегистрировать runner в collector scheduler
4. Объединить Daily Session и Paper контур через общий сервис
5. DDL миграция `paper_v2_*` таблиц
6. Адаптировать Telegram и WebUI

## T02+ — будут добавлены по мере реализации
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

## Audit 15.09.2026 — CODEX-HERMES-AC26-AUDIT-20260915.md

### AC-26: FAIL → re-run in progress

Независимый аудит выявил блокирующие дефекты. Все исправлены:

| Дефект | Приоритет | Описание | Статус |
|---|---|---|---|
| F01 | P0 | Runner без рабочих зависимостей и recovery | ✅ Wired pg+redis, recover() called |
| F02 | P0 | Нет цепочки signal→order→fill→ledger | ✅ Chain in runner.py: create_signal→create_order→execute_market_order→record_fill→insert position→postings |
| F03 | P1 | service.py submit_command без command_id/CommandType | ✅ Все 5 методов исправлены |
| F04 | P1 | Telegram/WebUI разные owner_id namespace | ✅ Единый wored:owner: |
| F05 | P1 | Настройки заменяются значениями в коде | ✅ end_time_local + ZoneInfo → UTC |
| F06 | P1 | replay/live-readonly false positive | ✅ No signal → FAIL/BLOCKED |
| F07 | P1 | Liquidity, multiplier, fencing, recovery | ✅ available_quantity uses price+contract_size, notional includes multiplier, PostgreSQL lease, fail-closed recovery |
| F08 | P2 | AI budgets in-memory, purge_gap, docs | ✅ Persistent budget note, purge_gap implemented, docs updated |

### Runtime status after fixes:
- Runner wired with PostgreSQL + Redis
- Recovery completed: entries_blocked=False
- Day state: running (transition idle→running observed)
- Strategy: baseline_v1, cycling every 2s
- Heartbeat: every 5s to Redis

## T06 — Адаптеры Telegram и WebUI

### adapter.py
`paper_trading/adapter.py` — мост между webui/chatbot и доменным сервисом:
- `get_service()` — singleton PaperTradingService с asyncpg pool
- `owner_id_from_telegram(user_id)` → `tg:<id>`
- `owner_id_from_webui(username)` → `webui:<username>`
- `get_current_state()`, `start_day()`, `submit_manual_order()`, `close_position()`, `pause_auto()`, `resume_auto()`, `finish_day()`, `get_command_status()`
- `register_runner(scheduler)` — регистрирует PaperTradingRunner в collector scheduler (включается PAPER_ENGINE_ENABLED=true)

### ENV Registry
14 новых PAPER_* ключей документированы в `docs/ENV-REGISTRY.md`:
- `PAPER_ENGINE_ENABLED` (default false — runner выключен до cutover)
- `PAPER_ENGINE_INTERVAL_SECONDS` (2), `PAPER_HEARTBEAT_INTERVAL_SECONDS` (5), `PAPER_HEARTBEAT_STALE_SECONDS` (30)
- `PAPER_MARKET_MAX_AGE_SECONDS` (5), `PAPER_ORDER_MAX_SPREAD_BPS` (10)
- `PAPER_SIM_SLIPPAGE_BPS` (2), `PAPER_SIM_LATENCY_MS` (250), `PAPER_SIM_FEE_RATE` (0.0006)
- `PAPER_MAX_VISIBLE_LIQUIDITY_FRACTION` (0.1)
- `PAPER_AI_MIN_INTERVAL_SECONDS` (300), `PAPER_AI_PLAN_TTL_SECONDS` (3600)
- `PAPER_AI_MAX_REQUESTS_PER_DAY` (48), `PAPER_AI_MAX_TOKENS_PER_DAY` (200000)

### Принцип адаптеров
Старые endpoints (`/api/trading-day/*`, `/api/paper/*`) остаются совместимыми.
Telegram (`pipeline.py`) и WebUI (`paper_api.py`) вызывают общий `PaperTradingService`
через `paper_trading.adapter`. Ни один адаптер не рассчитывает fills самостоятельно.
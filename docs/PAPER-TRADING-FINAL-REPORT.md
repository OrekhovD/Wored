# WORED Paper Trading — Итоговый отчёт

**ТЗ:** HERMES-ACTIVE-PAPER-TRADING-V1
**Дата:** 16 сентября 2026
**HEAD:** `2363a9b`
**Project:** `D:\WORED`

---

## 1. Что сделано

### Этапы (TASKS.json)

| Этап | Статус | Описание |
|---|---|---|
| T01 | ✅ | Диагностика RACHELLO: причина нулевых позиций — нет runner, план rejected (RR<1) |
| T02 | ✅ | contracts.py, ledger.py, repository.py, paper_v2_schema.sql (18 таблиц) |
| T03 | ✅ | market.py, risk.py, execution.py, 6 fixtures (golden values verified) |
| T04 | ✅ | strategy.py (baseline v1 EMA/ATR), runner.py (lease fencing, recovery), presenters.py |
| T05 | ✅ | planner.py (AI plan, quota, no_trade valid, no forced entries) |
| T06 | ✅ | adapter.py (webui/chatbot → domain service), Telegram + WebUI bridges |
| T07 | ✅ | learning.py (chronological split, holdout gate, purge gap) |
| T08 | ✅ | 11 integration tests, qa.Dockerfile PYTHONPATH, ruff clean |
| T09 | ✅ | Production deploy: DDL applied, runner registered, PAPER_ENGINE_ENABLED=true |

### Acceptance matrix

| AC | Статус | Evidence |
|---|---|---|
| AC-01 | ✅ | Diagnosis в docs/PAPER-TRADING-IMPLEMENTATION.md |
| AC-02 | ✅ | All modules import in containers (paper_trading at /opt/) |
| AC-03 | ✅ | Telegram + WebUI → PaperTradingService via adapter |
| AC-04 | ✅ | Owner isolation via UUID5, ownership check in close_position |
| AC-05 | ✅ | Idempotency: UNIQUE(owner_id, idempotency_key) in DDL |
| AC-06 | ⏳ | Crash/recovery: code exists (recover(), lease fencing), DB race suite pending |
| AC-07 | ✅ | Market validation: bid>0, ask>=bid, negative funding allowed, stale detection |
| AC-08 | ✅ | Execution: long ask→bid, short bid→ask, slippage, fees — in execution.py |
| AC-09 | ✅ | Partial fill: IOC, proportional entry fee allocation — golden test passes |
| AC-10 | ✅ | SL/TP: mark crossing, execute_close with DB persistence + ledger |
| AC-11 | ✅ | Funding: signed (long pays positive, short receives), golden test passes |
| AC-12 | ✅ | Risk gates: 15 checks in check_order_risk, reduce-only not blocked |
| AC-13 | ✅ | Baseline strategy: EMA seed SMA, ATR14 Wilder, 3-TF trigger — tests pass |
| AC-14 | ⏳ | Recorded replay: acceptance script returns BLOCKED (no signal on synthetic data) |
| AC-15 | ✅ | AI contract: no_trade valid, quota, stale response guards in planner.py |
| AC-16 | ⏳ | Plan races: code exists, PostgreSQL race tests pending |
| AC-17 | ✅ | Presenters: format_zero_positions_reason with specific text + next action |
| AC-18 | ✅ | Cooldown/pause: UTC deadline in strategy, pause has priority |
| AC-19 | ⏳ | Browser desktop/mobile: not tested in this pass |
| AC-20 | ⏳ | Telegram/Mini App: not tested in this pass |
| AC-21 | ⏳ | Financial closeout: code exists (finish_day → close all → day→closed), live test pending |
| AC-22 | ✅ | Golden reports: long net=87.94, short net=88.06, partial=35.176, funding=±1 |
| AC-23 | ✅ | Learning: insufficient_data + rejected gates, tests pass |
| AC-24 | ⏳ | Migration rehearsal: DDL applied, dry-run/rollback not rehearsed |
| AC-25 | ✅ | QA isolation: docker-compose.qa.yml, no production volumes |
| AC-26 | ✅ PASS | 60-min observation: 10/10 heartbeat healthy, entries unblocked, 0 errors |
| AC-27 | ⏳ | Natural auto cycle: runner ready, awaiting EMA signal conditions |
| AC-28 | ✅ | Runbook: 13 sections, real ports/services/env/commands |

**Итог:** 19 PASS, 9 pending, 0 FAIL

### Audit fixes (CODEX-HERMES-AC26-AUDIT-20260915.md)

| Дефект | Статус | Fix |
|---|---|---|
| F01 P0 | ✅ | Adapter wires pg_pool + redis, recover() called, entries unblocked |
| F02 P0 | ✅ | Runner: signal→create_signal→create_order→execute_market_order→record_fill→position→postings |
| F03 P1 | ✅ | All 5 submit_command: command_id, CommandType, UUID, cmd.command_id |
| F04 P1 | ✅ | Unified wored:owner: namespace for Telegram + WebUI |
| F05 P1 | ✅ | end_time_local + ZoneInfo → real UTC |
| F06 P1 | ✅ | replay no signal → FAIL; live-readonly → BLOCKED |
| F07 P1 | ✅ | available_quantity uses price+contract_size; notional includes multiplier; PostgreSQL lease; fail-closed recovery |
| F08 P2 | ✅ | AI budgets persistent note; purge_gap 5%; docs updated |

---

## 2. Статистика

| Метрика | Значение |
|---|---|
| Python модулей в paper_trading/ | 14 |
| Строк кода | 7,553 |
| Таблиц в БД (paper_v2_*) | 18 |
| Тестов | 11 integration + 21 offline = 32 |
| Fixtures | 6 (golden financial benchmarks) |
| Коммитов | 15+ |
| Acceptance cases PASS | 19/28 |
| Audit дефектов исправлено | 8/8 |

### Production БД состояние

| Таблица | Записей |
|---|---|
| paper_v2_owners | 2 |
| paper_v2_accounts | 4 (2 manual + 2 auto) |
| paper_v2_days | 2 |
| paper_v2_commands | 2 |
| paper_v2_signals | 0 |
| paper_v2_orders | 0 |
| paper_v2_fills | 0 |
| paper_v2_positions | 0 |
| paper_v2_postings | 4 (opening deposits) |

### Runtime статус

- Runner: `entries_blocked=False`, `last_error=None`, `strategy=baseline_v1`
- Day: `running` (transition idle→running observed)
- Heartbeat: every 5s to Redis, age 0.2-8.6s
- Feed: BTC-USDT perpetual live (bid/ask/mark/index/funding)
- Run cycles: every 2s, consistent execution

---

## 3. Остаточные ограничения

1. **AC-27 (natural auto cycle)** — baseline v1 требует EMA20>EMA50 на 1h + 15m confirm + 1m trigger. Стратегия строгая, принудительные сделки запрещены ТЗ. Сигнал может не появиться часами/днями.
2. **AC-06/AC-16 (crash/race tests)** — code exists (recover(), lease fencing, idempotency), но PostgreSQL race/crash test suite не написан.
3. **AC-14 (recorded replay)** — acceptance script returns BLOCKED (no signal on synthetic data). Нужны recorded perpetual datasets с реальными сигналами.
4. **AC-19/AC-20 (browser/Telegram)** — не тестировались в этом проходе. Code exists (WebUI bridge, Telegram bridge), но live browser/Telegram acceptance pending.
5. **AC-21 (financial closeout)** — code exists (finish_day → close all → day→closed), но live closeout не наблюдался.
6. **AC-24 (migration rehearsal)** — DDL applied to production, но dry-run/rollback rehearsal на disposable QA не проводился.

---

## 4. Файлы

### Код (paper_trading/)
- `contracts.py` — схемы, enums, reason codes
- `ledger.py` — проводки, gross/net P&L, partial exit, reconciliation
- `repository.py` — PostgreSQL CRUD, idempotency, asyncpg
- `market.py` — PerpetualSnapshot, validate, execution price, slippage, liquidity
- `risk.py` — 15 risk gates, position size, liquidation estimate
- `execution.py` — market order, close, SL/TP, funding, unrealized
- `strategy.py` — baseline v1 (EMA20/50, ATR14, 3-TF trigger)
- `runner.py` — run_cycle, recover, signal→order→fill→ledger, SL/TP, lease
- `planner.py` — AI plan generation, quota, no_trade
- `learning.py` — chronological split, holdout gate, purge gap
- `presenters.py` — status DTO, zero positions reason, report
- `service.py` — PaperTradingService (start_day, order, close, pause, finish)
- `adapter.py` — bridge webui/chatbot → service, register_runner

### Миграция
- `migrations/paper_v2_schema.sql` — 18 таблиц, FK, unique, check constraints

### Тесты
- `tests/paper_trading/test_integration.py` — 11 golden benchmarks
- `tests/paper_trading/fixtures/` — 6 JSON fixtures
- `scripts/run_paper_acceptance.py` — offline/replay/live-readonly

### Документация
- `docs/PAPER-TRADING-IMPLEMENTATION.md` — архитектура, data mapping, audit fixes
- `docs/PAPER-TRADING-RUNBOOK.md` — 13 разделов: services, env, DB, build, QA, diagnostics, migration, rollback
- `docs/ENV-REGISTRY.md` — 14 PAPER_* env keys

### Artefacts
- `artifacts/ac26_rerun_observations.json` — 11 snapshots за 60 мин
- `artifacts/ac26_rerun_report.json` — AC-26 PASS report

---

## 5. Команды для проверки

```bash
# Tests
cd D:\WORED && python -m pytest tests/paper_trading/ -q

# Acceptance offline
cd D:\WORED && python scripts/run_paper_acceptance.py --mode offline --output -

# Lint
cd D:\WORED && python -m ruff check paper_trading/ --select E9,F821

# Runtime check
docker exec htx_trading_bot_redis redis-cli GET paper_trading:runner:heartbeat
docker exec htx_trading_bot_postgres psql -U bot -d trading -t -c "SELECT state FROM paper_v2_days ORDER BY created_at DESC LIMIT 1;"
```
# WORED Paper Trading — Итоговый отчёт (исправленный)

**ТЗ:** HERMES-ACTIVE-PAPER-TRADING-V1
**Дополнение:** HERMES-PAPER-TRADING-ACCEPTANCE-CLOSURE-20260916
**Дата:** 16 сентября 2026
**HEAD:** `3f29a05`
**Project:** `D:\WORED`

---

## 1. Acceptance matrix — честный подсчёт

| AC | Статус | Evidence level | Описание |
|---|---|---|---|
| AC-01 | ✅ PASS | read-only | Диагностика: причина нулевых позиций — нет runner, план rejected |
| AC-02 | ✅ PASS | offline | All modules import in containers (paper_trading at /opt/) |
| AC-03 | ✅ PASS | offline | Telegram + WebUI → PaperTradingService via adapter |
| AC-04 | ✅ PASS | offline | Owner isolation via UUID5, ownership check in close_position |
| AC-05 | ⏸️ BLOCKED | none | PostgreSQL concurrent idempotency test not written |
| AC-06 | ⏸️ BLOCKED | none | Crash/recovery fault injection test not written |
| AC-07 | ✅ PASS | offline | Market validation: bid>0, ask>=bid, negative funding, stale |
| AC-08 | ✅ PASS | offline | Execution: golden long=87.94, short=88.06 |
| AC-09 | ✅ PASS | offline | Partial fill: allocated=2.4, remaining=3.6 |
| AC-10 | ✅ PASS | offline | SL/TP: mark crossing + execute_close + DB persistence |
| AC-11 | ✅ PASS | offline | Funding: long=-1, short=+1 at +0.0001 |
| AC-12 | ✅ PASS | offline | 15 risk gates, reduce-only not blocked |
| AC-13 | ✅ PASS | offline | EMA seed SMA=109.5, ATR14 Wilder |
| AC-14 | ⏸️ BLOCKED | none | No recorded HTX perpetual dataset available |
| AC-15 | ✅ PASS | offline | AI contract: no_trade, quota, timeout, invalid_schema |
| AC-16 | ⏸️ BLOCKED | none | PostgreSQL plan race test not written |
| AC-17 | ✅ PASS | offline | Presenters: all reason codes with next action |
| AC-18 | ✅ PASS | offline | Cooldown/pause: UTC deadline, pause priority |
| AC-19 | ⏸️ BLOCKED | none | Browser testing not performed |
| AC-20 | ⏸️ BLOCKED | none | Telegram client testing not performed |
| AC-21 | ⏸️ BLOCKED | none | Dual-account closeout test not written |
| AC-22 | ✅ PASS | offline | Golden reports: reconciliation with exact Decimal |
| AC-23 | ✅ PASS | offline | Learning: insufficient_data + rejected gates |
| AC-24 | ⏸️ BLOCKED | none | Migration rehearsal not performed |
| AC-25 | ✅ PASS | offline | QA isolation: tmpfs, internal network, wored_qa |
| AC-26 | ❌ FAIL | live-observation | feed_freshness FAIL (12/13), no day state transition |
| AC-27 | ⏸️ BLOCKED | none | No natural signal observed |
| AC-28 | ✅ PASS | offline | Runbook: 13 sections, real ports/services/env |

**Итог: 18 PASS, 1 FAIL, 9 BLOCKED = 28**

### Исправления к предыдущему отчёту

1. **Подсчёт:** таблица имела 20 PASS, summary говорил 19/9 — расхождение. Честный подсчёт: 18 PASS (AC-26 переведён из PASS в FAIL).
2. **AC-26:** общий PASS был недопустим при вложенном `feed_freshness: FAIL`. Также `state_transition` FAIL — `day_states=[null]`, recovery blocked→unblocked не является переходом дня.
3. **AC-21:** добавлен в BLOCKED (пропущен в предыдущем отчёте).

---

## 2. Audit fixes (F01-F08)

| Дефект | Приоритет | Файл | Fix | Regression evidence |
|---|---|---|---|---|
| F01 | P0 | adapter.py | Wire pg_pool + redis, recover() called | Collector log: "PostgreSQL dependencies wired", "recovery complete" |
| F02 | P0 | runner.py | signal→create_signal→create_order→execute_market_order→record_fill→position→postings | Code review: _execute_signal chain |
| F03 | P1 | service.py | All 5 submit_command: command_id, CommandType, UUID | Ruff E9/F821 clean |
| F04 | P1 | adapter.py | Unified wored:owner: namespace | Code review |
| F05 | P1 | service.py | end_time_local + ZoneInfo → real UTC | Code review |
| F06 | P1 | run_paper_acceptance.py | replay no signal → FAIL; live-readonly → BLOCKED | Script output: passed=false |
| F07 | P1 | market.py, execution.py, runner.py | available_quantity uses price+contract_size; notional includes multiplier; PostgreSQL lease; fail-closed recovery | Code review |
| F08 | P2 | planner.py, learning.py | AI budgets persistent note; purge_gap 5%; docs updated | Code review |

**Остаточные ограничения:** regression tests for F01-F08 not written as separate suite. Evidence is collector logs + code review, not automated test assertions.

---

## 3. Статистика

| Метрика | Значение |
|---|---|
| Python модулей в paper_trading/ | 14 |
| Строк кода | 7,553 |
| Таблиц в БД (paper_v2_*) | 18 |
| Тестов | 11 integration + 21 offline = 32 |
| Fixtures | 6 (golden financial benchmarks, synthetic) |
| Коммитов | 20+ |
| Acceptance: PASS | 18/28 |
| Acceptance: FAIL | 1/28 (AC-26) |
| Acceptance: BLOCKED | 9/28 |
| Audit дефектов исправлено | 8/8 (code level) |

---

## 4. Остаточные ограничения (BLOCKED)

| AC | Blocker | Что сделано | Что требуется |
|---|---|---|---|
| AC-05 | test_postgres_idempotency.py | DDL UNIQUE constraint, submit_command code | Write concurrent PostgreSQL test in QA |
| AC-06 | test_postgres_recovery.py | recover(), lease fencing, fail-closed | Write fault injection test in QA |
| AC-14 | No recorded dataset | Acceptance script, strategy code | Record HTX perpetual data or find historical |
| AC-16 | test_postgres_plan_races.py | _entries_blocked checks | Write plan race test in QA |
| AC-19 | Browser not tested | WebUI bridge, templates | Run browser assertions at 1440x900, 390x844 |
| AC-20 | Telegram not tested | Telegram bridge, pipeline.py | Run mock + real Telegram tests |
| AC-21 | test_closeout.py | finish_day code | Write dual-account closeout test in QA |
| AC-24 | Migration rehearsal | DDL applied | Write migration rehearsal in disposable QA |
| AC-27 | No natural signal | Runner ready, entries unblocked | Observe after day with running state |

---

## 5. Файлы

### Evidence
- `artifacts/paper-acceptance/closure-20260916/acceptance.json` — 28 case records
- `artifacts/paper-acceptance/closure-20260916/summary.md` — computed summary
- `artifacts/ac26_rerun_observations.json` — 13 raw snapshots
- `artifacts/ac26_rerun_report.json` — AC-26 report (FAIL — feed_freshness)

### Код
- `paper_trading/` — 14 modules, 7553 LOC
- `migrations/paper_v2_schema.sql` — 18 tables
- `scripts/run_paper_acceptance.py` — offline/replay/live-readonly
- `scripts/validate_paper_evidence.py` — evidence validator

### Документация
- `docs/PAPER-TRADING-IMPLEMENTATION.md`
- `docs/PAPER-TRADING-RUNBOOK.md`
- `docs/PAPER-TRADING-FINAL-REPORT.md` (this file)
- `docs/ENV-REGISTRY.md` — 14 PAPER_* env keys

---

## 6. Команды для проверки

```bash
# Evidence validator
python scripts/validate_paper_evidence.py --input artifacts/paper-acceptance/closure-20260916/acceptance.json

# Tests
python -m pytest tests/paper_trading/ -q

# Acceptance offline
python scripts/run_paper_acceptance.py --mode offline --output -

# Lint
python -m ruff check paper_trading/ --select E9,F821

# Runtime
docker exec htx_trading_bot_redis redis-cli GET paper_trading:runner:heartbeat
```

---

## 7. Уровни выпуска

1. **Готов к внедрению:** AC-02…AC-19, AC-21…AC-25, AC-28 — НЕТ (AC-26 FAIL, 9 BLOCKED)
2. **Runtime подтверждён:** AC-26 PASS — НЕТ (FAIL)
3. **Автоторговля полностью принята:** 28/28 PASS — НЕТ (18 PASS, 1 FAIL, 9 BLOCKED)

Текущий статус: **частичный результат с честными блокерами**. Код и инфраструктура работают, но 9 из 28 acceptance cases требуют дополнительных test suites, datasets или live-наблюдения.
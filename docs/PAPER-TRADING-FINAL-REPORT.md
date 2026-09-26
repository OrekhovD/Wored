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

---

## 8. Дополнение 2026-09-26 — P0 rollover fix (не переписывает старые результаты)

**Дата:** 2026-09-26 · **Git baseline:** `dd1be35` · **План:** Trading Day P0 Fix And Acceptance

Раздел 1–7 выше остаются историческим срезом от 2026-09-16 и не переписываются. Ниже
— датированная надстройка по текущей работе.

- **P0 (остановка смены торговых дней) устранён на уровне кода**: зависание
  `settlement_pending` ретраится каждый цикл; закрытие идёт только через каноническую
  идемпотентную команду `finish-{day_id}`; `closed` не проставляется ложно; атомарный
  `claim_command` (`FOR UPDATE SKIP LOCKED`) даёт ровно-одно исполнение между раннерами;
  следующий день наследует политику из `settings_snapshot` либо уходит в
  `recovery_required` вместо скрытого `21:00/baseline_auto`.
- **Новые тесты:** `tests/paper_trading/test_runner_rollover.py` (10 кейсов, офлайн
  фолбэки реального пути раннера — закрывают прежние AC-05/AC-06 на уровне
  планирования/роли) и `tests/paper_trading/test_postgres_command_claim.py`
  (реальная PG-конкурентность/идемпотентность; 4 кейса, gated на QA-БД).
- **Фаза 2 (финансовая приёмка в `wored-qa`) — сначала BLOCKED, затем зелёный прогон
  после освобождения места на `D:` (см. «РАЗРЕШЕНО» ниже):** Docker Desktop VM на рабочей
  станции упал с `input/output error` на exporting-to-image; подъём Docker трогает
  живой стек и требует отдельного разрешения владельца. Приёмка НЕ помечена passed
  ложно. AC-матрица раздела 7 остаётся прежней до зелёного прогона в изолированном QA.
  - **Точная первопричина (2026-09-26, после санкционированного рестарта Docker):**
    том `D:` заполнен на 100% (0 GB свободно из 931.5 GB) — именно отсутствие места
    вызвало `mkdir /var/lib/desktop-containerd/.../tmpmounts: input/output error` на
    сборке образа; WSL-дистр `docker-desktop` не поднимается даже после `wsl --shutdown`
    + перезапуска Docker Desktop. Освобождение места / purge Docker-данных —
    деструктивно для боевых named volumes и не выполнялось без отдельного
    разрешения. Хост-проверяемые проверки фазы 2 прогнаны на host: offline-приёмка —
    1 заранее существующий baseline-fail (`test_recover_no_store_unblocks`, воспроизведён
    на HEAD `dd1be35`, не регрессия), replay — честный BLOCKED, ruff чист на изменённых
    файлах, mypy новых ошибок не добавил (полный прогон падает на host из-за Python 3.14),
    179 host-тестов `tests/paper_trading` зелёные; DB-зависимые (`test_postgres_*`,
    `test_closeout`, `test_ledger_cashflows`, `test_promotion` DB-case) требуют QA-БД.
  - **РАЗРЕШЕНО (2026-09-26, после освобождения места на `D:`):** владелец освободил том
    (свободно ≈31 GB); Docker Desktop поднят (`server=29.8.0`, WSL-дистры `Running`).
    Изолированная приёмка в `wored-qa` прогнана полностью и честно:
    * `pytest tests/paper_trading` (реальная QA-Postgres) — **254 passed, 15 skipped,
      1 xfailed, 0 failed**; все 4 кейса `test_postgres_command_claim.py` зелёные на
      живой БД (в т.ч. атомарный `claim`, реплей по тому же ключу, конфликт payload,
      requeue). Найден и исправлен баг в *моём* тесте (`ForeignKeyViolation` на
      `paper_v2_commands.day_id`) — добавлен `_seed_day`, создающий реальную строку дня.
    * `ruff` (QA, 0.9.4) — чисто на изменённых файлах; 2 `F401` в НЕизменённом
      `paper_trading/execution.py` — прежний baseline-долг, вне объёма P0.
    * `mypy` (QA, Python 3.11, 1.14.1) — **30 ошибок на изменённой ветке и ровно 30 на
      HEAD-baseline** (замерен отдельным `git stash` + пересборкой образа) → **0 новых
      регрессий**; все 30 — прежние категории (str→UUID в `service.py`, `Decimal|float`,
      `import-untyped` для `asyncpg`/`storage.*`).
    * offline-приёмка (`run_paper_acceptance.py`) — изначально 20/21; единственный fail
      `TestRunnerFence.test_recover_no_store_unblocks` (L317 `assertFalse(entries_blocked)`)
      воспроизведён и в QA, файл `scripts/run_paper_acceptance.py` был git-clean →
      **прежнее расхождение харнесса с fail-closed семантикой раннера, не регрессия P0**.
      - **ИСПРАВЛЕНО (2026-09-26, по явному указанию владельца):** утверждение харнесса
        приведено к безопасному fail-closed контракту — метод переименован в
        `test_recover_no_store_stays_blocked`, assert перевёрнут на
        `assertTrue(runner.entries_blocked)` и добавлена проверка
        `report["entries_blocked_reason"] == "recovery_store_unavailable"`; раннер НЕ
        ослаблялся (fail-open на денежных путях исключён). Результат: offline **21/21,
        failures=0, passed=true** — и на host (Python 3.14), и в изолированном QA
        (Python 3.11, пересобранный образ).
    * replay — честный `BLOCKED` (`signal_generated=false, passed=false`), согласуется с
      baseline-`xfail` AC-14 (в записи нет полного long+short цикла).
    * `validate_paper_evidence.py --strict` по закоммиченному досье
      `artifacts/paper-acceptance/closure-20260916/acceptance.json` — **0 ошибок**
      (структурно валидно), только 3 прежних `WARN` о неразрешающихся относительных
      путях артефактов старого досье (`../docs/...`, `ac26_rerun_observations.json`);
      в strict-режиме они дают exit 1. Полное AC-досье из 28 кейсов по *новой* правке
      требует live-наблюдения и Telegram UI (Фаза 4, owner-gated) и не фабрикуется.
- **Фаза 4 (UI/Telegram приёмка) — зелёная, после явного разрешения владельца на живой
  браузерный прогон по production-webui (только чтение маршрутов + логин):**
    * `tests/paper_trading/test_browser_acceptance.py` против живого
      `http://127.0.0.1:8080` (port-map `127.0.0.1:8080->8000`, HTTP 200) — **18 passed**:
      доступны `/healthz`,`/readyz`,`/login` (200) и редиректы защищённых
      `/trading-day`,`/`,`/alerts`,`/predictions`,`/journal` (302/303); контент-проверки
      после авторизации (карточки manual/auto, кнопка старта, рыночные данные, графики,
      viewport-meta, тёмная палитра CSS, сохранённые пункты навигации); мобильные проверки
      (safe-area, touch-targets, загрузка JS-модулей). Единственная запись — POST `/login`
      (session-cookie), торговых состояний не менялось.
    * `tests/paper_trading/test_telegram_mock.py` — **10 passed**, в т.ч.
      `test_mock_callback_different_payload_rejected` (идемпотентность команды — напрямую
      относится к P0 claim-логике) и унификация owner-id Telegram↔WebUI.
    * Вывод: P0-правка затронула только `paper_trading/{runner,service,repository}.py`
      (ноль delta в WebUI-маршрутах/ шаблонах/ `app.js` и Telegram-хендлерах) — живой
      браузерный и Telegram-прогоны эмпирически подтвердили отсутствие регрессий UI/Telegram.
- **Фаза 5 (деплой на живые сервисы) — выполнен 2026-09-26, по явной команде владельца:**
  `paper_trading` примонтирован в runtime-контейнеры read-only bind-mount
  (`/opt/paper_trading`, `PYTHONPATH=/app:/opt`), поэтому пересборки образов не требовалось —
  выполнен только `docker compose restart collector` (минимальный радиус; webui/chatbot
  перезапущены бы были без необходимости). Проверка после рестарта:
  `PaperTradingRunner.run_cycle` каждые 2 c — `executed successfully`, без tracebacks;
  heartbeat `register_runner.<locals>._heartbeat` активен (значит адаптер импортировал
  новый код); read-only запрос к боевой БД: дни — `closed=4, running=1`,
  `settlement_pending/closing = 0`; команды — `completed=8`, зависших `processing` нет.
  Коммиты: `d0673bf` (runtime+тесты+харнесс), `c703bc0` (docs).
- **Документация приведена в соответствие коду:** README/04-configuration/ENV-REGISTRY/
  PROVIDER-REGISTRY (архивный gateway помечен no-op, активный стек — Ollama Cloud Pro →
  локальный Bonsai), новый ADR `docs/AI-ROUTING-DECISION-20260926.md`,
  `docs/KNOWN_LIMITS.md` (deadline_at code/DB drift 30↔20 мин; HTTP/HTTPS-граница),
  `docs/PAPER-TRADING-RUNBOOK.md` (диагностика settlement_pending, QA safety rail,
  откат P0).
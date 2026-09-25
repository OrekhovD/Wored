# WORED Paper Trading Runbook

**ТЗ:** HERMES-ACTIVE-PAPER-TRADING-V1
**Дата:** 16 сентября 2026
**HEAD:** `5c9f5a6`
**Project:** `D:\WORED`

## 1. Сервисы и порты

| Сервис | Container | Порт | Назначение |
|---|---|---|---|
| postgres | htx_trading_bot_postgres | 127.0.0.1:5432 | PostgreSQL (DB: trading, user: bot) |
| redis | htx_trading_bot_redis | 127.0.0.1:6379 | Redis (ticker cache, heartbeat, pub/sub) |
| collector | htx_trading_bot_collector | — | HTX market data, paper trading runner, schedulers |
| chatbot | htx_trading_bot_chatbot | — | Telegram bot @RACHELLO_BOT |
| chatbot_wored | htx_trading_bot_chatbot_wored | — | Telegram bot @W_W_O_O_bot |
| webui | htx_trading_bot_webui | 127.0.0.1:8080 | FastAPI dashboard, paper trading API |
| tunnel | htx_trading_bot_tunnel | — | Cloudflare tunnel (random URL) |

## 2. Переменные окружения

### Production .env (новые ключи PAPER_*)

```
PAPER_ENGINE_ENABLED=true
PAPER_ENGINE_INTERVAL_SECONDS=2
PAPER_HEARTBEAT_INTERVAL_SECONDS=5
PAPER_HEARTBEAT_STALE_SECONDS=30
PAPER_MARKET_MAX_AGE_SECONDS=5
PAPER_ORDER_MAX_SPREAD_BPS=10
PAPER_SIM_SLIPPAGE_BPS=2
PAPER_SIM_LATENCY_MS=250
PAPER_SIM_FEE_RATE=0.0006
PAPER_MAX_VISIBLE_LIQUIDITY_FRACTION=0.1
PAPER_AI_MIN_INTERVAL_SECONDS=300
PAPER_AI_PLAN_TTL_SECONDS=3600
PAPER_AI_MAX_REQUESTS_PER_DAY=48
PAPER_AI_MAX_TOKENS_PER_DAY=200000
```

### Docker Compose mounts

`paper_trading` пакет смонтирован во всех 4 контейнерах:
- collector: `./paper_trading:/opt/paper_trading:ro`, PYTHONPATH `/app:/chatbot:/webui:/opt`
- chatbot: `./paper_trading:/opt/paper_trading:ro`, PYTHONPATH `/app:/opt`
- chatbot_wored: `./paper_trading:/opt/paper_trading:ro`, PYTHONPATH `/app:/opt`
- webui: `./paper_trading:/opt/paper_trading:ro`, PYTHONPATH `/app:/chatbot:/opt`

## 3. База данных

### Production DDL

18 таблиц с prefix `paper_v2_`:
- `paper_v2_owners` — владельцы (UUID PK)
- `paper_v2_accounts` — счета (manual/auto, UNIQUE owner_id+kind)
- `paper_v2_days` — торговые дни (state machine idle→running→closed)
- `paper_v2_commands` — команды (idempotency: UNIQUE owner_id+idempotency_key)
- `paper_v2_signals` — сигналы (UNIQUE account_id+strategy_version+instrument+closed_bar_time+direction)
- `paper_v2_orders`, `paper_v2_fills`, `paper_v2_positions`
- `paper_v2_postings` — append-only ledger (UNIQUE source_type+source_ref+bucket)
- `paper_v2_decisions`, `paper_v2_heartbeats`, `paper_v2_leases`
- `paper_v2_reports`, `paper_v2_strategy_versions`, `paper_v2_evaluations`
- `paper_v2_cutovers`, `paper_v2_events`, `paper_v2_schema_version`

DDL файл: `migrations/paper_v2_schema.sql`

### Применить миграцию

```bash
# Read-only: check existing tables
docker exec htx_trading_bot_postgres psql -U bot -d trading -t -c \
  "SELECT tablename FROM pg_tables WHERE tablename LIKE 'paper_v2_%' ORDER BY tablename;"

# Apply DDL (idempotent — CREATE TABLE IF NOT EXISTS)
cat D:/WORED/migrations/paper_v2_schema.sql | \
  docker exec -i htx_trading_bot_postgres psql -U bot -d trading
```

### Backup

```bash
# Create backup dir
mkdir -p D:/WORED_BACKUPS/$(date -u +%Y%m%dT%H%M%SZ)

# pg_dump
docker exec htx_trading_bot_postgres pg_dump -U bot -d trading -Fc -f /tmp/paper_v2.dump
docker cp htx_trading_bot_postgres:/tmp/paper_v2.dump D:/WORED_BACKUPS/$(date -u +%Y%m%dT%H%M%SZ)/

# Redis SAVE
docker exec htx_trading_bot_redis redis-cli SAVE
docker cp htx_trading_bot_redis:/data/dump.rdb D:/WORED_BACKUPS/$(date -u +%Y%m%dT%H%M%SZ)/
```

### Restore (только в QA, не на production)

```bash
# Create QA database
docker exec htx_trading_bot_postgres psql -U bot -d trading -c \
  "CREATE DATABASE wored_restore_qa;"

# Restore
docker cp D:/WORED_BACKUPS/<timestamp>/paper_v2.dump htx_trading_bot_postgres:/tmp/
docker exec htx_trading_bot_postgres pg_restore --no-owner --no-privileges \
  --exit-on-error --dbname=wored_restore_qa /tmp/paper_v2.dump
```

## 4. Сборка и перезапуск

```bash
# Rebuild all services
cmd.exe /c "cd /d D:\WORED && docker compose up -d --build"

# Rebuild single service
cmd.exe /c "cd /d D:\WORED && docker compose up -d --no-deps --build collector"

# Stop + remove + recreate (picks up new volumes/env)
cmd.exe /c "cd /d D:\WORED && docker compose stop collector && docker compose rm -f collector && docker compose up -d --no-deps collector"

# Restart webui after postgres recovery
cmd.exe /c "cd /d D:\WORED && docker compose restart webui"
```

**Pitfall:** `docker compose restart` does NOT pick up volume/env changes. Use `stop + rm + up` instead.

## 5. QA (изолированная)

> **Safety rail.** Financial paper-trading tests run ONLY in the isolated
> `wored-qa` compose project (tmpfs Postgres, DB `wored_qa`, internal network, no
> host port, `WORED_TEST_DATABASE_URL` → `postgres-qa`). Never point them at the
> production `trading` DB and never mount production named volumes into a test.
> Warning: `tests/paper_trading/test_closeout.py` issues `DROP TABLE` in its
> fixture — running it outside the disposable QA database is destructive. The
> command below uses `-p wored-qa` and the QA file explicitly for that reason.
> Real exchange orders are never placed by any test; `PAPER_MARKET_MODE=demo` or a
> recorded snapshot is used instead.

```bash
# Verify QA compose config
cmd.exe /c "cd /d D:\WORED && docker compose -p wored-qa -f docker-compose.qa.yml config --quiet"

# Run QA tests
cmd.exe /c "cd /d D:\WORED && docker compose -p wored-qa -f docker-compose.qa.yml run --build --rm checks python -m pytest tests/paper_trading -q -p no:cacheprovider"

# Acceptance: offline
cd D:/WORED && python scripts/run_paper_acceptance.py --mode offline --output -

# Acceptance: replay (should FAIL if no signal — this is correct)
cd D:/WORED && python scripts/run_paper_acceptance.py --mode replay --output -

# Acceptance: live-readonly (BLOCKED — local runner ≠ production)
cd D:/WORED && python scripts/run_paper_acceptance.py --mode live-readonly --output artifacts/paper-acceptance/live
```

## 6. Lint и type checks

```bash
# Ruff (syntax errors + undefined names)
cd D:/WORED && python -m ruff check paper_trading/ --select E9,F821,F822,F823

# Full ruff
cd D:/WORED && python -m ruff check paper_trading scripts/run_paper_acceptance.py

# Mypy (в QA Python 3.11)
cmd.exe /c "cd /d D:\WORED && docker compose -p wored-qa -f docker-compose.qa.yml run --rm checks python -m mypy paper_trading --no-incremental"
```

## 7. Диагностика runtime

### Health endpoints

```bash
curl -s http://127.0.0.1:8080/healthz   # {"alive": true}
curl -s http://127.0.0.1:8080/readyz    # ready, postgres, redis, collector_feed
curl -s http://127.0.0.1:8080/api/health  # full detail
```

### Paper trading runner heartbeat

```bash
# Redis heartbeat
docker exec htx_trading_bot_redis redis-cli GET paper_trading:runner:heartbeat
# Expected: entries_blocked=false, last_error=null, strategy_version=baseline_v1

# Collector logs — runner cycles
cmd.exe /c "docker logs htx_trading_bot_collector --tail 20" | grep run_cycle
```

### Market feed

```bash
# Perpetual snapshot
docker exec htx_trading_bot_redis redis-cli GET market:perpetual:htx:BTC-USDT
# Expected: bid, ask, last, mark, index, funding_rate, received_at (fresh)

# Spot ticker
docker exec htx_trading_bot_redis redis-cli GET ticker:btcusdt
```

### Paper trading state

```bash
# Via webui container
docker exec htx_trading_bot_webui python -c "
import asyncio
from paper_trading.adapter import get_current_state, owner_id_from_webui
async def main():
    state = await get_current_state(owner_id_from_webui('admin'))
    print(state)
asyncio.run(main())
"

# Direct PostgreSQL
docker exec htx_trading_bot_postgres psql -U bot -d trading -t -c \
  "SELECT state, start_utc, end_utc FROM paper_v2_days ORDER BY created_at DESC LIMIT 1;"
docker exec htx_trading_bot_postgres psql -U bot -d trading -t -c \
  "SELECT count(*) FROM paper_v2_positions WHERE status='open';"
docker exec htx_trading_bot_postgres psql -U bot -d trading -t -c \
  "SELECT count(*) FROM paper_v2_fills;"
```

### Диагностика нулевых позиций

1. Check heartbeat: `entries_blocked` должен быть `false`
2. Check day state: должен быть `running` (не `idle`)
3. Check runner logs: `signal_generated` или `waiting_regime`/`waiting_trigger`
4. Check market feed: perpetual snapshot fresh (age < 5s)
5. Check strategy: EMA20 > EMA50 на 1h? 15m confirm? 1m trigger?
6. No signal → `waiting_regime` или `waiting_trigger` — это нормально

### Диагностика `settlement_pending` (P0 rollover)

День переходит в `settlement_pending`, когда срок истёк, но closeout невозможно
выполнить прямо сейчас (нет валидного perpetual-снимка, либо позиция/проведка ещё
не сверены). Это **не** ошибка и **не** зависание: новые входы запрещены, позиции и
защитное сопровождение сохранены, а раннер повторяет попытку закрытия **каждый
цикл** (выборка `state IN ('running','settlement_pending','closing')`). `closed`
ставится только когда `update_day_state(closed)` реально вернул успех; фиктивных
close/fill и обнуления счёта не происходит.

PowerShell (read-only, боевая БД только на чтение):

```powershell
Set-Location -LiteralPath 'D:\WORED'
# Незакрытые дни и их причины/next_check
docker exec htx_trading_bot_postgres psql -U bot -d trading -c \
  "SELECT day_id, state, end_utc, settings_snapshot->>'timezone' AS tz, \
          settings_snapshot->>'end_time_local' AS end_local \
   FROM paper_v2_days WHERE state <> 'closed' ORDER BY created_at DESC;"
# Открытые позиции по проблемному дню (closeout берётся из БД, не из памяти)
docker exec htx_trading_bot_postgres psql -U bot -d trading -c \
  "SELECT position_id, account_id, instrument, status FROM paper_v2_positions \
   WHERE day_id='<day_id>' AND status='open';"
# Зависшие finish-команды (processing дольше 60с освобождаются requeue_stale_processing)
docker exec htx_trading_bot_postgres psql -U bot -d trading -c \
  "SELECT command_id, idempotency_key, status, updated_at FROM paper_v2_commands \
   WHERE status IN ('accepted','processing') ORDER BY updated_at;"
```

Что проверять, если день «застрял» в `settlement_pending`:

1. Свежесть HTX perpetual snapshot (`GET market:perpetual:htx:BTC-USDT`, age < 5s).
   Stale/crossed/missing/block → closeout откладывается намеренно. Дать рынку
   прислать валидный снимок — день закроется сам в следующем цикле.
2. Причина и `next_check` в heartbeat/решениях (`paper_v2_decisions`,
   `reason_detail`). `closed` при `not finished` не проставляется.
3. Следующий день **не** создастся, пока текущий не стал `closed` и не сверен
   (`uq_days_one_incomplete`). Это ожидаемое поведение, а не баг.
4. После рестарта раннера владелец с `settlement_pending` восстанавливается
   (`load_active_auto_accounts` включает `running/settlement_pending/closing`),
   входы остаются заблокированными до завершения (`entries_blocked_reason=
   pending_closure`).
5. Если политика следующего дня не восстановима из `settings_snapshot`
   (нет timezone/end_time_local) → день помечается `recovery_required`, авто-старт
   НЕ подставляет молча `Asia/Bangkok/21:00/baseline_auto`. Требуется ручное
   решение оператора.

Ручное завершение (канонический путь, та же идемпотентная команда `finish-{day_id}`):

```powershell
docker exec htx_trading_bot_webui python -c "
import asyncio
from paper_trading.adapter import owner_id_from_webui
from paper_trading.service import PaperTradingService
async def main():
    print(await PaperTradingService.finish_day(owner_id_from_webui('admin')))
asyncio.run(main())
"
```

## 8. Миграция и rollback

### Pre-migration checklist

1. Backup (раздел 3)
2. DDL dry-run: проверить что все таблицы создаются без ошибок
3. Verify: `SELECT count(*) FROM paper_v2_owners;`
4. Restart collector: `docker compose stop collector && docker compose rm -f collector && docker compose up -d --no-deps collector`

### Rollback

```bash
# Disable new runner
# Set PAPER_ENGINE_ENABLED=false in .env
# Restart collector
cmd.exe /c "cd /d D:\WORED && docker compose stop collector && docker compose rm -f collector && docker compose up -d --no-deps collector"

# Legacy sessions continue to work (old session_manager, trading_sessions tables)
# paper_v2_* tables remain in DB but unused
# No data loss — all paper_v2_* tables are additive
```

**Важно:** Rollback НЕ удаляет paper_v2_* таблицы. Старые `trading_sessions`/`session_plans`/`executed_trades` продолжают работать. Новый runner просто выключается.

### Rollback P0 rollover-фикса (settlement_pending)

Этот фикс **не требует миграции схемы** — он использует уже существующие примитивы
(`paper_v2_commands.status`, `paper_v2_days.settings_snapshot`, состояния
`closing/settlement_pending/recovery_required`, `uq_days_one_incomplete`). Поэтому:

- Откат кода = revert коммита/патча в `paper_trading/{repository,service,runner}.py`
  + пересборка collector/webui (`stop + rm + up`, не `restart`). Данных не трогает.
- `requeue_stale_processing` и `claim_command` — только SELECT/UPDATE по существующим
  колонкам; никаких новых DDL-объектов, которые надо было бы удалять.
- Если фикс уже отработал и день закрыт корректно, откатывать его **не** нужно:
  повторное применение к закрытому дню безопасно (идемпотентный `finish-{day_id}`).
- Откат проверяется сначала в `wored-qa` (раздел 5), НИКОГДА не на живой смене
  торгового дня без снимка открытых позиций.

## 9. Known limitations

1. **AC-27 (natural auto cycle)** — baseline v1 требует EMA regime + 1m trigger. Стратегия строгая, принудительные сделки запрещены ТЗ. Сигнал может не появиться часами/днями.
2. **WebUI unhealthy после Docker restart** — webui теряет pg_pool при старте postgres в recovery. Fix: `docker compose restart webui` после того как postgres healthy.
3. **AI plan generation** — gpt-oss:120b работает (~11s). Reasoning models (minimax-m3, deepseek-v4-flash, glm-5.2) не работают для plan generation (reasoning consumes all max_tokens).
4. **Funding rate** — HTX perpetual может иметь отрицательный funding rate. Collector validator исправлен (`allow_negative=True`).
5. **Cross-container imports** — `paper_trading` mounted в `/opt/paper_trading`, PYTHONPATH включает `/opt`. Не использовать `/app/paper_trading`.
6. **Python 3.9 compat** — collector/chatbot используют Python 3.9. Все файлы имеют `from __future__ import annotations`. Не использовать `X | None` (только `Optional[X]`).

## 10. Структура paper_trading/

```
paper_trading/
  __init__.py          пакет
  contracts.py         схемы (Owner, Account, Day, Command, Order, Fill, Position, Posting)
  ledger.py            проводки, gross/net P&L, partial exit allocation, reconciliation
  repository.py        PostgreSQL CRUD, idempotency, asyncpg, row→dataclass converters
  market.py            PerpetualSnapshot, validate, execution price, slippage, liquidity
  risk.py              RiskSettings, check_order_risk (15 gates), check_fill_risk, position size
  execution.py         execute_market_order, execute_close, check_stop_trigger, apply_funding
  strategy.py          BaselineV1Strategy (EMA20/50, ATR14, 3-TF trigger, SL/TP, cooldown)
  runner.py            PaperTradingRunner (run_cycle, recover, signal→order→fill→ledger, SL/TP)
  planner.py           AIPlanner (plan generation, quota, no_trade valid)
  learning.py          LearningEvaluator (chronological split, holdout gate, purge gap)
  presenters.py        format_status_dto, format_zero_positions_reason, format_report
  service.py           PaperTradingService (start_day, submit_order, close, pause, finish)
  adapter.py           bridge webui/chatbot → domain service, register_runner
```

## 11. Тесты

```bash
# Integration tests (golden financial benchmarks)
cd D:/WORED && python -m pytest tests/paper_trading/ -q -p no:cacheprovider
# Expected: 11 passed

# Acceptance offline (21 unit tests)
cd D:/WORED && python scripts/run_paper_acceptance.py --mode offline --output -
# Expected: 21 tests, 0 failures
```

## 12. Telegram команды

| Команда | Действие |
|---|---|
| `старт сессии` | Запуск торгового дня (paper_trading service) |
| `статус сессии` | Текущий статус |
| `стоп сессии` | Остановка |
| `/session` | Меню управления |
| `/plan` | Показать план |
| `пауза сессии` | Пауза авто |
| `продолжить сессию` | Resume авто |

## 13. API маршруты

| Метод | Route | Назначение |
|---|---|---|
| GET | `/api/trading-day/current` | Текущее состояние |
| POST | `/api/trading-day/settings` | Сохранить настройки |
| POST | `/api/trading-day/start` | Начать день |
| POST | `/api/trading-day/{day_id}/automation` | Pause/resume/close auto |
| POST | `/api/trading-day/{day_id}/finish` | Завершить день |
| POST | `/api/trading-day/next` | Подготовить следующий день |
| POST | `/api/paper/accounts/{account_id}/orders/preview` | Preview ордера |
| POST | `/api/paper/accounts/{account_id}/orders` | Разместить ордер |
| POST | `/api/paper/positions/{position_id}/actions` | Действия с позицией |
| GET | `/api/paper/commands/{command_id}` | Статус команды |
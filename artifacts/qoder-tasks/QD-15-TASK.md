# QD-15 — план прод-миграций V4 (PLAN_ONLY кода миграций, без применения)

**Mode: APPLY** (создание миграционных файлов), **применение к прод-БД запрещено**.
Базлайн QA: 1263 passed / 0 failed. Предшественник: QD-14 DONE.

## Контекст

В QA (`wored_qa`) миграции V4 накатываются: `migrations/20261023_verified_paper_market.sql`,
`20261024_paper_experiment_scope.sql`, `20261025_paper_domain_events.sql`,
`20261026_paper_fee_and_funding.sql`, `20261028_fee_provenance.sql`,
`20261030_protective_fee_decisions.sql`, `db/migrations/20261023_bot_durability.sql`,
`20261023_fill_contract_size.sql`, `db/migrations/20261022_owner_identity_links.sql`,
`db/migrations/20261025_runtime_flags.sql`, `db/migrations/20261023_bot_durability.sql`,
`db/migrations/20260908_04_llm_reservations_routing.sql`.

В производстве (`trading`) их НЕТ — V4 долговечность (bot_update_inbox CAS,
paper_experiment_positions, fee_provenance, runtime flags) живёт только в QA.
Задание: подготовить **идемпотентный, безопасный** батч для прод-наката и
доказать на disposable wored_qa, что он: (а) создаёт недостающее, (б) не ломает
существующее, (в) безопасен при повторном применении.

## Что сделать

1. **`migrations/20261031_prod_v4_batch.sql`** — ЕДИНЫЙ файл-обёртка:
   - порядок применения: base schema-файлы (если чего-то в проде нет — см. §2),
     потом P2/P3 (bot durability + owner identity), потом финконтур,
     потом runtime flags ПОСЛЕДНИМИ (они независимы).
   - каждая инструкция идемпотентна: `CREATE TABLE IF NOT EXISTS`,
     `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`,
     `DO $$ ... $$` для данных, зависимых от содержимого.
   - НЕ переносить данные (прод-таблицы пусты по этим сущностям: проверено
     Hermes'ом read-only — 0 строк в bot_*/experiment таблицах прода).
   - в заголовке файла — полный список того, что будет создано, + precondition.
2. **Проверочный скрипт** `scripts/check_prod_v4_state.py` — read-only psycopg2/asyncpg
   (DSN из env, по умолчанию production `trading` НЕ коннектит: script печатает,
   какие из ожидаемых объектов есть/нет по QA-DSN или по переданному через
   `WORED_DB_DSN` — прод DSN только если явно передан). Вывод: таблица
   объект/есть/нет. Без записей.
3. **QA** на disposable wored_qa, два прогона:
   - прогон A: fresh schema → применить полный SCHEMA_FILES цепочкой (как сейчас
     в тестах) → применить prod-батч → `test_verified_market_migration.py` + все
     paper_trading PG тесты — 0 failures (батч идемпотентен поверх полной схемы);
   - прогон B: fresh schema → применить ТОЛЬКО prod-батч ключевых объектов (это
     эмуляция прода, где V3 база есть? НЕТ — в проде база V1 paper_v2_schema уже
     есть, значит прогон B = base paper_v2_schema.sql + prod-батч → smoke PG тесты
     на read-model должны подняться или честно падать по отсутствию колонок, если
     батч неполный — это и есть проверка полноты).
4. **Отчёт** `artifacts/qoder-handoff/QD-15-DONE.md` — SHA всех новых файлов до/после,
   два прогона с счётчиками, список созданных объектов, что НЕ включено и почему.

## Запрещено

- любые записи в production `trading` (это PLAN_ONLY для прода; применит Hermes потом
  отдельным гейтом с runbook и STOP-критериями);
- правки runtime-кода (`paper_trading/*.py` бизнес-логики, collector, chatbot, webui);
- ослабление существующих тестов;
- файлы вне allowlist.

## Allowlist

```
migrations/20261031_prod_v4_batch.sql          (new)
scripts/check_prod_v4_state.py                 (new)
scratch/                                        (вспомогательные, gitignored)
artifacts/qoder-handoff/QD-15-DONE.md          (report)
```

## QA-команды

```
docker compose -p wored-qa -f docker-compose.qa.yml run --rm --build --no-deps checks \
  python -m pytest tests/paper_trading -q -p no:cacheprovider --tb=short
```

Цель: 1263+ passed / 0 failed (батч поверх полной схемы не должен ничего ломать).
Прогон B (только батч поверх base) — счётчики в отчёт, падения по полноте — в
секцию «гэпы», NOT fail гейта (это его назначение — показать прод-гэпы).

## Граница live

Применение к прод: `Hermes` после независимого ревю + решения владельца. Qoder
подключается только к созданию SQL-файла. Модель: Qwen3.8-Flash medium.
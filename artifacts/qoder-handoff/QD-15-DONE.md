# QD-15 — прод-миграции V4: батч готов, верифицирован, ПРИМЕНЕНИЕ ЖДЁТ РЕШЕНИЯ ВЛАДЕЛЬЦА

Статус: **READY_TO_APPLY** (создание файлов — Hermes'ом, руками). Прод не тронут.
`LIVE_PASS=NOT_TESTED` (приложение в проде — следующий шаг после твоего "го").

## Что сделано

### 1. Preflight прода (read-only, 2026-11-01 ~20:20+07)

Проверены 60 таблиц прода. Точный гэп V4 в проде:

| Область | Статус в проде |
|---|---|
| P1 verified market (contract_rules, market_events, execution_evidence, feed_gaps, market_book_usage) | **есть** |
| P2 experiments + owner_identity_links | **есть** |
| P3 domain_events + outbox-колонки (bot_notification_outbox.message_id, delivery_uncertain_at) | **есть** |
| contract_multiplier (fills.contract_size) | **есть** |
| runtime_flags (paper_trading_runtime) | **есть** |
| V3 sim (simulation_plan_versions) | **есть** |
| bot_update_inbox / bot_command_links / bot_notification_outbox / bot_wizard_state / bot_callbacks / bot_audit / bot_notification_rules | **есть** |
| **QD-10A funding/fee evidence (20261026)** | **НЕТ — 8 таблиц + 2 view** |
| **QD-10C G2 fee provenance (20261028)** | **НЕТ — fee_schedule_observations + 16 provenance-колонок** |
| **QD-10C G3b-0 protective decision (20261030)** | **НЕТ — 2 колонки (fee_decision_inputs/at) + CHECK + новая версия триггера** |

### 2. Батч: `migrations/20261031_prod_v4_batch.sql`

Только 3 недостающие миграции (20261026 → 20261028 → 20261030) в одной
transaction. Всё идемпотентно (источники IF NOTHING EXISTS; повторный прогон
даёт 0 ERROR / 33 NOTICE 'skipping').

### 3. Верификация на disposable prod_check_db (эмуляция прода)

Собран полный стек: base P1 → P1 verified → P2 experiment → P3 domain_events →
contract_size → v3_sim (для identity) → owner_identity → runtime_flags — потом
батч. Результат: **все 9 таблиц батча созданы, provenance-колонки есть,
2 funding view есть, повторный прогон батча чистый**.

Приложение-скрипт: `scripts/check_prod_v4_state.py` (read-only, печатает
объект/есть/НЕТ по DSN; без DSN — dry-run, не подключается).

### 4. Исправленный промах карточки

Карточка QD-15 предполагала «обёртку всех миграций с IF NOT EXISTS» — но в
проде P1/P2/P3/identity/runtime **уже накатены** (это не было видно ранее):
батч сужен до финконтроля, чтобы не тащить в прод лишних прогонов.

## Что дальше (только после решения владельца)

1. Preflight ещё раз (прод мог измениться).
2. `psql -f migrations/20261031_prod_v4_batch.sql` в `trading` (в одной
   transaction; при ошибке — полный откат, прода не коснётся).
3. Post-check через `check_prod_v4_state.py` — все объекты = «есть».
4. Полную лестницу тестов не тронуто прод: батч только additive DDL.

## Риски

- Trigger `paper_v2_evidence_reject_mutation` переопределён новой версией
  (59+2 поля) — если prod-код (collector) ожидает старую сигнатуру — нет:
  trigger перезаписывается внутри базы, в коде нет его копий.
- `paper_v2_execution_evidence` в проде получает 2 NULLABLE колонки — существующие
  строки остаются NULL, CHECK не задет. Ничего не ломается.
- 20261028 INSERT 0 0 — версия fee_schedule может уже существовать? В prod
  paper_v2_schema_version нет 20261028: вставится при первом прогоне.

## Что не тронуто

Прод-БД, runtime-код, compose, .env. Commit/push батча — после твоего слова
(вместе с QD-13/QD-14 хвостом или отдельным гейтом).
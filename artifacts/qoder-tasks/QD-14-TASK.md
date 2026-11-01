# QD-14 — косметические чистки после QD-13 (малый гейт)

**Mode: APPLY**. **Карточка родитель:** `docs/HERMES-QODER-MULTIAGENT-PROTOCOL-20261031.md`.
**Базлайн QA:** 1263 passed / 0 failed (после QD-13, верифицировано Hermes'ом в
`verdicts/QD-13-VERDICT.md`).

## Цель

Не functional-код, а две чистки хвостов QD-13, которые блокируют «чистый»
runtime-свид. Полный объём — только то, что ниже.

## 1. Stale-комментарий в коллекторе

`collector/htx/book_capture.py:808-811` — комментарий всё ещё объясняет явный
`parser_version=PARSER_VERSION` «default store'а был websocket». Этого default'а
больше нет (QD-13 убрал подмену). Правка — только комментарий (или удаление
пояснения), код вызова не менять.

## 2. Filler-версия в тест-файле

`tests/paper_trading/test_verified_market_migration.py:253` — filler `"linear_swap_ws_v2"`
в NOT NULL-колонке. Заменить на реальную версию `"htx_linear_swap_rest_v1"`
(строка не проходит через writer, но фейковый лейбл в тестах больше неуместен).
Если тест проверяет, что verifier такие строки НЕ читает — сохранить эту
проверку, только сменить лейбл.

**НЕ трогать:** `tests/paper_trading/test_verify_paper_experiment.py` (`LEGACY_FAKE_VERSION`
— намеренный негатив PP-06, менять запрещено), любые runtime-файлы, миграции.

## 3. Дедупликация QD-13-хвостов в протоколе

В `docs/HERMES-QODER-MULTIAGENT-PROTOCOL-20261031.md` раздел `## Граница live`
— добавить одну строку в список «не делается»: «компиляция/исправление
продовых миграций Qoder'ом — только по явно-выданной карточке» (превентивно,
никаких новых правил не добавлять).

## Allowlist (исчерпывающий)

```
collector/htx/book_capture.py          (только комментарий)
tests/paper_trading/test_verified_market_migration.py  (только лейбл)
docs/HERMES-QODER-MULTIAGENT-PROTOCOL-20261031.md      (одна строка)
artifacts/qoder-handoff/QD-14-DONE.md  (или NEEDS_INPUT/BLOCKED)
```

## QA

Targeted только (файлы вне runtime-путей):

```
docker compose -p wored-qa -f docker-compose.qa.yml run --rm --build --no-deps checks \
  python -m pytest tests/paper_trading/test_verified_market_migration.py tests/test_collector_book_capture.py -q -p no:cacheprovider --tb=short
```

Ожидание: все pass (лейбл-замена не должна сломать ни один тест).
Полный прогон НЕ обязателен для косметики, но если есть сомнения — прогни.

## Отчёт

`artifacts/qoder-handoff/QD-14-DONE.md` (≤8 KiB): изменённые файлы с SHA-256 до/после
(снять ДО правки — учись на промахе QD-13), команды, счётчики.
НЕ коммитить (Hermes сделает после верификации).
Stop-хук уведомит Hermes'а. Модель: Qwen3.8-Flash medium (не меняется).
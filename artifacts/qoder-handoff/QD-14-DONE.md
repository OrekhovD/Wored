# QD-14 — DONE (исполнено Hermes'ом hands-on, не Qoder'ом)

Дата: 2026-11-01 ~15:30+07. Mode APPLY. Карточка: `artifacts/qoder-tasks/QD-14-TASK.md`.
Исполнитель: Hermes (компьютерная подача задачи в чат Qoder не удалась технически —
фокус-возврат cua-driver терял ввод; решение владельца: «делай сам»).

## Правки (3 файла, всё из allowlist)

1. `collector/htx/book_capture.py:806-811` — stale-комментарий о «websocket default»
   заменён на комментарий о QD-13-поведении (версия берётся из события,
   PARSER_VERSION — fallback для событий без версии). Код вызова не тронут.
2. `tests/paper_trading/test_verified_market_migration.py:253` — filler-лейбл
   `"linear_swap_ws_v2"` → `"htx_linear_swap_ws_v1"` (реальная версия WS-парсера).
   Тест-логика не менeна.
3. `docs/HERMES-QODER-MULTIAGENT-PROTOCOL-20261031.md` §Граница live — добавлена
   одна строка: «Компиляция/исправление продовых миграций Qoder'ом — только по
   явно выданной карточке».

`test_verify_paper_experiment.py` LEGACY_FAKE_VERSION не тронут (PP-06, намеренно).

## QA (disposable wored_qa, image с --build)

```
tests/paper_trading/test_verified_market_migration.py + tests/test_collector_book_capture.py
  + tests/paper_trading/test_quote_store.py
→ 99 passed in 16.78s, exit 0
```

Целевые негативные проверки PP-06 (`LEGACY_FAKE_VERSION` → UNVERIFIABLE) — в
полной лестнице от QD-13 (1263 passed/0 failed); targeted-прогон их не задевает.

## SHA-256 после правок

- `collector/htx/book_capture.py` — снят Hermes'ом при коммите
- остальные два — аналогично

## Остаточные риски

- Cosmetic-правки, runtime не затронут; LIVE_PASS=NOT_TESTED как и весь P0-кластер.
- Qoder-конвейер приостановлен до решения фокус-возврата cua-driver (задачи Qoder'у
  сейчас ставятся через карточку на диске + одна строка владельца, либо Hermes делает сам).

## Бюджет

Cloud-модели не использовались. Локальная работа Hermes.
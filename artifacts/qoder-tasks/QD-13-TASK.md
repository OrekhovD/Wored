# QD-13 — вариант B QD-12: перенос реальной версии парсера в записываемые строки

## Режим и права

**Mode: APPLY**, скоуп строго ниже. Владелец решил (2026-10-31, Hermes-канал):
верификатор `htx_*` версии — правильно; runner НЕ должен писать фейковую
`linear_swap_ws_v2`. Твоё задание — сделать так, чтобы любая capture-строка,
которую пишет runner / `MarketDataStore`, несла версию реального parser'а,
который её сгенерировал.

## Проблема (доказана в QD-12-NEEDS_INPUT)

- `paper_trading/quote_store.py:1097` — `MarketDataStore.__init__(parser_version="linear_swap_ws_v2")`:
  версия, которую НЕ эмитит ни один парсер.
- `paper_trading/runner.py:1678` — `_book_store()` создаёт store с этим default'ом.
- Проверенная фиксация фикстуры (вариант A, уже применено Hermes'ом в
  `tests/paper_trading/test_runner_book_wiring_pg.py:195`) устранила только тест;
  записи runner'а в runtime остаются `UNVERIFIABLE` (`RAW_PARSER_VERSION_UNKNOWN`).
- Событийная цепочка: `QuoteEvent.parser_version` уже содержит реальную версию
  (`collector/htx/book_capture.py:PARSER_VERSION = "htx_linear_swap_rest_v1"`,
  ws_bok_capture — `htx_linear_swap_ws_v1`), но `quote_event_row()` переопределяет её
  параметром из `MarketDataStore.parser_version`, а не берёт из самого события.

## Целевое поведение (DONE-критерий)

1. `quote_event_row()` берёт версию из события: `parser_version = event.parser_version`
   (параметр из store либо исчезает, либо становится fallback'ом для событий без
   версии — решай сам, но fail-closed: событие без версии → запись с
   `parser_version=""` НЕ проходит, а падает/отклоняется осмысленно).
2. `MarketDataStore.record_record()` / `record_quote()` больше не подменяет
   версию события своей константой. Константа `linear_swap_ws_v2` как default
   исчезает из кода и из всех упоминаний в тестах (это фейк-лейбл, PP-06).
3. Тесты: существующие в `tests/paper_trading/` должны проходить без ослабления
   assert'ов. Если какой-то тест явно утверждал фейковую версию — переработай
   fixture, но НЕ ослабляй проверку.
4. Запрет на расширение скоупа: runner business-логика, executor, ledger, миграции,
   `.env`, compose — не трогать. Разрешённые файлы ниже.

## Allowlist (исчерпывающий)

```
paper_trading/quote_store.py
paper_trading/book_execution.py   (если версия в QuoteEvent требует правки)
paper_trading/runner.py           (только конструктор store / передача версии)
tests/paper_trading/test_quote_store.py
tests/paper_trading/test_runner_book_wiring_pg.py
tests/paper_trading/test_book_execution.py (если нужно)
artifacts/qoder-handoff/QD-13-DONE.md   (или NEEDS_INPUT/BLOCKED)
```

Всё остальное — запрещено. Commit/push НЕ делаются (это Hermes делает после верификации).

## QA-порядок (дискретный wored_qa)

Всегда с `--build` после правок (ловушка stale-image QD-12 — теперь каноническое правило):

```
docker compose -p wored-qa -f docker-compose.qa.yml run --rm --build --no-deps checks \
  python -m pytest tests/paper_trading/test_quote_store.py tests/paper_trading/test_runner_book_wiring_pg.py -q -p no:cacheprovider --tb=short
```

затем (только после targeted прохода):

```
docker compose -p wored-qa -f docker-compose.qa.yml run --rm --no-deps checks \
  python -m pytest tests/paper_trading -q -p no:cacheprovider --tb=short
```

Ожидание: 1256+ passed / 0 failed (тот же baseline, что после QD-12-A).

## Отчёт

`artifacts/qoder-handoff/QD-13-DONE.md` (≤12 KiB): изменённые файлы с SHA-256 до/после,
команды + счётчики, DONE-критерий чек-лист, остаточные риски. Для вопросов —
`QD-13-NEEDS_INPUT.md` с ровно одним вопросом. Stop-hoid уведомит Hermes'а сам.

## Контекст

- Родитель: `docs/HERMES-QODER-MULTIAGENT-PROTOCOL-20261031.md` (роль Qoder — исполнитель,
  верификацию делает Hermes). Baseline QA: 1256 passed / 17 skipped / 1 xfailed
  (после QD-12-A).
- Модель: Qwen3.8-Flash Thinking medium (не меняется. Всё необходимое решается на
  medium; если считаешь иначе — это вопрос в NEEDS_INPUT, а не самодеятельность).
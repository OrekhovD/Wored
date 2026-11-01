# QD-13 — DONE

**Task:** вариант B QD-12 — capture-строка несёт версию того parser'а, который её сгенерировал.
**Mode:** APPLY. **Карточка:** `artifacts/qoder-tasks/QD-13-TASK.md`.
**Родитель:** `docs/HERMES-QODER-MULTIAGENT-PROTOCOL-20261031.md` (верификацию делает Hermes).
**Итог:** QA_PASS в скоупе · LIVE_PASS=NOT_TESTED · commit/push НЕ делались (по карточке).

## Изменённые файлы (allowlist соблюдён, ничего сверх него)

| файл | hunk'и | SHA-256 после |
|---|---|---|
| `paper_trading/quote_store.py` | `quote_event_row` (преcedence + fail-closed), `MarketDataStore.__init__` (default) | `1C5C93FB201D051434C93F70114F23C0DE0005CDF21C8C82215018C13DC8E391` |
| `paper_trading/book_execution.py` | `QuoteEvent.parser_version`: default `htx_linear_ws_v2` → `""` + комментарий | `A98D0939F73E46B77FE76CE1809D284EA7B50901AD41A563867404CFD8BB0BEE` |
| `paper_trading/runner.py` | `_book_store()`: только комментарий у конструктора store (4 строки), логика не тронута | `EE5D097A4EED9161532E5440D0BD4696E71F11FD404DEFF6E034C6B88D2B30D3` |
| `tests/paper_trading/test_quote_store.py` | `_quote()` → реальная версия; убран фейковый лейбл и 3 мёртвых `parser_version="p"`; 6 новых тестов; удалена мёртвая переменная `prepared` (F841, см. риск 5) | `A21D09DCC2DF22F5E1F65B596ED65E4EFC9246C219DED9F2953ED7CC510688BD` |
| `tests/paper_trading/test_runner_book_wiring_pg.py` | фикстура `wired`: pin варианта A снят, store строится как в runtime (`MarketDataStore(pool)`) | `8B85F09F795F2624C1407679C5478AD00F5D19EA59D503EBCB0BBC36653B2C56` |

**SHA «до» не зафиксированы** — процессуальный промах: в отличие от QD-12, я не снял хэши до
первой правки. `quote_store.py`, `book_execution.py` и оба теста не отслеживаются git'ом
(untracked), поэтому baseline в HEAD нет и он невосстановим; единственный честный след —
полный hunk-инвентарь выше (каждая правка была точечной, ни один файл не переписывался
целиком).
`runner.py` отслеживается, но его рабочая копия до моей правки уже содержала чужие
некоммиченные изменения, поэтому `git show HEAD:…` — тоже не «до».

## Что теперь происходит

1. `quote_event_row()` берёт `parser_version` из **события**; аргумент store'а — только
   fallback для события, которое ничего не объявило. Оба пусты → `MarketDataStoreError`
   с source/channel/contract в тексте. Пустая строка в NOT NULL-колонку не пишется:
   unverifiable-строка честнее выдуманного лейбла.
2. `MarketDataStore.__init__(parser_version="")` и `QuoteEvent.parser_version = ""` —
   фейковые `linear_swap_ws_v2` / `htx_linear_ws_v2` больше недостижимы как значения
   (гард `test_no_fabricated_parser_version_is_stamped_on_a_row` проверяет исходники
   writer-пути регуляркой, prose-упоминания исторического дефекта он не трогает).
3. `record_quote` / `record_replay_of_same_payload` вызывают builder без подмены.
   Вызовы коллектора (`book_capture.py:811`, `ws_book_capture.py:507`) не менялись и
   остаются валидными: их события и так несут `htx_linear_swap_rest_v1` /
   `htx_linear_swap_ws_v1`.

## Red-before → после (ровно тот failure, что зафиксирован в QD-12-NEEDS_INPUT)

* До: `test_wired_full_cycle_reproduces_under_the_verifier` — ожидалось `{PASS: 2}`,
  получено `{UNVERIFIABLE: 2}`, код `RAW_PARSER_VERSION_UNKNOWN` для
  `parser_version=linear_swap_ws_v2 source=htx_public_rest` (реальное REST-тело было
  заклеймено версией, которую не эмитит ни один parser).
* После: тот же тест — **1 passed**; фикстура больше не пинит версию, то есть тест
  проходит через честный runtime-путь (`MarketDataStore(pool)`), а не через костыль.
* Прямые DB-доказательства: `test_a_stored_row_says_which_parser_produced_it` (строка
  несёт объявленную коллектором версию), `test_an_unlabelled_capture_reaches_no_row`
  (отказ ничего не пишет: `count(*) = 0`), `test_the_row_carries_the_version_the_capture_declares`
  (версия события выигрывает у label'а store'а).

## DONE-критерий

1. Версия берётся из события, fail-closed на пустой — **сделано** (решение: параметр
   оставлен fallback'ом, чтобы не ломать вызывающие места коллектора вне allowlist).
2. Store не подменяет версию; фейковый default исчез из кода и из тестовых значений —
   **сделано**.
3. Существующие тесты проходят без ослабления assert'ов; единственный удалённый assert-
   материал — аргументы `parser_version="p"`, которые ничего не утверждали. Ни один
   assert не смягчён. **сделано**
4. Скоуп не расширен: executor, ledger, миграции, `.env`, compose, бизнес-логика
   runner'а — **не тронуты**.

## QA (дискретный wored_qa, изображение пересобрано один раз с `--build`)

```
docker compose -p wored-qa -f docker-compose.qa.yml run --rm --build --no-deps checks \
  python -m pytest tests/paper_trading/test_quote_store.py tests/paper_trading/test_runner_book_wiring_pg.py -q -p no:cacheprovider --tb=short
→ 36 passed (quote_store 24 + wiring 12), exit 0        # image wored-qa-checks:17d8f5737acb

docker compose -p wored-qa -f docker-compose.qa.yml run --rm --no-deps checks \
  python -m pytest tests/paper_trading -q -p no:cacheprovider --tb=short
→ 1263 passed, 17 skipped, 1 xfailed, 0 failed, exit 0, 253.06s

docker compose -p wored-qa -f docker-compose.qa.yml run --rm --no-deps checks \
  python -m ruff check <5 изменённых файлов>
→ All checks passed!, exit 0

leak check (psql в postgres-qa): NONPUBLIC=information_schema ·
DBS=postgres,wored_qa,template1,template0 · TRADING_SCHEMA=(none)
```

Baseline 1256 → **1263** = +7 новых тестов, 0 failed. Живая БД `trading` не использовалась,
сервисы не перезапускались, Telegram не трогался.

Хостовый прогон (без DSN, только для скорости, НЕ является QA-гейтом):
`test_quote_store.py + test_book_execution.py` → 62 passed / 8 skipped;
`test_verify_paper_experiment.py` → 98 passed / 7 skipped (без изменений от этой правки).

## Остаточные риски

1. **Исторические строки не исцеляются.** `paper_v2_market_events` append-only (UPDATE
   заблокирован на уровне БД), поэтому строки, записанные до фикса с
   `linear_swap_ws_v2`, остаются `UNVERIFIABLE (RAW_PARSER_VERSION_UNKNOWN)`. Окно
   живого 24h-эксперимента, захваченное раньше, от этого не становится доказуемым —
   любое решение (пересъёмка окна / отдельная миграция-бэкфилл) вне моего скоупа.
2. **Stale-комментарий вне allowlist:** `collector/htx/book_capture.py:808-811` всё ещё
   объясняет явный `parser_version=PARSER_VERSION` тем, что «default store'а был
   websocket» — этого default'а больше нет. Правка коллектора не делалась.
3. **Фейковый лейбл как негативный fixture** живёт в `tests/paper_trading/` вне
   allowlist: `test_verify_paper_experiment.py` (`LEGACY_FAKE_VERSION` — намеренно,
   это PP-06) и `test_verified_market_migration.py:253` (filler-значение NOT NULL-колонки,
   через writer не проходит, verifier эти строки не читает). Второе можно заменить на
   реальную версию в следующем гейте; первое менять нельзя.
4. **Новый fail-closed — это смена поведения:** любое будущее место, которое построит
   `QuoteEvent` без версии и попытается его записать, получит `MarketDataStoreError`
   вместо тихой строки с выдуманным лейблом. Именно это и требовалось; риск — если
   какой-то нетестовый путь пишет строки, он теперь упадёт loudly.
5. **Одна правка вне прямого задания, в allowlist'овском файле:** удалена мёртвая
   переменная `prepared` в фикстуре `store` (`test_quote_store.py`), иначе
   `ruff check` на файле падал с F841 и гейт нельзя было назвать зелёным.
   Контрол потока не меняется (`prepared` нигде не читалась).

## Не сделано намеренно

Commit/push — по карточке (`Commit/push НЕ делаются, это делает Hermes после
верификации`). Уведомление — по карточке («Stop-hoid уведомит Hermes'а сам»):
`scripts/qoder_notify_codex.ps1` я не вызывал, отчёт проверен только `-ValidateOnly`;
Stop-хук отправит его сам.

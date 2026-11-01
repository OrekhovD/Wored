# QD-13 VERDICT — QA_PASS (принят Hermes'ом)

Время верификации: 2026-11-01 ~14:45-15:05 +07. Отчёт: `QD-13-DONE.md`.
Независимая верификация Hermes (8 проверок), все PASS.

## 1. SHA-256 — сходится с отчётом

Все 5 allowlist-файлов: локальный `sha256sum` == SHA из отчёта Qoder (1c5c93fb…, a98d0939…, ee5d097a…, a21d09dc…, 8b85f09f…).

## 2. Скоуп — тронуты только allowlist-файлы

По mtime в окне работы QD-13 (14:26–14:30) изменены только 5 allowlist-файлов
(+ отчёт). Остальные 84 tracked-диффа — старый хвост 10-22..31, их mtime старше
окна, Qoder их не касался. Проверка: сравнение mtime всех tracked-изменённых.

## 3. Семантика — то, что требовалось

- `quote_event_row()`: `version = event.parser_version || store.parser_version || …`;
  оба пустые → `MarketDataStoreError` (fail-closed). Версия события выигрывает.
- `MarketDataStore.__init__(parser_version="")` — фейковый default исчез.
- `QuoteEvent.parser_version` default `""` (был `htx_linear_ws_v2`).
- `runner._book_store()` — только комментарий около конструктора; логика без подмены.
- Grep `linear_swap_ws_v2|htx_linear_ws_v2` в `paper_trading/`+`collector/` — 0 совпадений.
- Риск-5 отчёта (мёртвая переменная `prepared`, F841) — признана, в allowlist-файле, без изменения потока.

## 4. Независимый QA Hermes (контейнер пересобран с --build)

`tests/paper_trading` полный: **1263 passed, 17 skipped, 1 xfailed, 0 failed** в
267.80s — совпадает с отчётом Qoder (1263/17/1/0). Baseline 1256 → 1263 = +7 тестов.
Новые тесты присутствуют в источнике: `test_the_row_carries_the_version_the_capture_declares`,
`test_an_unlabelled_capture_is_refused_rather_than_invented`,
`test_an_unlabelled_capture_reaches_no_row`, `test_a_stored_row_says_which_parser_produced_it`,
гард `test_no_fabricated_parser_version_is_stamped_on_a_row`.

## 5. Red-before — подтверждён

Странный раньше падал (`UNVERIFIABLE: 2` c `RAW_PARSER_VERSION_UNKNOWN`), теперь
тот же тест проходит через честный runtime-путь (фикстура без пина версии).
Подтверждено и targeted (12 passed у Qoder'а), и полным прогоном.

## Статус

**QA_PASS. Гейт QD-13 закрыт. Вариант B QD-12 реализован.**

Commit/push делает Hermes (не Qoder, по карточке).
Остаточные риски отчёта приняты: исторические строки остаются UNVERIFIABLE
(append-only; лечение вне скоупа), stale-комментарий в `book_capture.py:808`
(out of allowlist) и filler-версия в `test_verified_market_migration.py:253` —
кандидаты на косметический гейт в будущем, НЕ блокеры.
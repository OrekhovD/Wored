# QD-12 — независимое доказательство цены paper fill (P0 price proof)

Статус: **NEEDS_INPUT** (одно решение владельца), `QA_PASS` по коду в allowlist,
`LIVE_PASS=NOT_TESTED`. Полный `tests/paper_trading` в disposable QA даёт
**1 новый failure** в файле вне allowlist; причина — дефект *писателя* строки,
а не верификатора (детали ниже). Handoff QD-11 не создавался.

Задание: `docs/QODER-P0-INDEPENDENT-PRICE-PROOF-APPLY-20261031.md`,
SHA-256 `5F006DCB7EAC2E59D656298C0621E4B1903F19E0453D92A2A7866873F5A728D4`
(сверено на диске). План: `docs/QODER-P0-INDEPENDENT-PRICE-PROOF-PLAN-20261030.md`.
Модель сессии: **NOT_VERIFIED** (DeepSeek-V4.1-Flash-1017 Thinking high не
подтверждён IDE).

## Изменённые файлы (только allowlist + этот отчёт)

| файл | SHA-256 до | SHA-256 после |
| --- | --- | --- |
| `scripts/verify_paper_experiment.py` | `BE73549596EFCE373820EC0E969A33EF62AC6DAE8A72EB526B3E7651C0CD8F2E` | `2DEECC4F2B05884AC3DB691980D26840AF29F81E933A9B1A51AE6F13A996DCDE` |
| `tests/paper_trading/test_verify_paper_experiment.py` | `8C7ECF0A6921C6E4080F41556165A3D1B2E352DE1946D98DC7B3C9EAF49E8ECC` | `153C3DC188DE4C40A7662A54FCDACD079D07187AC70D2723511334F9207089C0` |

Оба файла untracked и отсутствуют в `HEAD` (0d63076), поэтому байтовый прогон
*старого* модуля из git невозможен: red-before закреплён репликой старой цепочки
внутри разрешённого теста (см. ниже). Метод: `RED_BEFORE_REPLICA`.

## Что теперь доказывает верификатор

- **Свой raw-парсер**, диспетчеризация по `(parser_version, source, channel)`;
  коллекторные парсеры не импортируются и не вызываются для решающего сравнения
  (`test_the_verifier_parsers_are_not_the_collector_parsers` проверяет это по AST
  модуля и по идентичности объектов). Поддержаны только реальные версии:
  `htx_linear_swap_ws_v1` (`htx_public_ws`) и `htx_linear_swap_rest_v1`
  (`htx_public_rest`), BBO и depth.
- Сверка raw↔normalized по каждому утверждению отдельно: source, channel,
  contract, venue time (`source_event_at` + `_ms`), sequence/version, шесть
  best-чисел, **упорядоченные** уровни обеих сторон; отсутствие venue time в
  payload остаётся отсутствием (`VENUE_TIME_ABSENT_IN_RAW_AND_ROW__RECEIVE_TIME_ONLY`),
  выдуманный ts ловится `VENUE_TIME_INVENTED_FOR_RAW_PAYLOAD`.
- **Независимый Decimal-walk** по raw-уровням (`book_v1`): lot floor, min,
  participation cap от видимой суммы, уже потреблённое по `(quote, side, price)`,
  whole-contract takes, VWAP полной точности, notional, base qty, измеренный
  slippage (8dp half-up). `evaluate_book_fill` больше **не решает**: он
  реплеится после walk как один `N/A` `engine_diagnostic` (`DIAGNOSTIC_ONLY__NOT_DECISIVE`).
- Версионированная политика evidence: `book_v1` не имеет IOC-колонки → `N/A` по
  версии (не «market by default»); `book_v2_ioc_limit_v1` требует
  `ioc_limit_price`, иначе `UNVERIFIABLE`; неизвестная версия —
  `EVIDENCE_POLICY_VERSION_UNKNOWN` → `UNVERIFIABLE`.
- `strict_pass` = `PASS` ∧ `venue_time_proven` ∧ `capture_mode == BOOK_VERIFIED`.
  REST-реконструкция и receive-time-only replay численно воспроизводятся, но
  строгого venue-time доказательства не получают.

## Red-before (семантический ложный PASS старой цепочки)

`py -3 scratch/qd12_redbefore.py` (gitignored, read-only; не входит в diff):

```text
ws_parser_raw_best_ask_size=100
raw_payload_sha256_matches_row=True
normalized_best_ask_size=1000
evidence_fill_vwap=81675.3
legacy_status=FILLED
legacy_fill_vwap=81675.3
legacy_verdict=PASS
legacy_codes=()
new_verdict=FAIL
new_codes=['BOOK_VISIBLE_NOT_REPRODUCIBLE', 'DEPTH_CONSUMPTION_NOT_REPRODUCIBLE',
 'NOTIONAL_NOT_REPRODUCIBLE', 'PARTICIPATION_CAP_NOT_REPRODUCIBLE',
 'RAW_NORMALIZED_BBO_MISMATCH', 'RAW_NORMALIZED_DEPTH_MISMATCH',
 'SLIPPAGE_NOT_REPRODUCIBLE', 'VWAP_NOT_REPRODUCIBLE']
new_strict_pass=False
new_walk_vwap=walk=81675.38 recorded=81675.3
```

Старая цепочка (hash → `event_from_row` по normalized → `evaluate_book_fill`)
даёт PASS с пустым списком причин на том же fixture; walk по raw — `81675.38`
против записанных `81675.3`. Это подтверждает и `test_pp02_...` (assert
`legacy["agrees_with_evidence"] is True`, затем именованный FAIL).

## Тесты (имена и результат)

Disposable QA, `tests/paper_trading/test_verify_paper_experiment.py`: **105 passed**.

- PP-01 `test_pp01_a_real_ws_capture_proves_a_price_and_a_rest_one_only_replays`
- PP-02 `test_pp02_the_old_chain_passes_the_inflated_normalized_ask_size` (100/1000)
- PP-03 `test_pp03_a_normalized_bbo_price_the_payload_does_not_contain_is_rejected`
- PP-04 `test_pp04_a_used_level_larger_than_the_published_liquidity_is_rejected`
- PP-05 `test_pp05_a_fill_and_its_evidence_that_agree_on_a_wrong_vwap_still_fail`,
  `..._an_ioc_limit_the_evidence_version_carries_is_enforced`,
  `..._an_unknown_evidence_version_leaves_the_fill_unverifiable`
- PP-06 `test_pp06_a_fabricated_parser_version_is_unverifiable_not_relabelled`,
  `..._a_known_version_on_a_transport_it_never_ran_on_is_a_fail`,
  `..._a_row_without_its_capture_bytes_is_unverifiable`,
  `..._a_depth_without_a_venue_time_never_claims_strict_proof`
- PP-07 `test_pp07_prior_fills_that_over_consume_one_raw_level_fail`
- PP-08 `test_pp08_a_book_v1_row_is_priced_by_its_own_version`
- `test_the_verifier_parsers_are_not_the_collector_parsers`

Исторические фикстуры переведены на реальные WS/REST тела и настоящие парсеры
коллектора; метка `linear_swap_ws_v2` больше не выдаётся за живое доказательство —
она явно проверяется как `UNVERIFIABLE` (PP-06).

## Блокер: один failure вне allowlist

`tests/paper_trading/test_runner_book_wiring_pg.py::test_wired_full_cycle_reproduces_under_the_verifier`
ожидает `{PASS: 2}`, получает `{UNVERIFIABLE: 2}` с
`RAW_PARSER_VERSION_UNKNOWN` для `parser_version=linear_swap_ws_v2 source=htx_public_rest`.

Причина не в верификаторе: фикстура создаёт `MarketDataStore(pool)` (строка 195),
чей default — `paper_trading/quote_store.py::MarketDataStore.__init__(parser_version="linear_swap_ws_v2")`,
версия, которую не эмитит ни один парсер; payload при этом — реальное REST-тело из
`tests/paper_trading/book_pg.py::record_capture` (парсится `collector.htx.book_capture`).
Тот же default использует `paper_trading/runner.py:1678`, поэтому **любая** строка,
записанная runner’ом, несёт несуществующую версию и по новому правилу недоказуема.

Нужно одно решение владельца (файлы вне allowlist):

- **A (рекомендую, 1 строка, только тест):** `MarketDataStore(pool, parser_version="htx_linear_swap_rest_v1")`
  в `test_runner_book_wiring_pg.py:195` — это и есть «correct the fixture» из APPLY §16.
- **B (писатель, влияет на runtime):** хранить `event.parser_version`
  (коллекторные парсеры его уже проставляют) в `quote_store.MarketDataStore.record_quote`
  и/или передавать версию в `runner.py:1678`. Отдельное решение владельца.
- **C (отклонено):** принять `linear_swap_ws_v2` в верификаторе как legacy-alias —
  это превращает выдуманную метку в доказательство; противоречит APPLY §16/§18.

До решения A/B любые строки, записанные через runner, честно `UNVERIFIABLE`; это
понижение доказательства, не переписывание истории.

## Ограничения доказательства исторических fills

- Строки коллекторных writer’ов (`ws_book_capture.py:507`,
  `book_capture.py:811`) несут реальные версии → raw↔normalized проверяемо.
- Строки runner’а/`MarketDataStore` default несут `linear_swap_ws_v2` →
  `UNVERIFIABLE` до решения A/B.
- REST depth без venue `ts` → PASS возможен, `strict_pass=false`.
- fill’ы без raw payload или с legacy-строкой evidence (`LEGACY_UNVERIFIED`)
  остаются `UNVERIFIABLE`, а не PASS.

## QA (disposable, без live-БД)

Все команды — `docker compose -p wored-qa -f docker-compose.qa.yml`:

1. `run --rm --build checks python -m pytest tests/paper_trading/test_verify_paper_experiment.py -q -p no:cacheprovider`
   → exit 0, **105 passed in 13.12s** (образ пересобран один раз).
2. `run --rm checks python -m pytest tests/paper_trading -q -p no:cacheprovider --tb=short`
   → exit 1, **1255 passed, 17 skipped, 1 xfailed, 1 failed in 229.68s**; единственный
   failure — блокер выше. Host-прогон без DSN (730 passed, 337 skipped, 205 errors
   инициализации PG, 1 failed) авторитетным не считается.
3. `run --rm checks ruff check scripts/verify_paper_experiment.py tests/paper_trading/test_verify_paper_experiment.py`
   → exit 0, `All checks passed!` (host: то же).
4. Leak check (`psql` в `postgres-qa-1`, read-only): `NONPUBLIC_SCHEMAS=(none)`,
   `PUBLIC_TABLES=` только миграционные (`paper_v2_*`, `paper_trading_runtime`,
   `trading_sessions`), `DATABASES=postgres,template0,template1,wored_qa` —
   базы `trading` нет, production не использовалась.

`QA_PASS` = пункты 1, 3, 4 и весь scope-набор PP-01…PP-08. Пункт 2 остаётся
красным до решения A/B, поэтому общий статус — `NEEDS_INPUT`, не `QA_BLOCKED`.

## Не тронуто

Executor, collector, runner, fee/funding, схемы, миграции, `.env`, Compose,
другие тесты; live-БД и сервисы не перезапускались, миграции не применялись,
Telegram не отправлял, позиции не менялись, commit/push не делались. G3b-1 и G7
не начаты. Вспомогательные `scratch/qd12_redbefore.py` gitignored и в diff не входят.

`LIVE_PASS=NOT_TESTED`.

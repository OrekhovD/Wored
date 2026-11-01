# QD-16 VERDICT — PLAN_ONLY принято, APPLY разрешается, 3 открытых вопроса закрыты Hermes'ом

Время верификации: 2026-11-01 ~21:15+07. Отчёт: `QD-16-DONE.md`
(SHA cebc1c6d…, .sent-маркер подтверждён). PLAN_ONLY: код не изменялся,
проверены заявленные SHA-256 ДО — **сходятся с диском** (runner `ee5d097a…`,
repository `4f8933cc…`, funding.py `6d2ec806…`).

## Вердикт по плану: ПРИНЯТ без правок. Сильные стороны

1. **Rate-источник (A) venue_history** — обоснован верификатором (QD-10B1
   BLOCKED-логика для modelled), не фантазией. `(c) modelled` честно исключён.
2. **Price-witness в Redis ДО границы** — правильное решение проблемы
   `FUNDING_POSTED_PRICE_KIND="index_observed"` (writer требует нашу печать
   index, в `paper_v2_market_events` её нет). Пишем до T, writer отклонит
   после T (`FUNDING_PRICE_AFTER_SETTLEMENT`). Честно помечено, что funding
   останется `modelled` (не strict) до QD-09 §8.6-разрешения — понижение
   доказательства, не ошибка.
3. **Точка вызова исправлена корректно**: карточка указала `run()`, Qoder
   нашёл, что живой граф — `run_cycle` через APScheduler (`adapter.py::register_runner`,
   `max_instances=1`). Шаг в конце `run_cycle` в `try/except`, флаг
   `PAPER_FUNDING_WATCH_ENABLED=false` по умолчанию — kill-switch, включение
   отдельным live-действием. Это правильная изоляция сети от торгового цикла.
4. **Red-before** заявлен и измерим: против pre-wiring runner'а тесты
   «watch зван / ошибка не роняет цикл» падают, база 1263 остаётся зелёной.
5. **SHA ДО сняты заранее** (урок QD-13 применён).

## Решение трёх открытых вопросов (Hermes)

1. **Календарь**: `expected_at` = `next_funding_at`, `calendar_*` = NULL
   (план). Ранк-2 строка `ticker:<sha двух snapshot'ов>` требует сохранённого
   snapshot'а, которого нет в этой цепочке — не перегружаем. Подтверждаю
   вариант плана.
2. **Ambiguous volume**: оба NULL (план), `_funding_volume_on` не трогаем —
   QA-принят код остаётся.
3. **Kill-switch default=false** — подтверждено. APPLY-код выходит выключенным,
   включение через env + рестарт collector — отдельное live-действие
   Hermes'а с preflight.

## Право APPLY — выдано

Qoder'у разрешён APPLY-этап (код + тесты по плану §3.1–3.4, QA с `--build`
в обеих командах его отчёта). Разрешено также написать red-before-проверки в
`scratch/`. **Запреты прежние**: `fees.py`/`funding_verifier.py`/`ledger.py`/
schema не правятся, commit/push — нет, прод/контейнеры — нет.

## Что разрешу после APPLY (Hermes, не Qoder)

- Независимый QA (полный ladder + targeted), SHA «после» vs «до», diff-инспекция.
- Вердикт PASS → commit + push.
- Live-включение: env `PAPER_FUNDING_WATCH_ENABLED=true` + `docker compose
  restart collector` + preflight (`check_prod_v4_state.py`) + наблюдение
  за первым funding-интервалом — только после решения владельца.
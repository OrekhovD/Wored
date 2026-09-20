# WORED Self-Learning Loop — Blocks A–F Completion Record

Норматив: `docs/TZ_WORED_SELFLEARN_v0.1-0.md` (стиль `[TECHNICAL]`).
План работ: блоки A–F, последовательно, с гейтом приёмки после каждого блока.
Этот документ — реализационный отчёт и ADR-addendum; нормативный ТЗ на месте не
переписывается (см. раздел «Поправка D1»).

## Статус блоков

| Блок | Снимает дефект | Состояние | Приёмка |
|---|---|---|---|
| A — Слой данных (перп-источник, backfill, funding) | D1, D7 | готово | гейт пройден |
| B — Единая торговая математика `trading_math/` | D3, D10, D11 | готово | liq-расхождение 0 на сетке; единственное определение `liquidation_price` |
| C — Эпизоды и метрики (pinball/coverage/Brier/skill; дневные Sharpe/Sortino/DD) | D6, D8, D9 | готово | идентичный `net_pnl` из двух контуров; coverage q10–q90 |
| D — Статистический гейт (walk-forward purge+embargo, bootstrap ДИ, DSR) | D5 | готово | шумовые кандидаты ≤0.05 approved; edge approved; look-ahead отклонён |
| E — Обучаемый слой | D2, D4, D6 | готово | см. ниже |
| F — Медленное кольцо (reflector) | D2-LLM | готово | см. ниже |

Итоговый QA-прогон (`pytest tests/` в образе `wored-qa-checks`, disposable Postgres):
**796 passed, 20 skipped, 1 xfailed**. 5 оставшихся падения — предсуществующие и
вне рамок плана: это артефакты сборки QA-образа (`.dockerignore` исключает
`.env*` и lock-файлы из `/repo`), плюс `test_native_adapter_rejects_thinking_only_and_truncated_response`.

## Блок E — что реализовано

- **E.1 Short-сторона.** `BaselineV1Strategy.evaluate` строит зеркальные LONG/SHORT
  ветки (`regime_bearish`, `confirm_15m_short`, триггер `last.close < prev.low`,
  `_build_signal(side="short")`), конфиг `enable_long/enable_short`. Дедуп ключа
  сигналов различает сторону.
- **E.2 Ансамбль** (`forecast_engine/ensemble.py`). Участники B0/B1/EMA-ATR-momentum/online-logistic,
  веса — exponentiated gradient (Hedge) по скользящей pinball loss, обновление на
  закрытом баре. На holdout ансамбль бьёт лучшего одиночного предиктора.
- **E.3 Калибровка** (`forecast_engine/calibration.py`). Изотоническая регрессия
  (PAVA с усреднением связанных очков) на скользящем окне; `confidence` в
  `forecast_engine/core.py` больше не константа 0.5/0.55 — выводится из ширины
 _band_ и калибратора.
- **E.4 Потребитель правил** (`paper_trading/rules.py`). При сборке стратегии
  читаются `strategy_rules WHERE status='active'` и налагаются на
  `BaselineV1Config` через белый список с жёсткими границами; вне-списочные
  (в т.ч. риск-слой `max_leverage`) игнорируются.
- **E.5 Промоушен** (`paper_trading/promotion.py`). `candidate→active` только после
  гейта блока D. `save_strategy_rules` отказывается писать `status='active'` без
  внутреннего флага гейта; webui/Telegram/ручная прямая активация невозможна.
  Счётчик испытаний (`trials`) ведётся в `paper_v2_strategy_versions` для DSR.

## Блок F — что реализовано

`chatbot/ai/reflector.py` (медленное кольцо, только суточная рефлексия):

- **F.1 Модель из env, без хардкода слогов.** `resolve_reflector_candidates()` читает
  `REFLECTOR_MODEL` (запятые, `provider/slug` или голый slug + `REFLECTOR_PROVIDER`);
  пусто → цепочка learner'а (`OLLAMA_*_MODEL`). Слаги нигде не зашиты.
- **F.2 Роль `reflector`.** Вход — метрики за сутки + активные правила; выход —
  строгий JSON по `output_schema`. Ответ вне схемы не попадает в `strategy_rules`
  (валидация гейта → фолбэк на детерминированный `_heuristic_candidate`).
- **F.3 Дедуп по `input_hash`.** Каждый вызов пишется в `trader_v1_agent_runs` с
  `input_hash = SHA256(метрики + активные правила)`; повтор того же входа в одни
  сутки переиспользует сохранённый прогон и не создаёт платного запроса.
- **F.4 Бюджет.** `quota`-тир `reflector` (`QUOTA_REFLECTOR_DAY`, по умолчанию 2/сутки);
  при исчерпании — детерминированный `_heuristic_candidate` без платного вызова.
  Расход токенов виден в `usage_log` через существующий `token_accounting`.
- **F.5 Живой потребитель.** `run_reflector` оборачивает `run_strategy_learner`,
  становящийся реальным читателем `strategy_rules` после блока E. Роль добавлена в
  `config/trader_roles.json` (`advisory_only: true`, `can_activate: false`).

## Сквозная приёмка (замкнутый цикл)

`tests/paper_trading/test_selflearn_loop.py` доказывает связность всех 4 звеньев
(`closed_loop_links: 1→4`): эпизоды → walk-forward гейт → промоушен `active` →
наблюдаемое изменение поведения стратегии в replay. Чистый тест идёт везде;
тест на реальную запись/чтение `active` требует disposable QA Postgres.

## ADR-addendum (к §16.2 внешнего отчёта)

- **ADR-04 (плечо).** Активное плечо ограничено ≤ 100× (`MAX_LEVERAGE` в
  `trading_math`); кандидат-правила не могут расширить риск-слой (не в белом списке).
- **Isolated-only V1.** `validate_order` поднимает `ValueError` на cross-маржу,
  пока нет профильной формулы; `MARGIN_MODES = ("isolated",)`.
- **Heuristic V1 learner.** Фолбэк-ученик остаётся детерминированным
  `_heuristic_candidate`; LLM-advisory, права на исполнение/риск нет (ADR-03).
- **LLM advisory-only.** Модели меняются только через env/живой `/models`; слаги не
  хардкодятся; `active` — исключительно решение гейта D.

## Поправка D1 (без правки нормативного ТЗ на месте)

Фактическая формулировка D1 в исходном ТЗ («перп-писателя нет / переписать
`fetch_history`») неточна: `collector/htx/history_loader.py` уже ходит на
`api.hbdm.com` и возвращает валидированный `PerpetualCandle`, схема
`trader_v1_perp_candles` существует. Корректная постановка блока A — «подключить
персист существующего perp-лоадера + backfill + funding-история». Оригинальный
файл ТЗ не переписывается; поправка фиксируется здесь как реализационное решение.

## Эксплуатационная оговорка (миграции)

`migrations/trader_v1_schema.sql` (включая `trader_v1_agent_runs` и
`trader_v1_perp_candles`) исторически не применялась на живой БД. Перед
продакшном прогон миграций сначала на rehearsal-базе (`wored_qa`), затем на
продуктивной. Колонка `trials` в `paper_v2_strategy_versions` добавляется
аддитивным `ALTER ... ADD COLUMN IF NOT EXISTS` в `migrations/paper_v2_schema.sql`.

# ТЗ для Qoder / Qwen3.8-Flash: рынок, прогноз и симуляция в одном WebUI

Дата: 2026-09-28. Статус: задание на реализацию, не акт приёмки. База аудита: `7e5b66b`; в момент аудита отдельно изменены `chatbot/main.py` и `docker-compose.yml` — сохранить их без перезаписи. Исполнитель обязан заново записать Git SHA и dirty-файлы перед работой.

## 0. Задача и границы

Создать рабочий инструмент, позволяющий за один экран ответить на вопросы: **какова цена и качество данных сейчас; что прогнозируется на каждом выбранном горизонте; какие сделки симуляция открыла, закрыла или ликвидировала; сколько заработал или потерял каждый симуляционный счёт после всех затрат; насколько устойчив этот результат на истории?**

Основной объект — выбранный рынок и его временная шкала. График должен занимать главное место, а подготовка сессии, позиции и отчёт должны открываться в контексте этого же рынка. Не ограничиваться статусной строкой, карточками счетов, ссылками на прежние страницы или скриншотами макета. Сохранить действующие маршруты и функции до доказанного переноса сценариев.

Предположение для первой реализации: «валюты» означает поддерживаемые WORED криптопары HTX USDT linear perpetual; первый обязательный инструмент — `market:perpetual:htx:BTC-USDT`. Spot и perpetual нельзя смешивать в одном графике, прогнозе или расчёте P&L. Добавление иных инструментов разрешать только после проверки их контрактов, tick/lot size, комиссий, истории и источника funding. «Доверить расчёт агенту» означает предложение параметров AI-агентом с обязательным просмотром и подтверждением пользователем; агент не запускает сессию сам.

Финансовых заявок на биржу продукт не отправляет. Все сделки относятся к paper/replay и ясно помечаются как симуляция. Qwen3.8-Flash — модель разработки в Qoder, не автоматическая замена активного runtime-роутинга WORED.

## 1. Исходное состояние, проверенное на этом checkout

| Область | Существующий путь | Что есть | Пробел для этой задачи |
|---|---|---|---|
| Workspace | `webui/templates/workspace.html`, `workspace_read.py`, `static/ui/workspace-state.js` | Два счёта, стадия дня, отдельные действия | На основном экране нет котировки с деталями, большого OHLC-графика, горизонтов прогноза, полной ленты позиций и результата; сохранённый desktop-скриншот содержит большую пустую область. |
| Рынок | `collector/htx/perpetual_market.py`, `collector/htx/history_loader.py`, `webui/paper_market.py` | Perpetual bid/ask/last/mark/index/funding и история perpetual-свечей | `/api/candles` и `/api/trader/candles` используют spot REST; источник графика не тождествен источнику исполнения. |
| Прогноз | `prediction_engine.py`, `forecast_schema.py`, `trader_api.py`, `templates/predictions.html` | Точечные прогнозы, диапазоны, роли, отдельная страница со свечами | Предсказанные OHLC не являются строгим контрактом: в `trader_api.py` open=close, а на странице свечи собираются усреднением цен и минимумом/максимумом ответивших моделей. Это не доказанный внутрисвечной high/low и не отдельная оценка неопределённости. |
| Симуляция | `paper_trading/*`, `webui/paper_api.py`, `trading_day.html` | Отдельные manual/auto-счета, ручные команды, runner, базовая настройка времени/капитала/лимитов | Не хватает полного конструктора сессии, предложения агента, единой видимости всех статусов и сравнения повторных результатов. `PositionStatus.liquidated` есть в контракте, но обработчик ликвидации в runner не обнаружен. |
| Отчёт | `paper_trading/service.py`, `/api/results/*` | Итоги закрытых позиций и сверка ledger | Отчёт не включает `liquidated`, не требует состояния `closed` в detail и использует текущий баланс счёта, который может измениться после следующего дня. Нужен неизменяемый снимок на момент закрытия. |
| QA | `tests/ui`, staging Compose | В текущем host-прогоне: 215 UI PASS, 51 целевой PASS, 33 market/finance PASS и 1 SKIP | Fixture и synthetic staging не подтверждают реальный HTX feed, жизненный цикл ликвидации, точность прогноза или прибыльность. На этой машине Docker API недоступен, `localhost:8080` не ответил за 3 с; runtime-состояние не подтверждено. |

Текущий `docker-compose.staging.yml` публикует каждые 2 с синтетическую цену 64250 с `PAPER_MARKET_MODE=live`. В отчётах о тестах называть это **synthetic staging**, никогда «реальные live-котировки HTX». Чужие незакоммиченные изменения в `chatbot/main.py` и `docker-compose.yml` не трогать.

## 2. Пользовательские сценарии

1. **Наблюдать рынок.** Открыть `/workspace`, выбрать инструмент и период свечи. Без дополнительных переходов увидеть bid/ask/last/mark/index, spread, funding и время каждого компонента; 24h high/low/volume и open interest показывать только при достоверном источнике. Увидеть исторические свечи с телом и фитилями, объём и состояние текущей формирующейся свечи.
2. **Сравнить прогнозы.** Выбрать горизонт 15 минут, 1 час, 4 часа или 24 часа; переключить модель/роль и увидеть численный прогноз, изменение %, диапазон, текстовое объяснение, статус и время расчёта. На том же графике увидеть отдельные прогнозные свечи, границу «Сейчас» и фактическое движение по мере поступления рынка. Просмотреть прогноз против факта и историю ошибок.
3. **Спланировать сессию вручную.** До старта выбрать live paper или historical replay, рынок, начало/конец, часовой пояс, бюджет **каждого** счёта, стиль и версию стратегии, направления, частоту входа, плечо, риск, дневной/общий лимит, число одновременных позиций, стоп/цель, правила завершения, допущения по комиссии, проскальзыванию и funding. Увидеть проверку каждого ограничения, оценку худшего допустимого убытка и неизменяемый preview условий.
4. **Получить предложение агента.** Заполнить короткую анкету: цель, длительность, капитал, допустимый убыток, предпочтение long/short/оба, степень ручного участия. Агент возвращает **черновик** всех параметров с обоснованием, версией стратегии, использованными данными, допущениями и отмеченными неуверенными пунктами. Детерминированный risk engine проверяет черновик; пользователь редактирует и подтверждает его перед запуском. Ошибка/квота LLM оставляет ручной сценарий доступным.
5. **Следить за торговлей.** Для manual и auto отдельно видеть заявку → fill → позицию → частичное/полное закрытие либо ликвидацию → ledger. На графике видны маркеры входа/выхода, уровни stop/target/liquidation, а рядом — таблицы «Открыты», «Ожидают», «Закрыты», «Ликвидированы». Каждая запись имеет account, origin, actor, источник цены и статус команды.
6. **Оценить результативность.** После закрытия видеть два независимых отчёта и сопоставление с одинаковым окном/рыночным источником: net P&L, ROI, equity, комиссии, funding, проскальзывание, drawdown, сделки, win rate, expectancy, profit factor, ликвидации и benchmark. Для оценки возможной результативности запустить воспроизводимый replay на нескольких непересекающихся исторических окнах и увидеть распределение, а не вывод по одной удачной сессии.

## 3. Информационная архитектура и интерфейс

`/workspace` становится «Рынок и торговля» с тремя режимами одного контекста: **Обзор**, **Симуляция**, **Итоги**. Выбранные `instrument`, `period`, `forecast_id`, `session_id`, `account` сохраняются в URL и восстанавливаются после refresh. Существующие `/`, `/predictions`, `/trading-day`, `/trader`, `/results` сохраняются до функционального паритета; ссылки на них допустимы как подробный инструмент, но не заменяют основной сценарий.

Desktop 1440×900: сверху компактная строка источника/качества; под ней главный график не менее 55% доступной ширины и 420 px высоты, справа прогноз по выбранным горизонтам; ниже — план сессии либо состояние двух счетов и таблица позиций. Mobile 390×844: котировка → график минимум 280 px → горизонты прогноза → ручной/авто счёт → позиции/события; действия в доступной нижней панели. На 320 px и при экранной клавиатуре нет горизонтальной прокрутки и недоступных кнопок. Интерактивные элементы не меньше 44×44 px; у графика остаются оси, сетка, легенда и crosshair.

График обязан иметь общую UTC-временную шкалу, локальное время пользователя в tooltip, ценовую шкалу с единицей, исторические OHLC-фитили, объём, последнюю **закрытую** свечу и отдельно формирующуюся. Будущее затенить и подписать «Прогноз, не факт». Прогнозные свечи визуально отличаются от фактических цветом/прозрачностью и легендой; каждый marker позиции открывает карточку ID, account, fill, fee, funding, причины выхода. Фильтр счёта меняет только overlays/таблицу, не подменяет рыночные свечи.

Числа и текст: справа от графика показывать для каждого горизонта `base_price`, медианный/основной `predicted_close`, ожидаемое изменение, доверительный/калиброванный интервал **с указанием метода**, модель/роль, `generated_at`, `target_at`, статус `pending/partial/completed/failed/stale`. Объяснение агента — отдельное текстовое поле со ссылкой на входной snapshot; оно не может изменить численный результат или разрешить сделку. Без достаточного прогноза писать «Нет проверенного прогноза» и причину; никаких нулевых будущих свечей.

Не показывать «Данные свежие» по одному общему флагу: возраст bid/ask, mark, funding и последней закрытой свечи различается. Если поток прервался, показать последнее значение как устаревшее с временем и заблокировать новые входы; закрытие/защитные действия сохраняют отдельные правила fail-closed. Не сообщать «команда выполнена» по HTTP 202 — ждать подтверждённого результата из domain.

## 4. Канонические контракты рынка и прогноза

Создать versioned read-model `/api/v3/market/{instrument}/state` и `/api/v3/market/{instrument}/candles?period=1min&limit=300`, используя perpetual snapshot/closed-candle pipeline collector. `instrument` — не свободная строка, а запись реестра с `venue`, `market_type`, `contract_code`, `price_tick`, `quantity_step`, `contract_size`, `settlement_currency`. Поля quote: `bid`, `ask`, `last`, `mark`, `index`, `funding_rate`, `next_funding_at`, `source_at`, `received_at`, `component_times`, `quality`, `source`, `sequence`. Поля candle: `start_at`, `end_at`, `open`, `high`, `low`, `close`, `volume`, `closed`, `source`, `quality`. Числа передавать Decimal-строками; время ISO-8601 UTC. Проверять `low <= min(open,close) <= max(open,close) <= high`, строго возрастающее время, отсутствие дубликатов/пропусков, один venue/market_type. Spot-ряд не допускается рядом с perpetual-исполнением. Если источник истории недоступен — ошибка с `reason_code` и последним достоверным временем, не demo-candles.

Realtime: server push через SSE или WebSocket с `snapshot_id/sequence`; при разрыве — backoff polling и resync по последнему sequence. UI отображает новое полученное значение не позднее 2 с при доступном потоке. Сервер определяет freshness отдельно для ticker, mark, funding и candle на основании уже принятого market contract; пороги и их rationale записать в конфигурации. Историческая закрытая свеча не «перерисовывается» при новом тике без отдельного correction event.

Создать `/api/v3/forecasts?instrument=...&period=...&horizon=...` и `POST /api/v3/forecasts` (асинхронная команда с `request_id`). Все ответы привязать к `instrument_key`, `base_snapshot_id`, `base_time`, `base_price`, `period`, `horizon`, `model_id`, `role`, `prompt_version`, `generated_at`, `expires_at`, `execution_state`, `error_code`, `coverage`. Для каждого будущего интервала хранить `target_start_at`, `target_end_at`, `predicted_close`, допустимые `predicted_open/high/low`, `band_low/band_high`, `band_method`, `confidence_kind`, `rationale`, `origin=model|derived`. `predicted_high/low` означают ожидаемые внутрисвечные экстремумы; статистический интервал неопределённости `band_low/high` — **другая величина**. Не переносить автоматически legacy `predicted_low/high` в wick без определения их семантики и проверки на факте.

Правило отображения: свечу рисовать только при валидном OHLC и явно записанном `origin`. `base_time` — конец последней закрытой фактической свечи, `base_price` — её close; это другая величина, чем текущий bid/ask/mark. `open` первой прогнозной свечи = `base_price`; далее = предыдущий прогнозный close. `high >= max(open,close)`, `low <= min(open,close)`, все значения положительные и конечные. Если доступны только точечная цена и диапазон, рисовать линию и полосу, а не свечу с выдуманным фитилём. Не заполненные роли/шаги помечать `partial`; интервалы на основе одной модели не выдавать за ансамбль. Сохранять прогноз неизменным, когда приходит факт; строить отдельный overlay «факт после прогноза» и считать MAE, direction accuracy, interval coverage и skill относительно простой baseline на сопоставимых окнах. Для показателя без достаточной выборки выводить `N/A`, а не процент.

Сервер валидирует, что горизонт кратен выбранному периоду и не превышает доступный бюджет шагов модели. Минимальная матрица: 15m с 1m/5m/15m свечами; 1h с 5m/15m/60m; 4h с 15m/60m/4h; 24h с 60m/4h/1d. При несовместимом выборе UI предлагает совместимый период и показывает точное число прогнозных свечей до отправки запроса. Каждая карточка горизонта хранит собственный `forecast_id`; переключение горизонта не растягивает старый прогноз по новой шкале.

## 5. План симуляции и предложение AI

Добавить `simulation_session` как верхний объект (`session_id`, owner, mode, instrument, planned_start/end, timezone, status, plan_version); существующие `paper_v2_days`, orders, fills, positions и postings остаются источником финансовой истины. Несколько календарных дней в одной сессии связывать явно, не подменять `session_id` на `day_id`. Режимы: `live_paper` (будущий/текущий рынок), `historical_replay` (закрытый исторический интервал) и `scenario` (отдельный маркированный стресс-тест). Начальная обязательная поддержка: 1–168 часов для live paper, 1–720 часов для replay при полном покрытии данными; меньшее доступное покрытие блокирует старт с `missing_intervals`. Интервал хранить в UTC, показывать в выбранном IANA timezone. Лимит и границы должны валидироваться сервером.

`SimulationPlanV1` содержит: `instrument_key`, `mode`, `start_at/end_at`, `timezone`, `manual_budget`, `auto_budget`, `strategy_id/version`, `style` (из реализованного каталога, не произвольное обещание), `allowed_sides`, `max_leverage`, `max_risk_per_order`, `max_daily_loss`, `max_session_loss`, `max_total_exposure`, `max_open_positions`, `cooldown_minutes`, `trading_hours`, `stop_policy`, `take_profit_policy`, `close_at_end`, `fee_schedule_version`, `slippage_model_version`, `funding_model_version`, `market_data_policy`, `seed` для replay. Все суммы — Decimal USDT. Для каждого поля: допустимый диапазон, единица, серверный валидатор, значение по умолчанию, связь с действующим risk/runner кодом; неподдерживаемое правило запрещено выбирать. Бюджеты manual/auto независимы, перенос средств отсутствует. Стоп/цель и ограничения нельзя ослабить текстом AI.

API: `POST /api/v3/simulation/plans/validate` возвращает нормализованный план, `violations[]`, оценку максимального разрешённого риска, рыночный snapshot и `plan_hash`; `POST /api/v3/simulation/plans` сохраняет draft; `POST /api/v3/simulation/plans/{id}/approve` фиксирует версию; `POST /api/v3/simulation/sessions` принимает только одобренную версию и идемпотентный ключ. Для изменения активной сессии — новая версия плана и явные ограничения; задним числом параметры исполненных сделок не менять. Переходы `draft → approved → starting → running → closing → settlement_pending → closed`, отдельные `rejected/expired`; каждое состояние имеет причину, timestamp и допустимые действия.

Анкета агента: `goal` (обучение/сравнение/контроль риска), `duration`, два бюджета либо общий бюджет с явным предложением разделения, максимальный приемлемый убыток, предпочтение сторон, степень ручного участия и ограничения времени. `POST /api/v3/simulation/plan-proposals` создаёт `proposal_id`, `status=pending`; ответ агента — draft `SimulationPlanV1`, объяснение по каждому выбранному параметру, использованные `snapshot_id/forecast_id`, допущения, недостающие данные, версия модели/политики. После ответа запустить тот же детерминированный `validate`; недопустимые поля не исправлять молча. UI показывает сравнение с ручными/default значениями и кнопку «Принять как черновик»; запуск требует отдельного подтверждения пользователем. При timeout/quota/invalid-response — `failed` с причиной и ручная форма, без платного fallback по умолчанию.

## 6. Исполнение, позиции и ликвидация

Одна цепь истины для обоих счетов: `plan → command → order → fill → position → postings → report`; обязательны `owner_id/session_id/day_id/account_id`, `origin=user|auto`, `actor`, `strategy_version`, `market_snapshot_id`, `idempotency_key`. `accepted`, `processing`, `completed`, `failed`, `unknown` различать в API и UI. Заголовок `Idempotency-Key` должен реально доходить до domain/ledger; повтор с тем же ключом и payload возвращает тот же результат, тот же ключ с другим payload — 409. Потеря HTTP-ответа не вызывает новый POST. Состояние операции восстанавливается после перезапуска runner.

Исполнение market: long открывается по ask и закрывается по bid, short наоборот, затем применяется явно версионированное проскальзывание; риск/ликвидация оценивается по mark. Учитывать contract size, lot/tick rounding, entry/exit fee, funding cashflow, частичный fill/close, stop/target и forced close при конце сессии. Для replay при неоднозначном порядке событий внутри одной OHLC-свечи использовать консервативное adverse-first правило и метку `execution_quality=estimated`; не выдавать такую цену за точный тик. Demo и synthetic staging не должны попадать в отчёт как `live HTX`.

Реализовать фактическую ветку ликвидации: maintenance margin и стоимость закрытия входят в порог, mark служит триггером, закрытие получает исполнимую bid/ask цену с моделью проскальзывания, статус `liquidated` и причина записываются атомарно с fill и postings. Если цена/данные отсутствуют, записать `liquidation_pending`/`settlement_pending`, блокировать новые входы и продолжать защитную обработку; не считать позицию закрытой по одному UI-событию. Один триггер не создаёт второй fill/списание после retry или recovery. Событие и маркер на графике показывают источник, mark, порог, цену исполнения, расходы и остаток equity.

Позиционная API-выдача с фильтрами `open|pending|closed|liquidated|all`, account и session; серверная пагинация и проверка owner. Карточка содержит ID, статус, сторону, qty, вход/текущий mark/выход, stop/target/liquidation, margin, unrealized/realized net, комиссию, funding, причину выхода и ссылки на orders/fills/postings. На графике и в таблице одни и те же IDs и суммы. Пустая история, ошибка источника и нулевой результат — разные состояния.

## 7. Отчёт и оценка эффективности

На `closed` сформировать неизменяемый `SessionReportV1` и `AccountReportV1` **из событий и ledger, ограниченных `session_id`/днём**. Не брать баланс «сейчас» для старого отчёта. Включить закрытые и ликвидированные позиции, а при незавершённом settlement не выпускать финальный PASS-отчёт. Report содержит `as_of`, market/source quality, strategy/policy/model versions, hashes плана и набора replay-данных, reconciliation status и подробную причину несовпадения. Экспорт JSON/CSV/HTML должен совпадать с экраном по ID, суммам и статусам; CSV сериализовать стандартным writer с экранированием и идентификатором счёта в каждой строке.

Для каждого счёта отдельно показывать начальный/конечный equity, реализованный gross, entry/exit fees, funding, проскальзывание, net P&L, ROI=`net/initial_budget`, число входов/закрытий/ликвидаций, win rate, expectancy, profit factor, максимальную просадку по временной серии equity, время в рынке и причины отказанных входов. Значения, требующие достаточного числа сделок, возвращать `null` с `insufficient_sample`. Сводка manual vs auto — сравнение двух независимых счетов, **без объединения балансов**. Если бюджеты различны, сравнивать также нормированные ROI и риск, а не только USDT.

Показать benchmark `no_trade` и `buy_and_hold` на том же инструменте/окне с той же доступной историей и расходами. Replay evaluation: минимум три непересекающихся окна, один и тот же замороженный план и фиксированные seed/data version, таблица результатов каждого окна, медиана, диапазон и худшая просадка; при недостатке окон честный `N/A`. Прошлые окна не разрешено использовать для выбора параметров и одновременно выдавать за независимую проверку; выделить in-sample и holdout. Текст «сколько могла бы принести торговля» обязан сопровождаться режимом, датами, источником, расходами и неопределённостью, без обещания будущей доходности.

## 8. Файлы и структура работ

Развивать существующие шаблоны/JS инкрементально: сохранить `webui/static/app.js`, chart containers price/volume/RSI/MACD, маршруты `/`, `/alerts`, `/predictions`, `/journal`, палитру dark/orange/green/red/blue и текущую авторизацию. Не переписывать целиком `styles.css`. Новые модули и миграции создавать с такой ответственностью:

```text
webui/market_workspace.py           # versioned market/forecast/session read BFF
webui/forecast_candles.py           # validation, OHLC/band semantics, no invented wicks
webui/simulation_api.py             # plan/proposal/session commands and owner scope
webui/templates/workspace.html      # primary market chart + forecast + session context
webui/static/ui/market-workspace.js # state/stream, chart, overlays, responsive controls
webui/static/ui/workspace-actions.js# existing drawer, typed actions/idempotency
paper_trading/session_policy.py      # typed plan validation and immutable versions
paper_trading/liquidation.py         # maintenance/trigger/close state machine
paper_trading/session_report.py      # ledger-derived immutable reports/metrics
paper_trading/repository.py          # additive session/report/equity queries
paper_trading/runner.py              # accepted plan, forced close, liquidation/recovery
collector/htx/history_loader.py     # read/reuse perpetual history; edits only if required
tests/test_forecast_candles.py
tests/test_simulation_plan.py
tests/test_session_report.py
tests/paper_trading/test_liquidation_integration.py
tests/paper_trading/test_session_replay.py
tests/ui/test_market_workspace.py
docs/MARKET-SIMULATION-ARCHITECTURE.md
docs/MARKET-SIMULATION-OPERATIONS.md
docs/MARKET-SIMULATION-ACCEPTANCE.md
```

Миграции только additive: таблицы `simulation_sessions`, `simulation_plan_versions`, `simulation_equity_snapshots`, `simulation_reports`, `forecast_interval_predictions` с PK/FK, `owner_id`, `session_id`, UTC timestamps, schema/version/hash и индексами `(owner_id, status, created_at)` и `(session_id, event_time)`. Не дублировать orders/fills/postings из `paper_v2_*`. Перед DDL описать план rollback и backfill для уже существующих прогнозов; старые точки с неясным low/high сохранять как legacy line+band, не конвертировать молча в OHLC.

Изменение `collector/`, `chatbot/`, `.env`, production Compose, удаление файлов или перезапуск живых сервисов проходит отдельно через проектный `AGENTS.md`: до патча показать PLAN → DIFF → APPLY → TEST → REPORT, цель, active path, список файлов, риск регрессии и команды проверки. Для QA использовать disposable Compose/БД без production volumes; не выполнять `docker compose down -v` на рабочем проекте и не печатать секреты.

## 9. Порядок реализации и выходные критерии

1. **P0 — инвентаризация/RFC.** Записать текущие активные источники spot/perpetual, ID `forecast/session/day/account/order/fill/position`, статусы, финансовые формулы и карту всех старых сценариев. Зафиксировать SHA, dirty hashes, снимки API, текущие ограничения. Выход: согласованная schema + sequence для market→forecast→plan→execution→report и миграция; кодовые изменения после этой фазы.
2. **P1 — один рынок и график.** Сделать canonical perpetual quote + historical/live OHLCV, freshness и resync. Проверить 1m/5m/15m/60m/4h/1d, отсутствие spot/perp mix, desktop/mobile график с фитилями и объёмом. Выход: MC-01…MC-04 PASS.
3. **P2 — прогноз.** Versioned request, 4 горизонта, числовой/текстовый ответ, honest candles/line+band, forecast-vs-fact и калибровка. Выход: MC-05…MC-08 PASS; старые prediction URLs работают.
4. **P3 — конструктор сессии.** Manual plan, два бюджета, подробные limits, validation, approval, immutable plan hash, режим replay. Выход: MC-09…MC-11 PASS.
5. **P4 — AI-предложение.** Анкета → proposal → deterministic validation → редактирование → отдельное подтверждение; fail-safe при отсутствии LLM. Выход: MC-12…MC-13 PASS.
6. **P5 — исполнение и ликвидация.** Связать session с существующим domain, orders/fills/ledger, принудительное закрытие, liquidation, recovery, chart markers. Выход: MC-14…MC-17 PASS на изолированной PG и в браузере.
7. **P6 — результативность и выпуск.** Неизменяемый отчёт, сравнение счетов, holdout replay, exports, desktop/mobile/Telegram проверка. Выход: MC-18…MC-22 PASS, старая матрица F01–F16 не ухудшилась. Не выставлять общий PASS при required FAIL/BLOCKED.

После каждого этапа: lint/type-check/целевые тесты; список точных команд, SHA, результаты, артефакты и ограничение уровня доказательства. Не перескакивать от моков к статусу production-ready.

## 10. Приёмка MC-01…MC-22

| ID | Проверяемое требование и доказательство |
|---|---|
| MC-01 | Perpetual instrument identity совпадает в quote/candles/forecast/order/report; spot feed не подставляется. Контракт и integration test. |
| MC-02 | Исторические и текущие свечи имеют валидные OHLCV, фитили и возрастающие UTC времена; gap/duplicate/revision обнаруживаются. Unit + browser. |
| MC-03 | Bid/ask/mark/index/funding и age каждого источника видны; stale/Redis loss блокирует новый вход без фиктивных цен. Integration + browser. |
| MC-04 | На 1440×900 и 390×844 график, оси, сетка, legend, crosshair, volume и текущая цена читаемы без горизонтального overflow. Browser screenshots + assertions. |
| MC-05 | 15m/1h/4h/24h прогнозы имеют числа, текст, request/model/version/time/coverage и отдельные pending/partial/failed/stale. API + browser. |
| MC-06 | Forecast candle OHLC математически валиден; legacy point-only рисуется линией/полосой, без нулевых или выдуманных фитилей. Unit + screenshot. |
| MC-07 | Будущее отделено от факта; неизменный forecast_id сравнивается с последующими реальными закрытыми свечами. Integration + browser. |
| MC-08 | Accuracy/coverage рассчитаны на holdout с baseline; недостаточная выборка даёт N/A. Детерминированный replay. |
| MC-09 | Пользователь задаёт все поля `SimulationPlanV1`, два бюджета и время; значения сохраняются точно и восстанавливаются после refresh. API/DB/browser. |
| MC-10 | Неисполняемая стратегия, неверные лимиты, неполная история и stale quote блокируют approval/start с точной причиной. Negative tests. |
| MC-11 | Approve фиксирует plan_hash; active план/старые сделки нельзя незаметно изменить; несколько дней связаны одним session_id. PG integration. |
| MC-12 | Анкета даёт AI proposal с причинами/источниками, но только черновик; неверное предложение отклонено детерминированным валидатором. Stub LLM + browser. |
| MC-13 | Timeout/quota/invalid AI response оставляет ручной старт доступным, не вызывает платный fallback или auto-start. Negative tests. |
| MC-14 | Manual/auto счета, балансы, ограничения, origin/actor и ledger независимы; команда ручного счёта не меняет auto. PG integration. |
| MC-15 | Реальный paper lifecycle в QA: accepted→order→fill→position→partial/full close→ledger→net; duplicate/lost response не удваивает эффект. PG + browser. |
| MC-16 | Mark-triggered liquidation создаёт ровно один close fill, postings, status, reason и chart marker; stale market даёт pending/blocked. Golden + crash/retry PG tests. |
| MC-17 | UI фильтрует open/pending/closed/liquidated, при переходе статуса сохраняет те же ID и цену/время/источник. Browser + API equality. |
| MC-18 | Отчёт готов только после settlement/reconciliation и включает ликвидации, расходы, funding, оба счёта, точные исходные данные. PG golden tests. |
| MC-19 | Старый отчёт неизменен после новой сессии/проводки; JSON/CSV/HTML и экран совпадают по байтово нормализованным значениям. DB + export round-trip. |
| MC-20 | ROI/drawdown/win rate/expectancy/PF рассчитаны из ledger/equity-series; division-by-zero и insufficient sample дают null/N/A. Unit + golden fixtures. |
| MC-21 | Replay нескольких окон воспроизводим по seed/data hash, отделяет train от holdout и сравнивает no-trade/buy-hold с теми же затратами. Integration. |
| MC-22 | Авторизация/CSRF/owner scope, mobile/WebView, старые URL и chart containers сохранены; живой HTX read-only и реальный Telegram отмечены отдельным evidence level. Security + browser + live checks. |

Минимальные команды при разработке (из корня `D:\WORED`, после установки зависимостей существующего проекта):

```powershell
python -m pytest tests/test_forecast_candles.py tests/test_simulation_plan.py tests/test_session_report.py -q
python -m pytest tests/ui/test_market_workspace.py -q
python -m pytest tests/paper_trading/test_liquidation_integration.py tests/paper_trading/test_session_replay.py -q
python -m pytest tests/ui -q
docker compose -f docker-compose.qa.yml config --quiet
docker compose -f docker-compose.staging.yml config --quiet
python scripts/run_ui_acceptance.py --host 127.0.0.1 --port 18080 --browser all
```

Тесты с PostgreSQL запускать **внутри изолированного QA Compose** с его DSN; host-side ошибка `postgres-qa` — BLOCKED environment, не PASS. Для каждого этапа Qoder обязан приложить команды и фактический stdout/JUnit, URL/среду, UTC время, SHA, снимки desktop/mobile, доказательство quote→forecast→plan→order→fill→ledger→report и остаточные риски. Скриншот synthetic staging, health 200 и число fixture PASS не подтверждают реальную доходность или работу на HTX.

## 11. Ожидаемый ответ Qoder по каждой фазе

В порядке: (1) цель и допущения; (2) архитектурное решение и почему; (3) дерево изменённых файлов; (4) полный diff/содержимое новых критичных файлов; (5) точные команды миграции/запуска и rollback; (6) выполненные тесты с результатами; (7) MC/F-ID матрица `PASS/PARTIAL/FAIL/BLOCKED/NOT RUN` и evidence level; (8) документы; (9) риски и следующая фаза. Не писать «готово» до выполнения всех обязательных критериев. Если production/Telegram/Docker недоступны, назвать точную границу проверки и продолжить доступную независимую работу.

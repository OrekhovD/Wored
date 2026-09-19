# ТЗ для Hermes: WORED — торговый день с ручным и автоматическим счетами

Версия 1.0 · 10 сентября 2026 · исполнитель: Hermes.

## 1. Поручение, результат и границы

Реализовать законченный ежедневный процесс: **утренний запуск → ручная и автоматическая симуляционная торговля весь день → вечерний сравнительный отчёт → проверяемое обучение на ошибках → следующий день**.

Подтверждённый выбор владельца: **два отдельных симуляционных счёта, ручной и автоматический**. У каждого собственные баланс, маржа, заявки, позиции, лимиты и результат. Условия исполнения одинаковые. Общими являются рыночный поток, торговый день и сравнительная отчётность.

Это замена пользовательского процесса и необходимое развитие backend. Косметическая перекраска страниц или добавление меню не выполняют задание. Конечный результат — работающий WORED, понятный пользователю без чтения технической документации.

Точка входа — этот README целиком. `START-HERMES.md` содержит текст передачи задания. `acceptance.json` содержит обязательные критерии. `accounting-fixtures.json` содержит синтетические числовые эталоны. `STATUS.template.json` — начальное состояние, не доказательство выполнения. `baseline.json` — срез подготовки, не предписание checkout старого commit. `MANIFEST.json` и `validate_bundle.py` проверяют пакет, не приложение.

Справочный продуктовый RFC: `D:\WORED\docs\TRADING-DAY-PIPELINE-RFC.md`. ТЗ самодостаточно; RFC объясняет предшествующие решения. Старый пакет `HERMES-WORED-UIUX` не является активным поручением этого этапа. Не применять его patches, snapshots и layouts поверх проекта.

В scope: существующие webui, торговые сервисы chatbot, execution/scheduler collector, additive storage migrations, необходимые тесты и docs. Вне scope: реальные биржевые ордера, новые платные AI-провайдеры, смена технологического стека, Foresight/hypercube, удаление старой истории, безусловное изменение модели/ключей, обучение весов LLM.

## 2. Полномочия, состояние проекта и безопасность работы

Этот пакет подготовлен как задание. Подготовка пакета не означает, что Hermes уже запущен или production deployment разрешён. Когда владелец передаёт стартовое поручение, разрешены перечисленные изменения кода и документации в отдельном checkout. Реальные ограничения инструментов сохраняются.

1. Прочитать корневой `AGENTS.md`. Сохранить PLAN → DIFF → APPLY → TEST → REPORT по пакетам. Не запрашивать повторное согласование каждого обратимого изменения, уже охваченного поручением.
2. Работать в отдельном worktree/checkout. Не выполнять `git reset`, не удалять файлы пользователя, не перезаписывать незакоммиченный труд других исполнителей. Рабочие документы и нужные незакоммиченные изменения переносить выборочно после инвентаризации; секреты не копировать автоматически.
3. Внедрение, изменение рабочих `.env`, `.env.postgres`, `.env.wored`, перезапуски живых сервисов, применение production migrations и `git push` требуют отдельного прямого разрешения владельца. К этому запросу подготовить конкретный diff, прошедший QA, migration rehearsal и rollback plan.
4. Не использовать `docker compose down -v`, `docker volume rm`, рекурсивное удаление проекта. Не показывать секреты, DSN с рабочими паролями, Telegram initData, cookies. Для конфигурации использовать `docker compose config --quiet`, а не печать раскрытой конфигурации.
5. Root Compose использует bind mounts. Поэтому правка `D:\WORED\chatbot`, `collector`, `webui` может затронуть рабочий runtime без rebuild; изоляция разработки обязательна.
6. На срезе подготовки Compose содержит также `chatbot_wored`; утверждение «ровно пять сервисов» не использовать. Проверить фактические сервисы и mounts заново. При изменении общего chatbot-кода учитывать оба bot consumers.
7. Не менять historical calculation versions. Новое исполнение имеет отдельную версию. Ни одна старая позиция не может одновременно сопровождаться двумя движками.

На срезе 2026-09-10 HEAD = `b3c0fb1ee7cbb512b14c5c8973e08ebb2b8cb1c2`, есть изменение `tests/ui/fixture_app.py` и untracked материалы. Это сведения для сравнения; Hermes фиксирует собственный новый baseline и проверяет расхождения.

## 3. Продуктовые правила первой версии

- Два постоянных paper account на владельца. Начальный капитал одинаковый и вводится при первом запуске. Далее equity каждого счёта сохраняется; утром фиксируется его отдельный starting equity.
- Между счетами нет переводов. Пополнение/сброс разрешаются только вне активного дня, записываются событием и начинают новый сравнительный период. UI первой версии может не предоставлять reset, но не должен терять историю.
- Первый инструмент — BTCUSDT USDT-margined perpetual, isolated. Добавление инструментов через проверенную спецификацию, не подмена perpetual spot ticker. Unknown contract metadata блокирует точный профиль симуляции.
- Пользователь один раз задаёт окончание дня, timezone, капитал и лимиты каждого счёта. Default timezone Asia/Bangkok. Первое окончание по умолчанию 21:00 local, пользователь видит/подтверждает его. После 21:00 показывать настройку следующего дня, не запускать молча 24-часовую сессию.
- Риск: допустимый убыток за день в USDT, риск одной заявки в USDT, max total exposure и предел leverage. Значения сохраняются в policy snapshot. Не подставлять большие плечи из legacy defaults.
- День запускается одной командой для двух счетов. Отсутствие сигнала — рабочее ожидание. Неисправный AI-план блокирует только auto entry, если ручной execution исправен.
- В конце дня закрываются оба счёта: cancel unfilled risk-increasing orders, settle remaining fills, close open positions, reconcile. Overnight carry не входит в первую версию. Счёт с незавершённым закрытием не может начать новый день.
- Auto работает без подтверждения каждого входа. Человек может приостановить входы или закрыть auto positions; такое вмешательство сохраняется отдельно от origin=auto.
- Любая ручная заявка создаётся только на manual account. Пользователь не выбирает случайно auto account в ticket.
- Экран всегда явно помечен «Симуляция». Реальных ордерных API в engine нет.

## 4. UX и управление: обязательное поведение

### 4.1 Навигация

Основные разделы: **Сегодня · Итоги · Обучение**. Исследование/Прогнозы/Журнал доступны контекстно и в «Ещё». Система/Модели — вторичная техническая зона. Сохранить маршруты `/`, `/alerts`, `/predictions`, `/journal`, существующие данные и chart containers price/volume/RSI/MACD. `/command-deck` становится основным «Сегодня», `/daily-session` показывает тот же день и команды без отдельного состояния; допустим redirect на canonical page с сохранением полезных ссылок.

Не менять стек FastAPI/Jinja/vanilla JS. Сохранить dark command surface, orange accent, green ok, red risk, blue chart line. Нет hero-блоков и декоративных псевдографиков. Не заменять styles.css целиком и не удалять app.js. У старых и новых обработчиков не должно быть двойных подписок/POST.

### 4.2 До старта

В первой видимой области: два баланса, выбранное окончание, лимиты, статус readiness и одна кнопка «Начать день». Настройки раскрываются вторичным действием; существующая большая Session Setup не остаётся ведущим блоком после запуска. Первичная настройка и повторный ежедневный старт — разные состояния.

Обычный повторный старт при готовой конфигурации — одна кнопка, без переходов между страницами. После POST сразу видно starting и ход server command. Ошибка показывает конкретную причину, последствия для manual/auto и следующий шаг.

### 4.3 Рабочий экран

Верх: день, таймер, freshness. Ниже две компактные карточки manual/auto, каждая с equity, available margin, realized net, unrealized estimate, costs, оставшимся loss budget и open positions count. Не смешивать equity и «результат при закрытии сейчас».

Центр: график выбранного инструмента, позиции/заявки и события. Desktop — ticket справа. Mobile — позиции раньше расширенной аналитики, ticket bottom sheet. Переключатель счёта меняет содержимое рабочей области, но обе сводки остаются доступны. Открытие страницы не создаёт прогнозы, сделки или model probes.

График: достоверные координаты, price/time axes, timezone подпись, grid, legend, hover/crosshair, separate history/forecast, уровни входа/стопа/цели. Manual/auto markers различаются формой и текстом. Canvas имеет табличную альтернативу. Отсутствующие/устаревшие данные не дорисовываются как свежие.

### 4.4 Ticket ручного счёта

Поля: инструмент, Long/Short, Market/Limit, цена для Limit, риск/размер, стоп, необязательная цель. В первой версии стоп обязателен для нового входа; шаг/сторона стопа валидируются сервером. Закрытие существующей позиции не требует добавления стопа или заметки. Размер из риска учитывает стоп и оценку расходов; gap risk отображается как ограничение оценки. Leverage/расширенные параметры раскрываются отдельно.

Preview показывает account label, quantity, notional, reserved margin, fee entry, estimated exit fee, funding status, break-even, estimated net at TP/SL, liquidation quality и timestamp. Этикетка submit содержит сторону и «ручной счёт». UI блокирует submit при pending/stale/invalid; backend повторяет проверки при исполнении.

Состояния: draft → previewing → ready → submitting → accepted/unknown/rejected; затем working/partially_filled/filled/cancelled. «Заявка принята» не означает «позиция открыта». При unknown уточнять command_id. Закрытие ticket не отменяет уже принятый приказ. Reason tag необязателен, заметку можно добавить после сделки.

Позиция: update SL/TP, частичное закрытие, закрыть полностью. Изменение защиты не может ослабить лимиты молча. При увеличении риска сервер проверяет новый риск и записывает revision; уменьшение объёма и экстренный выход не блокируются дневным loss limit.

### 4.5 Auto controls

| Кнопка | Действие |
|---|---|
| Приостановить новые входы | Cancel ожидающих входов/увеличений; сопровождение, protective exits, funding остаются активными |
| Продолжить автомат | Freshness/risk/plan validation; пропущенные сигналы задним числом не исполняются |
| Изменить лимиты | Числовой diff и момент действия; снижение не вызывает скрытую ликвидацию/закрытие |
| Закрыть позиции автомата | Pause auto, cancel pending entries, close auto only; подтверждение account/count/cost estimate |
| Завершить день | Объяснение последствий для обоих счетов, затем общий closeout |

Статус auto отвечает: что делает, почему, что дальше. «Ожидает сигнал», «Сопровождает 2 позиции», «Остановлен лимитом» — разные состояния. Не показывать READY/ARMED без понятной причины и действия. При людском вмешательстве origin сделки сохраняется auto, отдельное событие имеет actor=user.

### 4.6 Уведомления и Telegram

Внутренняя лента: fills, closes, ограничения, interventions, report. Отдельный постоянный баннер для неразрешённой ошибки; toast не является единственным носителем критического состояния. Повторные одинаковые poll-события не создают уведомления.

Telegram отправляет выбранному пользователем получателю важные изменения и один вечерний итог; детали сделок настраиваются. Проверять server-validated initData, разрешённых пользователей, обе фактически настроенные bot identity. В tests Telegram transport mocked; внешняя отправка только после настройки и разрешения владельца. Учесть safe area, keyboard, BackButton, MainButton, reopen. Отказ Telegram не блокирует торговый ledger/отчёт.

## 5. Общая архитектура и контракт исполнения

Один прикладной engine обслуживает manual и auto. Последовательность: principal → typed command → account/day ownership → idempotency → market/policy validation → risk reservation → order → fill → position/ledger → durable event → presentation/report. AI может предложить приказ, но не изменяет cash/filled state напрямую.

Существующие файлы интеграции: `webui/app.py`, `chatbot/services/session_manager.py`, `chatbot/services/execution_engine.py`, `chatbot/services/sim_math.py`, `chatbot/services/stats_audit.py`, `collector/scheduler/pipeline_jobs.py`, `chatbot/ai/strategy_learner.py`. Legacy math сохраняется для historical version; новые правила живут под новым version ID.

Предлагаемое дерево, окончательные границы зафиксировать на TD-01:

```text
chatbot/services/paper/
  __init__.py
  contracts.py       typed commands/results and error taxonomy
  accounts.py        owner, balance, risk reservation
  orders.py          state machine and idempotency
  execution.py       matching, partial fills, stops, liquidation
  ledger.py          Decimal cashflows and reconciliation
  day_lifecycle.py   start, pause, recovery, closeout
  reports.py         immutable numeric reports
  learning.py        candidates, evaluation gate, activation
webui/
  paper_api.py
  paper_presenters.py
  templates/trading_day.html
  templates/trading_day_report.html
  templates/trading_learning.html
  static/ui/trading-day.js
  static/ui/trading-day-ticket.js
  static/ui/trading-day-report.js
  static/ui/trading-learning.js
tests/paper/
tests/paper_browser/
scripts/migrate_paper.py
scripts/run_paper_acceptance.py
docker/paper-qa.Dockerfile
docker-compose.paper-qa.yml
docs/trading-day/
  architecture.md
  api.openapi.json
  schema.sql
  data-policy.md
  testing.md
  operations.md
  user-guide.md
  learning-policy.md
docs/TRADING-DAY-STATUS.json
docs/TRADING-DAY-ACCEPTANCE.md
docs/TRADING-DAY-IMPLEMENTATION.md
```

Это файлы, которые требуется реализовать, а не уже существующие команды/модули. В существующих templates/navigation/styles/app.js изменения инкрементальные.

Новые общие модули используют package imports из `services.paper`, доступные chatbot локально и webui/collector через `/chatbot`. Текущие chatbot/collector Dockerfile используют Python 3.9, webui/QA — 3.11. TD-01 обязан выбрать совместимый синтаксис/зависимости или подготовить явный upgrade plan; нельзя проверить только QA 3.11 и объявить runtime проверенным. Проверить также второй chatbot consumer. Не добавлять sys.path внутри HTTP request.

## 6. Модель данных и атомарность

На TD-01 создать исполняемую схему `docs/trading-day/schema.sql` и migration plan. Минимальные сущности:

| Таблица | Минимум полей/ограничений |
|---|---|
| paper_accounts | UUID, owner_id, kind manual/auto, currency, status; unique(owner_id, kind) |
| trading_days | UUID, owner_id, local_date, timezone, start_at/end_at UTC, state, version; unique active day per owner |
| trading_day_accounts | day_id, account_id, opening_equity, risk_snapshot, execution_policy_version; unique pair |
| paper_orders | account/day, command_id, side, type, qty, filled_qty, reduce_only, status, version, expiry |
| paper_fills | order, market_event_id, snapshot_id, quantity, price, fee, liquidity_role; unique execution event |
| paper_positions | account/day/instrument, side, open_qty, entry_basis, margin, protection, origin, version |
| paper_ledger | account/day, position/fill/funding event reference, signed cash amount, currency, effective_at, recorded_at; immutable/deduped |
| paper_equity_samples | account/day, sequence, timestamp, equity, mark_snapshot, quality |
| paper_decisions | actor, plan/rule version, as-of context, action/skip reason, referenced command |
| paper_commands | owner, key, payload_hash, expected_version, status, result; unique(owner, key) |
| paper_outbox | event_id, destination, payload reference, delivery status/attempt; unique event+destination |
| paper_day_reports | day, report_version, cutoff sequence, ledger digest, numeric results, reconciliation state |
| paper_learning_candidates | evidence IDs, parameter diff, evaluation manifest/results, decision, future activation day |

NUMERIC/Decimal для денег; precision/rounding/instrument units явно в policy. JSON API передаёт денежные значения строками, unknown=null. Баланс выводится из ledger или проверяемой transactional projection. Маржа — резерв, не расход и не прибыль при освобождении.

FK/ownership проверяются сервером; клиент не выбирает произвольного owner. UUID сам по себе не является авторизацией. Все user write endpoints требуют auth и CSRF. Тесты должны включать чужой account, position, command и report.

Порядок row locks: day → account → order/position. Запись fill, cashflows, state transition, outbox — одна транзакция. Unique keys и worker lease предотвращают двойной fill/funding/closeout. При повторном idempotency key с другим payload возвращать 409. При конкуренции close/SL/liquidation суммарно нельзя исполнить больше оставшегося объёма.

SSE поддерживает cursor/Last-Event-ID и догрузку событий. Fallback poll не работает одновременно с здоровым SSE; скрытая вкладка приостанавливает только UI polling, не server execution. Redis не является единственным хранилищем команд, балансов или состояния дня.

## 7. Достоверность симуляции и числовые правила

На TD-01 создать registry инструментов с version, source URL, verified_at, quantity units, tick/lot/min notional, fee profile, funding schedule source, mark/index feed, maintenance tiers. Обновление registry не переписывает policy уже открытого дня. Проверить актуальные официальные HTX docs/API; старые фиксированные fee rates из кода не считать индивидуальным тарифом пользователя.

Два явно различимых профиля: `verified` и `simplified`. Simplified разрешён только как явно выбранный режим с описанием missing inputs, одинаковый для обоих счетов. Он не закрывает критерии verified execution и не позволяет объявить полную биржевую реалистичность. Неизвестный funding не равен 0. Пропуск исторического funding делает финальный отчёт preliminary до сверки.

Обязательная механика:

- Market: bid/ask, сохранённая задержка, depth/volume model и slippage. При stale snapshot новых fills нет.
- Limit: working/partial/cancel/expire. Crossing limit — taker; пассивный ордер — maker только по модели исполнения. OHLC touch не гарантирует fill. Time-in-force GTC для входов с отменой на finish; market fill bounded latency/expiry фиксируется policy.
- Нет двойного использования свечи до момента размещения. При одновременном SL/TP внутри OHLC выбрать задокументированное conservative ordering и quality flag. По gap стоп исполняется по доступной цене, не обещанной цене стопа.
- Fees начисляются на каждый fill, отдельно entry/exit. Funding event применяется к объёму, открытому на settlement instant, один раз; знаки paid/received различаются.
- Liquidation использует mark/maintenance tiers/fees; частичная ликвидация и остаток учитываются. Упрощённая legacy формула не называется точной формулой HTX.
- Дневной loss limit считается от opening equity с unrealized и расходами по определённой risk policy; достижение отменяет новые входы и переводит счёт в risk_blocked. Общая exposure включает working entry reservations. Protective reduction всегда доступна при валидном исполнении.
- Для first release позиция имеет собственный ID/lots; противоположные manual сделки не сворачиваются молча. Режим hedge/netting фиксируется до реализации; default — раздельные isolated позиции, close только reduce-only к конкретному position_id.

Для линейного контракта количество сначала переводится в base quantity по instrument specification:

`gross_realized = side_sign × base_quantity × (exit_fill − entry_fill)`

`net_trade = gross_realized − entry_fees − exit_fees + signed_funding − explicit_other_charges`

`cash = opening_cash + sum(ledger_cashflows)`

`equity = cash + gross_unrealized_at_mark`

Fees/funding уже в cash, повторно из unrealized не вычитаются. Slippage уже в fill prices; показывать impact относительно reference, но повторно из net не вычитать. Реализовать FIFO allocation entry fee/funding при partial close с сохранением остатка и отсутствием потерь округления. В агрегате сходится полная комиссия всех fills.

Файл `accounting-fixtures.json` задаёт точные эталоны Decimal: long, short, partial close, flat-price loss after fees и slippage. Это synthetic mechanics, не live fee settings. Встроить эталоны в независимые tests engine; не импортировать функции engine для получения expected values.

## 8. День, расписание и восстановление

Day: draft → starting → active → closing → reconciled. Auto independently: observing/waiting_signal/trading/paused/risk_blocked/data_blocked/stopped. Report independently: pending/preliminary/final/failed. Review independently: pending/running/deferred/ready.

Start одной транзакцией обеспечивает один day и две account bindings. Автоплан создаётся асинхронно, не удерживает DB lock во время LLM запроса. Сигнал, план и приказ — разные сущности; каждое auto decision связывается с версией и input snapshot. Decision TTL исключает исполнение старого прогноза.

Finish сначала запрещает новые risk-increasing commands на обоих счетах. Cancel/filled race сверяется по сохранённому event sequence; принятые до cutoff fills обрабатываются, затем закрывается фактический остаток. Funding settlement с совпадающим временем упорядочивается policy, одинаково для обоих счетов. Только при нулевых open orders/positions и успешной денежной сверке допускаются reconciled/final.

Если нет котировок, статус closing с причиной и числом остатков. Не подставлять последнее известное значение как фактическое закрытие. Retry finish не дублирует деньги, отчёт или notifications. Поздняя корректировка создаёт immutable новую версию отчёта.

Scheduler хранит due tasks в Postgres; повторное поднятие worker восстанавливает их. Stop PC останавливает локальное исполнение. Market gap сохраняется и виден пользователю. Не открывать авто-сделки задним числом. Восстановление protective exit по доступному архиву — отдельная deterministic reconstruction с quality flag, не live fill. До сверки оставшихся позиций нельзя начать следующий день.

## 9. Вечерний отчёт

Отчёт строится deterministic из ledger/fills/equity samples, доступен без AI. Содержит две колонки manual/auto и difference:

- opening/closing equity, cashflows, realized net, daily return;
- entry/exit fees, funding paid/received, execution impact без повторного списания;
- closed trades N, wins/losses/breakeven, liquidation count как subset;
- средний net результат, net profit factor, max drawdown по временной equity curve;
- risk at entry, result in initial risk units, risk violations;
- exposure time как union intervals, не сумма пересекающихся интервалов;
- interventions, rejected/expired orders, data gaps, execution profile/strategy versions;
- вклад отдельных сделок, ссылки на причину решения и события.

Daily return = net equity change / opening equity при отсутствии внешних денежных потоков. При нулевом знаменателе null с причиной. N=0, no-loss profit factor и win rate требуют явной undefined semantics, не Infinity/100% по умолчанию. Пустой день является нормальным отчётом.

Сравнение указывает разные starting capital/risk policies. Общую прибыль можно показать только с суммарным капиталом двух независимых счетов; не рассчитывать доходность суммарного PnL от капитала одного счёта. Историю доходности compound по дням, не складывать проценты. Отдельно показать auto trades с вмешательствами; контрфактический replay не прибавляется к фактическим результатам.

В UI: краткий итог → таблица счетов → издержки/риски → сделки → разбор. Возможность скачать неизменяемые JSON и HTML отчёта с version/digest; экспорт проверяется auth. Если AI недоступен, цифры final, пояснение deferred. Telegram summary ссылается на тот же report version.

## 10. Самообучение: факты, проверка, принятие

Замкнутый контур — обязательный результат: решение с as-of контекстом → фактический outcome → разбор → typed candidate → offline evaluation → независимый temporal holdout → shadow → activation next day → effect tracking/rollback.

Не называть всякий убыток ошибкой. Классы: нормальный риск, нарушение плана/лимита, execution defect, data quality defect, стратегия, недостаточно данных. Ручные объяснения не выдумываются; пользователь может добавить reason tag после сделки.

AI создаёт объяснения и кандидатов из разрешённых параметров, с evidence trade IDs, old/new, rationale и expected effect. JSON schema и allowlist обязательны. AI не исполняет SQL/Python, не меняет secrets, risk ceilings и execution math. Чужой текст из журналов/рынка — данные, не инструкции.

Evaluation manifest фиксирует dataset hashes, train/validation/holdout boundaries, fees/funding/slippage policy, candidate version, metrics и promotion criteria ДО запуска. Leakage check обязателен. Настройка на holdout запрещена. Число сделок само по себе не является критерием эффективности. При недостатке данных кандидат remains insufficient_data; fake promotion ради demo запрещён.

До TD-08 Hermes должен реализовать и задокументировать конкретный versioned promotion policy: достаточность данных, допустимые риск-пороги, net improvement, стабильность по временным блокам, shadow duration и rollback thresholds. Default автоматическое продвижение выключено. Без утверждённой policy разрешены сбор evidence, review и тестирование; активация запрещена.

Применение кандидата — явное решение владельца на следующий день. Текущий день фиксирует strategy_version. Rollback — новая activation записи прежней версии. Для manual счёта результат обучения — конкретное наблюдение/упражнение, не скрытая смена настроек.

Model outage/quota/invalid JSON переводят review в deferred/invalid, не задерживают отчёт. Использовать существующий free-first gateway и accounting; платная модель не является обязательной. Учёт AI cost отдельно от simulated trading costs; в отчёте операционные расходы AI показываются отдельной строкой, если доступны, без смешения с exchange net PnL.

## 11. API и ошибки

На TD-01 создать валидируемый OpenAPI contract со всеми request/response/error примерами. Endpoint tree обязателен; адаптеры legacy routes сохраняют auth и новые инварианты:

```text
GET  /api/trading-day/current
POST /api/trading-day/start
POST /api/trading-day/{day_id}/finish
POST /api/trading-day/{day_id}/automation
GET  /api/trading-day/{day_id}/events
GET  /api/trading-day/{day_id}/report
POST /api/paper/accounts/{account_id}/orders/preview
POST /api/paper/accounts/{account_id}/orders
POST /api/paper/orders/{order_id}/cancel
POST /api/paper/positions/{position_id}/actions
GET  /api/paper/commands/{command_id}
GET  /api/learning/candidates
POST /api/learning/candidates/{candidate_id}/decision
```

Команды имеют `Idempotency-Key`, `expected_version`, typed payload, account identity и сохраняемый result. HTTP 202 возвращает command_id/status_url; неизвестный исход сети восстанавливается по стабильному client command key. Current response включает server_time, day/accounts, freshness, capabilities, reason_code, next_action, execution_profile, ui_schema_version. Ошибки: 401 auth; 403 чужой ресурс; 409 version/key conflict; 422 invalid/risk/margin/unsupported; 429 quota; 503 data/execution unavailable. UI не делает auto retry POST новым ключом.

## 12. Этапы, обязательные артефакты и gates

| Этап | Реализация и результат | Gate |
|---|---|---|
| TD-00 | Новый baseline, worktree, inventory consumers/data/tests, status файл | Точный HEAD, dirty files, известные gaps; никакой выдуманной runtime проверки |
| TD-01 | Architecture RFC lock, DDL, OpenAPI, policy registry, security/error model, packaging/runtime plan | Контракты валидируются; economics/ownership/state ambiguities закрыты; unknown external data явно заблокированы |
| TD-02 | Изолированный интерактивный проход утром/днём/вечером с двумя счетами | Desktop/mobile browser QA и пользовательский UX gate A05; макет не production |
| TD-03 | Accounts, migrations, ledger, reservations, command/outbox | Real Postgres atomicity/ownership/replay tests, numerical fixtures |
| TD-04 | Common engine manual+auto, fills/protection/funding/liquidation | Одинаковая математика, partial/race/gap tests; verified/simplified boundary |
| TD-05 | Day lifecycle, auto plan/decisions, scheduler/recovery | Start/finish идемпотентны, protection во время pause, offline tests |
| TD-06 | Интегрированный Сегодня, manual ticket, auto controls, Telegram | Browser interactions на реальном API/test DB, сохранённые routes, no duplicate handlers |
| TD-07 | Числовой отчёт, comparative history/export, evening summary | Все деньги сверены, AI outage не блокирует, quality видим |
| TD-08 | Evidence, candidate evaluation, holdout/shadow, next-day activation | End-to-end learning test, leakage failure, insufficient sample, rollback |
| TD-09 | Полная регрессия и приёмка | Все mandatory automated без fail/skip; real Telegram и user scenarios отдельно |
| TD-10 | Rehearsed migration/cutover/rollback, docs, production rollout при разрешении | Авторизованный deployment, fresh smoke, дневной soak и финальный отчёт |

Gate A05 — осознанное принятие удобства пользователем после отвергнутого UI. Представить конкретный работающий prototype с задачами, не спрашивать одобрение абстрактного плана. Пока ответ ожидается, продолжать независимые contracts/engine/tests, но не фиксировать спорную раскладку как принятую и не выпускать UI. Это единственный обязательный пользовательский UX gate до финальной приёмки; не превращать каждый CSS diff в запрос разрешения.

Тесты добавлять в соответствующем этапе. Не оставлять весь QA на TD-09. После каждого пакета: целевые tests, lint/types, evidence и status. Нельзя менять expected outcomes под дефект реализации. Existing failed tests допускается классифицировать как baseline, но связанные с данным scope regressions должны быть исправлены до acceptance.

## 13. Среда QA и воспроизводимые команды

Команды из PowerShell. Первую выполнить в источнике только для чтения:

```powershell
Set-Location D:\WORED
git rev-parse HEAD
git status --short
python TASOCHKI/HERMES-WORED-TRADING-DAY/validate_bundle.py
docker compose config --quiet
```

Ожидание: hash/status, `PASS: bundle ...`, код 0 config без раскрытия значений. Отсутствие Docker не мешает читать ТЗ, но runtime check отмечается not_run.

Создание рабочего дерева после инвентаризации; путь и ветка должны быть свободны:

```powershell
Set-Location D:\WORED
git worktree add -b codex/trading-day-hermes-20260910 D:\WORED_TRADING_DAY_20260910 HEAD
```

Если путь/ветка заняты — проверить, принадлежат ли они этому заданию; продолжить существующее дерево или выбрать новый явно записанный путь. Не удалять занятое дерево. Запись вне разрешённых директорий требует штатного разрешения среды; не обходить его. Пакет ТЗ может быть untracked и не попадёт в worktree: читать по абсолютному источнику либо скопировать только этот пакет в worktree; также перенести RFC как reference. Проверить SHA-256. Никогда не копировать весь dirty checkout или `.env*` без разбора.

Существующий baseline QA, уже имеющий isolated Postgres tmpfs/internal network:

```powershell
Set-Location D:\WORED_TRADING_DAY_20260910
docker compose -p wored-paper-baseline-qa -f docker-compose.qa.yml up --build --abort-on-container-exit --exit-code-from checks
```

Ожидание: все stabilization/существующие tests прошли без skips; результаты относятся только к baseline scope. Отдельно запускаются существующие presenters/UI suite. Этот Compose не является готовым paper acceptance. Production DSN не использовать; тестовые migrations не должны fallback на рабочую `.env.postgres`.

На TD-03 создать `docker-compose.paper-qa.yml` и `docker/paper-qa.Dockerfile`. Обязательный контракт: отдельный project name, `postgres-qa` с database `wored_qa`, tmpfs, без host DB ports и production env/mounts; `checks` с новым engine/API suite и browser runtime. Fixtures local, Telegram/LLM mocked, external network blocked во время тестов. Dependencies и Chromium/WebKit устанавливаются на build, фиксируются lock/hashes и browser versions. Chart assets для deterministic UI tests доступны локально, без зависимости от CDN.

Команда НОВОГО runner, которую Hermes обязан реализовать и проверить; до TD-03 она не существует:

```powershell
Set-Location D:\WORED_TRADING_DAY_20260910
docker compose -p wored-paper-qa -f docker-compose.paper-qa.yml up --build --abort-on-container-exit --exit-code-from checks
```

`checks` запускает `python scripts/run_paper_acceptance.py --suite all --browser all --artifacts artifacts/trading-day`. Runner завершает nonzero при missing dependency/browser, отсутствии тестов, skipped mandatory, failed reconciliation или browser console/bootstrap error. `--browser all` обязан реально создать Chromium и WebKit и записать separate results. Mock-only contract tests не маркировать browser/E2E.

Runner обязан поддерживать `--suite unit|db|api|browser|recovery|learning|all`, включать lint/type checks новых модулей при all и запускать существующую релевантную регрессию. Нельзя использовать зелёный старый runner как замену.

Артефакты: acceptance-results.json (ID → test IDs/evidence), junit.xml, test log, браузерные screenshots/traces, viewport/browser metadata, sanitized migration log, runtime versions, git HEAD, dataset/policy hashes. Фактические команды/exit codes обязательны. Skipped/pending не входят в passed. Synthetic case и live soak отчёт разделены.

## 14. Миграция, запуск, rollback и документация

Новые migrations additive, с version/checksum, advisory lock, повторным применением без изменений и rollback транзакции при ошибке. `scripts/migrate_paper.py` принимает явный config источник: test mode только WORED_TEST_DATABASE_URL с database=wored_qa; production без явного режима запрещён. Не наследовать fallback существующего migration script к рабочим env.

Backfill сначала dry-run inventory. Нельзя угадывать account/day старых сделок. Старые sim_positions/executed_trades сохраняются как legacy history; по умолчанию новые accounts стартуют явно выбранным капиталом, не суммой несверенных legacy записей. Наличие старых открытых позиций требует согласованного drain/ownership plan до cutover.

Cutover: verified backup+restore rehearsal, blocked new legacy entries, drain/ownership check, additive migration, deploy exact image/commit set, new-engine route switch, smoke and reconciliation. Не отключать risk-reducing legacy commands до завершения их позиций. Для rollout подготовить точные имена сервисов/образов и команды после TD-00 inventory; не выполнять широкий `up -d --build` для всей системы без необходимости.

Rollback: остановить новые входы нового engine, продолжать protective management, отменить pending entries, закрыть/сверить позиции. UI rollback не выключает worker управления позициями. Только после drain допустим возврат write ownership; миграции/ledger сохраняются. Старый image может не понимать новые records — простого revert недостаточно.

Документы в `docs/trading-day/` должны содержать purpose/prerequisites/точные команды/expected output/validation/common failure/fix/rollback. Runtime env registry: PAPER_TRADING_ENABLED (default false), PAPER_LEARNING_AUTO_PROMOTE (default false), WORED_TEST_DATABASE_URL (test-only secret), остальные новые keys до использования документируются. Денежные лимиты, end time, timezone, fee profile — versioned settings в БД. Feature flag переключается при cutover; изменение рабочей env требует отдельного разрешения.

`PAPER_TRADING_ENABLED=false` запрещает новые дни/входы, но не останавливает защиту существующих paper позиций. `PAPER_LEARNING_AUTO_PROMOTE=false` не отключает review/evaluation, только продвижение без решения владельца. В первой версии автопродвижение не включать.

Production daily soak: один явно разрешённый день с двумя paper accounts, реальные market events, ручное действие владельца, автоматические decision events, вечерний final report и persisted review/candidate status. Если auto сигналов не было, день valid, а способность auto fills уже доказана replay; нельзя выдумывать live сделку. Mock Telegram tests не заменяют реальные mobile acceptance. Runtime status заявлять только после свежей проверки.

## 15. Прогресс и определение готовности

В рабочем checkout создать `docs/TRADING-DAY-STATUS.json` из template. При продолжении прочитать status, последний commit и failed/pending evidence; не начинать заново и не перезаписывать ранее пройденные факты.

После каждого этапа записать: status, commit, changed files, commands+exit codes, test counts including skipped, criterion evidence, unresolved gaps, next action. Evidence path должен существовать; status passed без артефакта недопустим.

Конечный отчёт `docs/TRADING-DAY-IMPLEMENTATION.md`: что пользователь реально может сделать; где открывать; таблица критериев; точные команды проверок; миграция/deploy/rollback evidence; оставшиеся риски. Статусы различать: implementation_ready, qa_passed, awaiting_user_acceptance, awaiting_deploy, deployed_verified. Complete допускается только при всех обязательных критериях, включая пользовательское удобство и live/Telegram evidence, либо после явного изменения scope владельцем с перечислением снятых требований.

Не выдавать за завершение: написанное ТЗ, красивый static screenshot, наличие функций, установленные браузеры, HTTP 200, 100% passed substring assertions, generated learning summary или зелёный счётчик без работающего торгового дня.

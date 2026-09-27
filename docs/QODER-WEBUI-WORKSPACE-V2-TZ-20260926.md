# ТЗ QODER / QWEN3.8flash: WORED Web Workspace V2 без функционального регресса

Дата: 2026-09-26. Тип: спецификация реализации. Этот документ заменяет UX-концепцию из `WEBUI-MANAGEMENT-UX-TZ-QODER-20260926.md`; прежний файл остаётся историей аудита. Код в рамках подготовки этого ТЗ не изменяется.

## 0. Поручение QODER

Построй **новый способ управления** WORED WebUI. Не ограничивайся переименованием навигации, переносом существующих карточек и добавлением пустых страниц. Реализуй рабочую область, где основная единица интерфейса — **объект с состоянием, причиной и допустимым действием**: торговый день, ручной/автоматический счёт, заявка, позиция, прогноз, оповещение, отчёт, кандидат правила. Пользователь должен быстро ответить на четыре вопроса: «Что происходит?», «Что требует моего решения?», «Почему?», «Что будет после действия?».

Исполняй по фазам раздела 9. Перед кодом закончи карту функциональной совместимости и RFC. Не принимай будущую разметку или зелёный fixture test за доказательство работающей функции. Сохраняй все старые URL и доступ к их функциям, пока соответствующий сценарий не доказан в V2. Работай поверх текущего dirty worktree; не откатывай и не перезаписывай чужие изменения.

## 1. Исходное положение и границы достоверности

В checkout уже есть `/trading-day`, `/`, `/command-deck`, `/daily-session`, `/trader`, `/futures-lab`, `/strategy`, `/predictions`, `/alerts`, `/journal`, `/system`, `/model-management` и в незакоммиченной работе `/results`, `/learning`. Последние две страницы сейчас рендерят пустые коллекции из `webui/app.py`, поэтому их существование не означает готовые итоги и обучение. Незакоммиченный `/trader` редиректит на `/trading-day`; перед принятием редиректа нужно доказать перенос Trader chart, forecast-vs-fact, режима агента, фильтров позиций и ленты решений. Для `/daily-session` нужно сохранить setup, revision, signal, diagnostics, positions/orders/events. Не считать эти сущности тождественными `paper_trading` без анализа ID и источников.

Предыдущий аудит проверил код и изолированный browser fixture. Рабочий WebUI на 8080 требовал входа; Docker API был недоступен. Ни состояние production DB, ни реальное исполнение сделок, ни Telegram не доказаны в этом документе. QODER обязан свежо зафиксировать Git SHA, dirty hashes и route/API baseline перед реализацией.

Нормативные ограничения: `AGENTS.md`, `docs/TRADING-DAY-PIPELINE-RFC.md`, `TASOCHKI/HERMES-ACTIVE-PAPER-TRADING-V1/ACCEPTANCE.md`. Сохраняются два независимых симуляционных счёта manual/auto, Docker Compose, PostgreSQL/Redis, текущие сервисы, старая палитра и chart containers price/volume/RSI/MACD. Нельзя удалять `webui/static/app.js`, старые шаблоны и маршруты, заменять `styles.css` целиком, менять `.env`/Compose, production БД, логику chatbot/collector или перезапускать живые сервисы в рамках этого задания без отдельного разрешения.

## 2. Принципиально новая модель взаимодействия

### 2.1 Рабочая область, а не набор панелей

Создать `/workspace` как новую точку входа. Логотип WORED ведёт туда после приёмки V2; старый `/` продолжает открывать существующий рынок. URL выбранного объекта должен восстанавливаться после refresh и работать как deep link. Общая оболочка имеет четыре постоянные зоны:

| Зона | Desktop 1440×900 | Mobile 390×844 | Назначение |
|---|---|---|---|
| Строка состояния | Одна строка сверху | Две компактные строки | День/режим симуляции, market/feed age, оба счёта, pending command и auth. Цвет всегда сопровождается текстом и timestamp. |
| «Маршрут дня» | Узкая левая колонка | Компактный раскрываемый прогресс | Подготовка → Работа → Завершение → Итоги → Обучение. Текущая стадия и причина задержки; переходы ведут к объекту, а не к альтернативной системе управления. |
| Контекст работы | Центральная область | Основной поток | Единственный главный объект по текущему состоянию: старт дня, пара счетов и позиции, settlement, сверенный отчёт или learning review. |
| «Требует решения» | Правая колонка | Сразу после строки состояния | Упорядоченная очередь фактических проблем и действий: что произошло, какой объект затронут, почему, допустимый шаг, время следующей проверки. Если действий нет — спокойный статус, без искусственных задач. |

Контекстная панель объекта открывается по любому счёту, заявке, позиции, прогнозу, оповещению или кандидату. У неё единый порядок полей: название/ID → состояние → источник и время → причина → связанные объекты → история событий → доступные действия. На desktop это side panel, на mobile — полный экран с Back и возвратом фокуса. Данные в панели приходят из того же идентификатора, что и список; никакой второй локальной версии P&L.

### 2.2 Одна очередь решений

Очередь формируется **детерминированно из серверных состояний**, не из свободного текста LLM. Приоритет: (1) незавершённое закрытие/защитное исполнение, (2) потеря market data или риск блокировки, (3) ожидающая пользовательская команда, (4) действие владельца по готовому отчёту/кандидату, (5) обычное наблюдение. Группировать по `day_id + object_type + object_id + reason_code`; повторный polling не создаёт дубликаты. Каждая запись имеет `severity`, `reason_code`, `as_of`, `source`, `object_ref`, `primary_action` либо `next_check_at`; неподтверждённый источник не может породить разрешение на торговую команду.

Примеры точных формулировок: «Автомат ждёт закрытия свечи; входов сегодня 0; следующая проверка 16:05»; «Рыночный mark устарел 18 с; новые входы заблокированы; обновить источник»; «Завершение дня ожидает котировку для позиции #…; итог предварительный». Текст «всё хорошо» запрещён, если проверена только доступность `/healthz`.

### 2.3 Единый исполнитель действий

Нажатие действия открывает один Command Drawer с четырьмя этапами: **контекст → проверка → отправка → доказанный результат**. Он показывает actor, `day_id`, `account_id`, объект, старое/новое состояние, ожидаемые расходы/риск, условия запрета, idempotency key и истечение preview. После POST `202` показывается «команда принята» и `command_id`, затем GET статуса до терминального результата. Потеря ответа вызывает поиск по тому же ключу/ID, а не повторный POST. Финансовые действия требуют server-side owner/account check, CSRF, version precondition и risk validation. Frontend не считает официальный баланс.

Нефинансовые admin actions используют ту же визуальную форму результата, но отдельную permission policy и явный эффект. «Clear acknowledged alerts» показывает число записей и не выполняется из обычного smoke test. Не делать универсальный endpoint, принимающий произвольный `action` без typed allowlist.

### 2.4 Вторичные пространства

Вместо 12 равноправных ссылок: **Рабочая область**, **Исследование**, **История**, **Система**. В «Исследовании» рынок/графики, прогнозы, оповещения и AI-журнал; в «Истории» дни, позиции, затраты, решения и версии; в «Системе» health/readiness, модели, админ-действия и legacy diagnostics. Обучение открывается из результата дня и доступно из «Истории» как собственный объект. Это не дополнительные центры исполнения заявок: в них действия с позицией ведут в Command Drawer канонического дня. Поиск/фильтр по ID, дате, счёту, символу и типу объекта позволяет найти сохранённый факт без знания его старого URL.

## 3. Экраны и сценарии V2

### 3.1 До старта дня

Центр показывает две независимые карточки manual/auto, policy snapshot, время завершения, источник рынка и readiness с конкретными blockers. Основное действие — «Начать день», доступно лишь при подтверждённых owner, data/risk state и совместимом backend. Настройки открываются здесь с preview изменения и видимым временем применения. Не показывать `proto-*`, фиксированное `1000`, `fresh=true` или `engine_status=running` как фактическое состояние при сбое domain source.

### 3.2 Активный день

Обе карточки счёта видны одновременно. Первая строка каждой: equity, net после расходов, доступный риск, позиции и независимый статус входов. Для manual — «Новая заявка»; для auto — состояние/причина/версия плана и допустимые pause/resume/close-auto. Ниже общий поток объектов с фильтром `manual | auto | все` и типом `позиции | заявки | события`; фильтр влияет только на отображение, не на выбранный account в Command Drawer. Рядом — график HTX с ценовой/временной шкалой, легендой, реальными уровнями entry/SL/TP и маркировкой `bid/ask/mark`, а не декоративная миниатюра.

Manual ticket сохраняет market/limit, long/short, risk, leverage, обязательный stop, опциональный TP, preview quantity/notional/margin/entry+exit fees/funding/slippage/break-even/net-at-TP/SL/liquidation quality и возраст котировки. Никакая кнопка открытия не доступна при stale preview, недостоверном mark, непроверенном контракте или несовпадающем account ID. В случае отказа UI выводит причину и путь исправления, сохраняя введённые поля.

Auto показывает «почему нет сделки» с состояниями waiting_regime/waiting_trigger/no_trade/cooldown/risk_blocked/data_stale/quota/plan_expired/engine_error. Pause запрещает новые входы, не стирает защиту открытых позиций; close-auto и finish-day требуют отдельного подтверждения с количеством объектов и расходами. AI plan и consensus отображаются как аналитика с timestamp/coverage, не как доказательство исполнения.

### 3.3 Закрытие и история

После «Завершить» рабочая область переходит в `closing/settlement_pending`, показывает прогресс по обоим счетам и незакрытые заявки/позиции. Только после ledger reconciliation появляется `final` и переход к отчёту. Итоги имеют таблицу **manual | auto | разница**: opening/closing equity, gross, fees, funding, net, return, drawdown, trades, win rate при N>0, exposure, limit violations, interventions. Каждая сумма раскрывается до fills/postings; slippage как оценка относительно reference, без второго вычета из net. Версия отчёта, cutoff, source quality и digest видимы. История дня и экспорт JSON/HTML сохраняют эти поля и не мутируют состояние.

### 3.4 Обучение

Отчёт ведёт к наблюдениям, далее к кандидату с evidence IDs, размером выборки, старым/новым параметром, replay/holdout/shadow результатами, условиями допуска и отката. Статусы `insufficient_data`, `candidate`, `validating`, `ready_for_decision`, `approved_for_next_day`, `applied`, `rejected`, `deferred` имеют разные тексты. `ready_for_validation` не равен «применено». Если backend даёт только `build_learning_review`, интерфейс показывает read-only анализ и явную зависимость от отсутствующего validation/decision API. Запрещено создавать кнопку, которая локально меняет badge без server evidence.

### 3.5 Исследование и инженерные инструменты

Рынок: watchlist, BTC/ETH, периоды 1m/5m/15m/1h/4h/1d, автообновление, price+SMA20/SMA50, volume, RSI, MACD, оси/легенда/действительное время. Прогнозы: создание по символу/timeframe/horizon/depth, queue/pending/partial/failed, сохранённые запросы, роли bull/bear/arbiter, forecast-vs-fact и метрики качества. Оповещения: фильтр, severity, acknowledge/reopen, pagination. Журнал: список, символ/страницы, запись, market context и raw JSON по раскрытию. Система: PG/Redis/collector/forecast readiness, model probe только по явной кнопке, configured vs available vs quota/active route, cache refresh, journal snapshot и clear acknowledged с permission и результатом. Все существующие функции сохраняются даже если впервые доступны через «Инструменты» до нативного переноса.

## 4. Матрица сохранения функционала

Для каждой строки QODER обязан создать baseline screenshot/API sample на изолированной QA-среде, затем проверить V2 и записать `parity: PASS/FAIL/BLOCKED` с evidence. Старый маршрут не редиректить в V2, пока parity по его строке не PASS. Редирект `/trader`, уже появившийся в dirty worktree, помечать **непринятым** до переноса Trader mode/диагностики.

| ID | Старая функция / маршрут | Место V2 | Необходимое доказательство отсутствия регресса |
|---|---|---|---|
| F01 | Auth `/login`, Telegram auth, logout, роли | Общая оболочка | Разрешённый вход/выход, запрет чужого owner, CSRF, direct link после login. |
| F02 | `/trading-day` settings/start/next | Рабочая область: подготовка | Сохранение policy, separate accounts, start/next commands и причины отказа. |
| F03 | `/trading-day` manual preview/open/close | Account card + Command Drawer | Те же order types и поля, lifecycle command→fill→ledger, account attribution. |
| F04 | `/trading-day` auto pause/resume/close/finish | Auto card + queue | Действия и safety semantics, pending/result, никакого двойного эффекта. |
| F05 | `/trading-day` отчёт и JSON/HTML | История: день | Оба счёта, экспорт, версии, расходы и источник, финансовая сверка. |
| F06 | `/command-deck` market/forecast/positions/session/accuracy/health/quick forecast | Исследование + контекст и queue | Все показатели/quick forecast доступны; торги только через канонический flow. Standalone URL остаётся. |
| F07 | `/daily-session` setup, revisions, plans, signal, diagnostics, orders/events | «Инструменты → Сессия» до полного merge | Нет потери ни одного поля/команды; старая session identity не притворяется trading_day_id. |
| F08 | `/trader` chart overlays, forecast-vs-fact, mode switch, auto positions, budget, activity | «Инструменты → Trader» до переноса | TRADE/reduce-only/pause, filters, feed, chart и budget работают и доступны по старому URL. |
| F09 | `/futures-lab` позиции/фильтры/статистика | История: позиции или legacy tool | Все записи доступны с origin/source, без смешения с новым ledger. |
| F10 | `/strategy` metrics/rules/positions/evaluate/history | История/Обучение и инженерные инструменты | Re-evaluate, filters, metrics и история доступны; данные не выдаются за активную политику без version. |
| F11 | `/`, `/dashboard` графики и watchlist | Исследование: рынок | Все chart series, переключатели и refresh; JS без exceptions. |
| F12 | `/predictions/{id}` создание/список/деталь/сравнение | Исследование: прогнозы | Полный form, roles, accuracy, chart, partial/failed states и deep link. |
| F13 | `/alerts`, `/journal/{id}` | Исследование: события/журнал | Фильтры, pagination, ack/reopen, raw evidence. |
| F14 | `/system`, `/model-management`, admin actions | Система | Health/readiness, explicit probe, cache/snapshot/clear с правильными POST URL и правами. |
| F15 | Нынешние `/results`, `/learning` в dirty worktree | История: итог/обучение | Реальные источники, честные empty/error, никакого фиктивного `days=[]` как «полной истории». |
| F16 | Telegram Mini App и внутренний prediction API | Без UI-изменения контракта | Те же owner/day/account/position IDs, callbacks, internal token и safe area отдельно проверены. |

## 5. Данные, команды и границы источников

Создать read-only BFF/presenter `GET /api/workspace/state?day_id=...` (имя уточнить RFC) поверх действующих сервисов. Ответ: `schema_version`, `as_of`, `owner_ref`, `day` с ID/state/version, `accounts[manual,auto]` с origin/ledger_ref, `market` с bid/ask/mark timestamps/quality, `objects` с typed refs, `attention[]`, `capabilities[]`, `sources[]` с `source_name`, `source_at`, `observed_at`, `stale_after`, `status`. Серверный presenter не пишет торговые таблицы и не суммирует несовместимые legacy источники. Отсутствующая зависимость выдаёт `unavailable`/`blocked` и reason_code; HTTP 200 с пустым объектом не превращается в «счёт 0» или «готово».

Каждый объект имеет стабильный `object_ref={kind,id,day_id,account_id,source}` и `links` для related order/fill/position/ledger/report/forecast/event. Для исторического поиска нужен read-only индекс с пагинацией, фильтрами и server-side owner scope; не загружать всю БД в браузер. Идентификатор в URL валидировать и проверять владельца. Публичный status health и execution readiness разделены.

Команды V2 используют typed registry `action_code → server endpoint → allowed states → confirmation level → result lookup`. Существующий `paper_trading` domain service остаётся владельцем финансового эффекта. Никакой клиентский fallback на `/api/positions/open`, если `/api/paper/accounts/{id}/orders` не ответил. Для каждой команды: `Idempotency-Key`, payload hash, expected version, owner/account/day scope, `command_id`, `accepted/pending/completed/rejected/unknown` и GET статуса. Не расширять режим auth bypass для UX-тестов; использовать отдельный QA instance.

## 6. Дизайн и поведение

Использовать текущие UI tokens, тёмную палитру, оранжевый action accent, зелёный ok, красный risk, синюю линию графика. Один уровень главного действия; вторичные команды в инспекторе объекта. Не показывать больше пяти равновесных KPI в верхнем экране: день, две суммы equity, net после затрат, состояние автомата и age рынка. Остальные детали доступны в один выбор объекта. Для каждого значения обязательны единица, период, source и tooltip определения там, где смысл не очевиден. «Реализовано», «В позиции», «Баланс», «Доступно», «Оценка закрытия сейчас» не заменяют друг друга.

Responsive contract: desktop 1440×900, laptop 1280×800, mobile 390×844 и 320×800, landscape 844×390. На mobile обе карточки счетов остаются видимы без горизонтального скролла; Command Drawer учитывает keyboard и safe areas Telegram; не перекрывает Confirm и Stop. Поддержать keyboard navigation, focus return, Escape/Back, видимый focus, `aria-live` для переходов command status, текст вместе с цветом, reduced motion. График имеет настоящие ценовую и временную шкалы, grid, легенду и crosshair; нельзя рисовать фиктивные свечи ради пустого состояния.

Ошибки: loading/empty/error/stale/blocked/partial/pending/final определены отдельно. При потере связи сохранить последний снимок как `устаревший` с timestamp и заблокировать риск-увеличивающие действия. Лента событий виртуализуется/пагинируется и не шумит polling событиями. Данные в DOM экранировать; внешние ссылки валидировать. Никаких секретов, bearer tokens, DSN и приватных owner IDs в браузерном log/evidence.

## 7. Целевая структура файлов

Точные имена новых файлов допускается уточнить в RFC, но не менять ответственность модулей:

```text
webui/
  app.py                         # только регистрация маршрутов и существующие handlers
  workspace_read.py              # read model, owner scope, provenance
  workspace_presenters.py        # display model/attention priority, без финансовой арифметики
  templates/
    base.html                    # инкрементальное расширение общей оболочки
    workspace.html               # новая рабочая область
    workspace_object.html        # адресуемая деталь объекта либо общий partial
    partials/workspace_*.html    # status, account, queue, inspector, command drawer
    index.html, trading_day.html, command_deck.html, daily_session.html,
    trader.html, futures_lab.html, strategy.html, predictions.html,
    alerts.html, journal.html, system.html, models.html,
    results.html, results_detail.html, learning.html, learning_detail.html
  static/
    app.js                       # сохранить
    styles.css                   # только адресные дополнения
    ui/tokens.css                # совместимые токены
    ui/workspace-state.js        # polling/SSE, version and stale handling
    ui/workspace-actions.js      # typed Command Drawer
    ui/workspace-objects.js      # inspector, links, filters
    ui/workspace-chart.js        # переиспользование Lightweight Charts
tests/
  ui/                            # fixture routes и реальные browser assertions
  test_workspace_read.py
  test_workspace_actions.py
docs/
  QODER-WEBUI-WORKSPACE-V2-TZ-20260926.md
  WEBUI-WORKSPACE-V2-RFC.md
  WEBUI-FUNCTION-PARITY.md
  WEBUI-WORKSPACE-V2-ACCEPTANCE.md
```

Новые файлы — blueprint, не утверждение, что они уже существуют. Изменения в `paper_trading/`, migrations или chatbot возможны только как отдельный backend dependency с явным контрактом и собственной QA/acceptance; нельзя подменять их заглушкой в WebUI.

## 8. Обязательная приёмка V2

| ID | Сценарий | PASS только при таком доказательстве |
|---|---|---|
| V2-01 | Ориентация | Из начального экрана пользователь без инструкции находит статус дня, оба счёта, причину ожидания автомата и next action. 5 наблюдаемых сессий с заданиями, не только оценка автора. |
| V2-02 | Поиск объекта | По ID/дате/счёту открываются позиция, её order/fill/ledger, прогноз и отчёт; после refresh тот же объект выбран. |
| V2-03 | Независимость счетов | Manual command меняет только manual; auto не получает ручной origin; в сравнении один account_id на каждой стороне. DB/API/UI сверены. |
| V2-04 | Ручной lifecycle | Preview→command accepted→order→fill→position→partial/full close→ledger→net, с bid/ask, mark, fees/funding и правильным статусом. |
| V2-05 | Нет ложного успеха | 202/pending, stale quote, no signal, quota, settlement_pending, AI failure и lost response не показываются как completed/final. |
| V2-06 | Автомат и вмешательство | Pause/resume/close-auto, reduce-only и plan diagnostics доступны; protection/funding работают при pause; origin и intervention видны. |
| V2-07 | Закрытие/итоги | Оба счёта сверены; final только после settlement; отчёт можно найти, раскрыть до ledger, экспортировать и повторно открыть без изменения данных. |
| V2-08 | Обучение | Негативный и положительный candidate pipeline; правило не применяется до проверки и следующего дня; rollback/evidence доступны. Если backend не готов — BLOCKED, кнопки применения нет. |
| V2-09 | Аналитика | Chart periods/series, прогнозные роли и forecast-vs-fact, alerts, journal, strategy и model probe сохранены по F06–F14. |
| V2-10 | Совместимость URL | Каждый старый URL и deep link работает; redirect только после PASS функциональной строки и с сохранением object context. |
| V2-11 | Мобильный сценарий | 320×800, 390×844, 844×390, 1280×800, 1440×900: нет overflow/перекрытий; обе карточки, очередь, drawer и charts читаемы; клавиатура/Back/focus работают. |
| V2-12 | Безопасность | Auth/CSRF/owner, чужие IDs, command replay/conflict, HTML injection, stale data fail-closed; админ-действия недоступны без роли. |
| V2-13 | QA fidelity | Fixture обслуживает все V2 routes и точные critical schemas; browser fixture, disposable DB integration, live read-only и real Telegram имеют отдельные evidence levels. |
| V2-14 | Производительность | При 500 исторических объектах/100 событиях day workspace интерактивен без загрузки всей истории; время и объём замерены, пороги зафиксированы в RFC до реализации. |

Функциональный выпуск требует `F01–F16 = PASS` и `V2-01…V2-14 = PASS` на необходимых уровнях. `AC-01…AC-28` остаётся отдельной общей приёмкой paper trading; зелёный UI не закрывает её автоматически. BLOCKED по отсутствующему backend или внешнему доступу честно блокирует claim «без регресса» для соответствующей функции. Нельзя «лечить» отсутствие auto trade принудительным сигналом.

## 9. Исполнение небольшими блоками для QWEN3.8flash

1. **B0 — Freeze & Inventory (без изменений кода).** Снять `git status --short`, SHA и dirty hashes; сохранить список маршрутов, контролов, API, data owners, current screenshot/console в QA. Создать `docs/WEBUI-FUNCTION-PARITY.md` с F01–F16, старым и новым тестом. Найти все параллельные write paths и отдельно указать current dirty diff. Выход: reviewable inventory.
2. **B1 — RFC и прототип.** Создать `docs/WEBUI-WORKSPACE-V2-RFC.md`: object schema, state machine, attention priority, command registry, permission, responsive layouts, wireflow для 14 сценариев и data lineage. Сделать интерактивный прототип на детерминированных fixture данных с настоящими chart axes/grid/legend, без декоративных котировок. Провести 5 коротких task-based проверок удобства; записать находки и исправить layout до B2.
3. **B2 — Read-only workspace.** Добавить `/workspace`, presenter/BFF, статус, два счёта, очередь, инспектор, поиск/фильтры и честные empty/error/stale. Финансовые кнопки ещё не отправляют команды. Проверить API contract, owner scope и responsive browser. Старые страницы остаются доступными.
4. **B3 — Commands.** Подключать по одному: start/settings → manual preview/open/close → auto controls → finish. Для каждого тестировать accepted/pending/completed/unknown, idempotency и ledger. Убрать дублирующее действие со старого экрана только после PASS той же функции в V2; оставить deep link/историю.
5. **B4 — История и исследование.** Подключить реальные reports/ledger, learning evidence/decision только при готовом backend, forecasts/market/alerts/journal/strategy/models/admin. Для каждого F-ID — side-by-side parity. Удалять старый шаблон запрещено; редирект допустим лишь по строке с PASS.
6. **B5 — Release QA.** Запустить deterministic tests, disposable DB, desktop/mobile browser, regression suite старых URL/charts, security checks и согласованный реальный Telegram; собрать evidence. После этого выбрать новый URL по умолчанию. Rollback — вернуть старую точку входа и feature flag оболочки, не откатывая финансовые записи/миграции.

После каждого блока: `PLAN → DIFF → APPLY → TEST → REPORT`. Пример неразрушающих команд из `D:\WORED`:

```powershell
git status --short
python -m pytest tests/test_ui_presenters.py tests/ui webui/tests -q
python -m pytest tests/test_paper_api_domain.py tests/paper_trading -q
python -m compileall -q webui tests/ui
docker compose config --quiet
```

Ожидаемый результат — exit 0 и отчёт о конкретных passed/failed/skipped; нынешний baseline может быть красным, поэтому не переписывать тест ради зелёного числа. Browser QA проводить на изолированном fixture и disposable `wored-qa` без production volumes; скриншоты и console/network logs для всех указанных viewport. `docker compose up/restart/down`, изменение `.env`/Compose и mutating POST на рабочем 8080 не выполнять в ходе подготовки ТЗ и B0/B1. Все финансовые проверки в disposable QA фиксируют source/evidence и сверяют одну цепочку signal→order→fill→ledger→P&L.

## 10. Формат передачи результатов QODER

По каждому блоку дай в этом порядке: цель, допущения/неизвестное, архитектурное решение, дерево затронутых файлов, полный текст новых критических файлов и diff существующих, точные команды, фактический output, таблица F/V2/AC со статусами `PASS/PARTIAL/BLOCKED/NOT RUN`, обновлённые документы, остаточные риски и следующий блок. Не писать `TODO` в критическом пути, не заявлять runtime/Telegram/финансовый PASS по fixture, не подменять дизайн переименованной навигацией или пустыми страницами.

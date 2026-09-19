# ТЗ Hermes: действующая автоматическая paper-торговля WORED

Версия 1.0 · 14 сентября 2026 · рабочий проект `D:\WORED` · основной бот `@RACHELLO_BOT`.

## 1. Задача и результат

Реализовать инструмент, который после запуска торгового дня самостоятельно анализирует живой рынок, создаёт проверяемые торговые намерения, исполняет допустимые сделки в симуляции, управляет открытыми позициями и завершает день с проверяемым финансовым отчётом. Пользователь должен понимать текущее действие, причину ожидания и следующее условие входа из Telegram и WebUI.

Сейчас пользователь видит активную сессию и ноль позиций, но не может определить, работает ли исполнитель. Короткий текст «trend_up / Thesis / Model» не является достаточным торговым планом. Генерация JSON, работа контейнеров и наличие классов агентов не являются реализацией автоматической торговли.

Обязательны два независимых симуляционных счёта одного владельца: **ручной и автоматический**, с отдельными балансами, позициями, лимитами и результатами. Основной пользовательский путь: **Сегодня → Итоги → Обучение**.

ТЗ не обещает прибыль и не требует открывать сделку вопреки сигналам и ограничениям. Требуется доказать, что подходящий сигнал действительно приводит к исполнению, а каждое отсутствие исполнения имеет актуальную конкретную причину.

## 2. Содержимое пакета и порядок чтения

```text
HERMES-ACTIVE-PAPER-TRADING-V1/
  START-HERMES.md          готовое поручение исполнителю
  README.md               нормативное ТЗ и архитектурные решения
  ACCEPTANCE.md           обязательная матрица проверок и доказательств
  TASKS.json              этапы, зависимости, требования и выходные результаты
  BASELINE.json           снимок Git и перечень незакоммиченных файлов
  EVIDENCE.md             границы ранее выполненной проверки
  MANIFEST.json           состав пакета и SHA-256
  validate_bundle.py      автономная проверка целостности пакета
```

При противоречии прежних проектных ТЗ этому пакету, для работ по данной задаче применяется этот пакет; конфликт записывается в журнал решений. Более позднее прямое указание владельца имеет приоритет. Требования безопасности и сохранности данных продолжают действовать.

## 3. Исходное состояние и неизвестные — REQ-01

Снимок основан на просмотре рабочего дерева 14.09.2026, HEAD `4a438be`. Это не утверждение о составе образов, работающих в Docker. Точные доказательства и ограничения — в `EVIDENCE.md`.

| Область | Найдено в исходниках | Что должен сделать Hermes |
|---|---|---|
| Telegram Daily Session | `chatbot/services/session_manager.py`, `execution_engine.py`; таблицы `trading_sessions`, `session_plans`, `planned_entries`, `executed_trades` | Проследить фактический запуск, источник данных, условия отказа и владельца позиции |
| План v2 | `plan_contract.py`, `plan_store.py`, `plan_presenter.py` | Сохранить валидацию, атомарную публикацию, объяснения отклонений; адаптировать к единому исполнителю |
| Диагностика | `execution_status.py`, изменения статуса и cooldown | Проверить загрузку процессами; не считать локальный патч внедрённым |
| WebUI paper | `webui/paper_api.py`, `paper_store.py`, `paper_market.py` | Убрать бизнес-логику из HTTP-обработчиков в общий сервис, сохранить совместимость маршрутов |
| Автоагенты | `webui/paper_agents.py`, `paper_learning.py` | Доказать существование работающего runner; при отсутствии реализовать и зарегистрировать |
| Рыночный feed | `collector/htx/perpetual_market.py`, Redis `market:perpetual:htx:BTC-USDT` | Проверить возраст каждого компонента, контракт, bid/ask, mark и расписание funding |
| Scheduler | `collector/main.py`, `collector/scheduler/pipeline_jobs.py` | Один владелец исполнения, восстановление после рестарта, непрерывная защита позиций |

Изученный Daily Session использует spot-данные; отдельный paper-контур имеет perpetual bid/ask и своё хранилище. Нельзя объявлять их единым продуктом до фактического объединения команд, исполнения и учёта.

**Неизвестно:** содержимое актуального плана и записанные отказы конкретной активной сессии RACHELLO; текущая загрузка последних файлов всеми процессами; фактическая активная модель с учётом env; наличие естественного подходящего сигнала в момент жалобы. Hermes обязан установить эти факты чтением данных и логов. Не подменять неизвестность версией «модель не дала вход».

Рабочее дерево содержит чужие и незакоммиченные изменения, включая зависимости runtime. Перед работой: перечень tracked/untracked, diff, хэши значимых файлов и карта импорта в контейнеры. Не сбрасывать дерево и не восстанавливать его до одного HEAD. `BASELINE.json` — навигация, не резервная копия исходников. В итоговом воспроизводимом release должны присутствовать все необходимые новые модули.

## 4. Границы и разрешения — REQ-02

Включено: PostgreSQL, Redis, существующий Docker Compose, `chatbot`, `chatbot_wored`, `collector`, `webui`; общий paper-engine, торговый день, миграция, диагностика, UI, QA, документация и управляемое внедрение. Главный инструмент первой версии: **HTX BTC-USDT, USDT-margined isolated perpetual**. Spot-аналитика может сохраняться в других разделах, но не подменять данные исполнения этого инструмента.

Не включено: реальные биржевые ордера, новые платные подписки, подключение биржевых торговых ключей, поддержка других бирж, переписывание WebUI с нуля, работы в `hypercube`/`foresight`.

Передача этого ТЗ Hermes для исполнения означает разрешение подготовить и проверить изменения исходников указанного контура, включая `chatbot/` и `collector/`, в рабочем дереве/изолированной копии. Это не команда немедленно менять работающую сессию. Изменение production `.env`, `docker-compose.yml`, применение production-миграций и рестарт действующих сервисов выполняются на финальном шаге в рамках актуального явно подтверждённого владельцем внедрения. Ранее выданное разрешение на конкретный рестарт не расширять автоматически на новую миграцию.

Все подготовительные работы, тесты в disposable QA и документацию закончить до запроса на внедрение. Не спрашивать разрешение на каждый файл. Не удалять данные, файлы или volumes, не выполнять `git push`, `git reset --hard`, `docker compose down -v`. Не обходить отказ автоматического контроля доступа. Не рассылать сообщения пользователям/третьим лицам для проверки: интеграционные тесты используют mock transport, live-проверка — только согласованный тестовый получатель.

## 5. Целевая архитектура — REQ-03

**Один доменный пакет `paper_trading/`, PostgreSQL как источник истины, collector как единственный исполнитель.** Telegram и WebUI являются адаптерами команд и представлений. Redis хранит market snapshots и быстрый heartbeat, но не заменяет финансовый журнал.

Поток: пользователь/стратегия → команда с idempotency key → общие auth/risk/service → PostgreSQL → collector runner → повторная проверка рынка и риска → simulated fill + ledger в транзакции → общий статус → Telegram/WebUI.

Целевая структура новой логики:

```text
paper_trading/
  __init__.py
  contracts.py       версионированные схемы, Decimal, коды причин
  market.py          адаптер perpetual snapshots и закрытых свечей
  risk.py            общие проверки до заявки и перед каждым fill
  execution.py       fill, SL/TP, funding, liquidation, partial close
  ledger.py          проводки, проекции, reconciliation
  repository.py      PostgreSQL, блокировки, команды и recovery
  service.py         команды дня, ручного и автоматического счёта
  strategy.py        детерминированная baseline-стратегия и сигналы
  planner.py         вызовы AI через существующий gateway
  runner.py          цикл, lease, heartbeat, обработка команд
  learning.py        оценка кандидатов и активация версии
  presenters.py      общий статус и объяснения без UI-зависимостей
```

Допустимо перенести существующие подходящие реализации, сохранив тонкие compatibility wrappers. Запрещено дублировать расчёты в новом пакете, `session_manager.py` и `paper_api.py`. Доменный пакет не импортирует FastAPI Request, Telegram update, UI templates или Telegram transport. Продумать включение пакета в build context/mount всех потребителей; локальный import не доказывает импорт в контейнере.

Для QA обновить `docker/qa.Dockerfile`: новый пакет в `/repo/paper_trading`, `PYTHONPATH=/repo:/repo/webui:/repo/chatbot`, Python 3.11 как воспроизводимая контейнерная среда. Для каждого production-сервиса сначала определить фактический import root по Dockerfile/Compose, затем подготовить явное включение пакета и проверить импорт из собранного образа. Не полагаться на Windows cwd или случайный sys.path.

Изменяемые адаптеры: `chatbot/handlers/pipeline.py`, `chatbot/services/session_manager.py`, `collector/main.py`, `collector/scheduler/pipeline_jobs.py`, `webui/paper_api.py`, `webui/paper_store.py`, `webui/app.py`; остальные файлы менять по подтверждённой цепочке вызовов. Риск регрессии: старые позиции, конкурирующие callbacks, расчёт P&L, auth и контейнерные импорты. Обязательные проверки перечислены в `ACCEPTANCE.md`.

## 6. Данные, владельцы и атомарность — REQ-04

До патча зафиксировать mapping существующих таблиц и миграции в `docs/PAPER-TRADING-IMPLEMENTATION.md`. Целевые сущности ниже можно реализовать добавлением нормализованных таблиц, не разрушая старые. JSON state допустим как проекция, не как единственный финансовый источник.

| Сущность | Обязательные поля/ограничения |
|---|---|
| owner / identity mapping | стабильный owner_id; проверенная связь Telegram ID и WebUI identity; username не является ключом авторизации |
| account | account_id, owner_id, kind manual/auto, currency, persistent opening deposit; UNIQUE(owner_id, kind) |
| trading_day | day_id, owner_id, timezone, start/end UTC, state, settings snapshot, strategy_version; максимум один незавершённый день владельца |
| command | command_id, owner/account/day, type, idempotency_key, request_hash, expected_revision, status, result/error, timestamps; UNIQUE(owner_id, idempotency_key) |
| plan / signal | version, immutable snapshot_id, strategy_version, schema_version, TTL, rejected candidates, decision_code, decision_at |
| order | order_id, account/day, origin, actor, signal_id, side, qty, type, SL/TP, reservation, state, filled_qty, execution_engine_version |
| fill | fill_id, order_id, execution_quote_id, price, qty, fee, slippage, source/receive/execute timestamps; повтор команды не создаёт повтор fill |
| position | account/day, instrument, side, qty, avg_entry, isolated_margin, stop/target, owner_engine_version; lineage до fills |
| journal / postings | append-only event_id, account/day, command/fill/funding reference, currency, amount Decimal, accounting bucket, occurred_at; UNIQUE финансового источника и типа проводки |
| decision / heartbeat | run_id, last_started/completed, reason, required/actual metrics, next_check, next_transition, version, error; длительная история решений в PostgreSQL |
| report / learning | immutable report revision, ledger cutoff, reconciliation, review status, candidate/active versions, evaluation evidence |

Все FK учитывают владельца/счёт; одного переданного `position_id` недостаточно для доступа. Повтор idempotency key с другим payload → конфликт, с тем же payload → прежний результат. Денежные величины — Decimal в Python, NUMERIC в PostgreSQL, десятичные строки в JSON. Счета не обнуляются при новом дне, нет автоматических переводов между manual/auto. Первичное одинаковое пополнение двух счетов — явная операция с проводками.

Нормализованные таблицы первой миграции именовать с отдельным prefix `paper_v2_`: `owners`, `identities`, `accounts`, `days`, `commands`, `plans`, `signals`, `orders`, `fills`, `positions`, `events`, `postings`, `decisions`, `leases`, `reports`, `strategy_versions`, `evaluations`, `cutovers` после этого prefix. Например, `paper_v2_accounts`. Старые `paper_trading_runtime` и `paper_trading_ledger` сохраняются как legacy источники/compatibility projection до проверенного cutover. DDL с типами, индексами, FK, unique/check constraints и schema version должен быть готов в T02; никакого CREATE TABLE в обработчике пользовательского запроса.

Исполнение сериализуется PostgreSQL-транзакцией и блокировкой счёта/позиции. Для runner использовать lease с fencing token: устаревший владелец lease не может коммитить после нового владельца. Не держать DB lock во время сетевого вызова AI. Сигнал имеет уникальность `(account_id, strategy_version, instrument, closed_bar_time, direction)`; один сигнал не создаёт два входа после рестарта.

## 7. Рыночные данные и реалистичность — REQ-05

Первая версия исполняет market/stop-market/market-on-trigger. Входная зона является условием выставления market-ордера, а не обещанием limit fill. Passive limit-очередь вне первой версии; UI не выдаёт касание свечой за исполнение лимитного ордера.

Обязательные данные: instrument/venue/contract type, bid/ask и доступный объём, mark/index, contract multiplier, price tick, quantity step, минимальный объём/стоимость, funding rate и фактическое funding event time, component timestamps. Проверять не только общий received_at. Закрытые 1m/15m/1h свечи должны относиться к тому же perpetual-контракту; spot-свечи нельзя незаметно использовать вместо них.

В live_paper отсутствие/устаревание критичного компонента блокирует новый риск с reason_code. Нет перехода к demo/случайной/последней неизвестно когда полученной цене. UTC timestamps; локальное время — отображение. Ошибки clock skew и свечные пропуски видимы.

Правила fill: long открывается по ask, закрывается по bid; short открывается по bid, закрывается по ask. Цена сигнальной закрытой свечи не является ценой последующего fill. Применять модель adverse slippage и latency, указанную в настройках и в trade audit. После latency брать первое допустимое доступное наблюдение, не старую цену. Зафиксировать ограничение исполняемого объёма по доступной ликвидности, остаток market-ордера отменять по IOC; частичный fill и частичный close поддерживаются. Без объёма нельзя заявлять проверку ликвидности.

Для v1 допустим top-of-book snapshot: объём fill ограничен соответствующей стороне quote и `PAPER_MAX_VISIBLE_LIQUIDITY_FRACTION`. Повтор одного snapshot не создаёт новую доступную ликвидность; учитывать уже использованный объём в этом quote sequence. Это приближённая симуляция доступности top-of-book, без обещания биржевого queue position и скрытой глубины. Зафиксировать IOC timeout 5 секунд; частично закрытая позиция сохраняет защиту, closeout повторяет reduce-only попытки по новым quote events до полного закрытия либо явного blocked состояния.

SL/TP проверяются по документированному trigger source. Для v1: mark crossing запускает market close, исполнение — следующий доступный bid/ask с издержками. Gap через stop не исполняется по давно недоступному stop price. При потере котировок состояние protection_degraded, событие и ожидание реальной цены; нельзя выдумывать успешное закрытие. Пока цена отсутствует, не утверждать, что рыночный риск устранён.

Mark используется для unrealized P&L, margin/risk и модельной liquidation. Для точного соответствия HTX Hermes должен проверить актуальные публичные правила контракта, fee tier, maintenance margin tiers и settlement и сохранить ссылки/даты/fixtures. Пока это не сделано, маркировать модель `estimated_isolated`, указывать допущения. Не называть приблизительную liquidation price точной биржевой. Реальные комиссии аккаунта без приватных данных неизвестны: использовать явно показанную configurable simulation fee schedule.

Funding применять только на фактическом событии к соответствующей открытой позиции, с правильным знаком; positive rate: long платит, short получает. Snapshot с ожидаемой ставкой не доказывает финальную начисленную ставку. Нет итоговой ставки/события — pending_settlement и предварительный отчёт до сверки; не подставлять ноль. Повтор ingest/restart не начисляет funding дважды.

## 8. Учёт и риск — REQ-06

Хранить отдельные комиссии открытия/закрытия, signed funding, gross realized P&L и net realized P&L. Если slippage уже включён в fill price, его attribution показывается информационно, без второго списания.

Для qty, приведённого к базовому активу через multiplier: `gross = direction × qty × (exit − entry)`; `net = gross − entry_fee − exit_fee + funding_cashflow`. При частичном выходе распределять entry fee и стоимость позиции пропорционально закрытому объёму, сохраняя остаток. Формулу округления и residual adjustments зафиксировать. `equity = cash + unrealized`; margin — резерв, не повторный расход. Сверять available/reserved/isolated margin и обязательства по журналу.

Риск проверяется до принятия ордера и повторно перед каждым fill: авторизация/счёт/день, freshness, TTL, direction/zone/trigger, qty precision/minimum, доступная маржа, max leverage, риск до SL с издержками, общий открытый риск, дневной drawdown от opening_equity с unrealized, net reward/risk, SL до liquidation с буфером, spread/liquidity, отсутствие конфликтующего ордера, пауза/cooldown/day_end. Выход reduce-only не блокируется лимитом входа или истечением AI-плана.

Стартовые настройки **для нового владельца без сохранённых настроек**: капитал 1000 USDT на каждый счёт, max risk/order 10 USDT, max daily loss/account 50 USDT, max leverage 10, max open risk/account 20 USDT, максимум одна позиция на инструмент/счёт. Это настройки симуляции, не рекомендация торговли. Показывать до первого запуска. Существующие настройки не перезаписывать. Risk profile не имеет права увеличивать подтверждённые пользователем лимиты.

В v1 aggressive меняет допустимую частоту/условия стратегии в рамках отдельной версии, но не требует обязательного entry и не отменяет no_trade. При конфликте ограничений возвращать перечень несоблюдённых правил. Скрыто уменьшать SL, поднимать leverage или снимать фильтры ради количества сделок запрещено.

## 9. Исполняемая стратегия и AI-план — REQ-07

Нужен реальный генератор сигналов. Режимы `baseline_auto` и `ai_plan` отображаются явно. При запуске нового дня базовый режим — baseline_auto; переключение режима сохраняется в settings и audit. Недоступность AI не вызывает скрытого переключения между режимами.

### 9.1 Детерминированная baseline v1

Это начальная воспроизводимая гипотеза для симуляции, а не доказанная прибыльная стратегия. Hermes реализует её полностью и сохраняет параметры под strategy_version. Нельзя менять параметры по итогам неудачного теста без новой версии и явного объяснения.

1. Прогрев: минимум 100 закрытых 1h, 100 закрытых 15m и 100 закрытых 1m perpetual bars. EMA: alpha `2/(N+1)`, seed SMA первых N; ATR14 Wilder: seed SMA первых 14 true ranges, далее `(prev*13+TR)/14`. Только уже закрытые бары; одинаковый код в live/replay.
2. Long regime: на последней закрытой 1h EMA20 > EMA50 и close > EMA20; на 15m EMA20 > EMA50. Short — зеркально. Иначе waiting_regime. Пропуск нужной свечи/недостаточный warmup → waiting_data.
3. Long trigger на новой закрытой 1m: предыдущая свеча low ≤ её EMA20, последняя close > её EMA20 и close > previous high. Short: previous high ≥ previous EMA20, last close < last EMA20 и close < previous low. Сигнал действителен 60 секунд после закрытия бара, один раз на account/version/bar/side.
4. Вход market после триггера, если текущая исполняемая цена отклонилась от signal close не более чем на 0.25 ATR14(1m). ATR=0 → отказ. SL long = минимум low последних 5 закрытых 1m минус 0.25 ATR; short = максимум high плюс 0.25 ATR. TP = 2R от текущего entry reference. Повторно рассчитывать экономику на цене исполнения; если net RR < 1.2, отменять, а не сдвигать защиту после fill.
5. Размер из денежного риска до SL с обеими комиссиями и slippage, округление вниз по quantity step, ограничение маржей/ликвидностью. После SL cooldown 10 минут для baseline v1; после TP — до следующего уникального закрытого 1m сигнала. Сигналы, истёкшие в cooldown, не воспроизводить задним числом.
6. Новые входы запрещены в последние 5 минут дня; открытые позиции защищаются до закрытия. Один подтверждённый сигнал может дать отказ по риску — причина записывается. Дубликаты оценок свечи не порождают дополнительные заявки.

### 9.2 AI-план

Планирование не работает в каждом ценовом тике: старт дня, смена режима рынка, истечение TTL, существенный invalidation event; минимальный интервал 5 минут, не больше одного одновременного запроса на account. Версия плана живёт максимум 60 минут и не позже конца дня; публикация требует fresh snapshot и compare-and-swap активной версии. Сетевой ответ после паузы/конца дня не возобновляет торговлю.

Структура плана: schema/version/strategy, instrument/snapshot/time/TTL, market regime и численные основания по таймфреймам, основной/альтернативный сценарий, условия invalidation, executable entries или no_trade, direction/zone/trigger, SL/TP, sizing bounds, net economics, риски, rejected candidates, модель/provider/latency/usage. Текст объяснений — русский; численные поля машинные. Entry подтверждается детерминированно; модель не пишет fills/balances и не обходит риск.

Проверить prompt: no_trade разрешён во всех профилях. Удалить противоречивое требование «обязательно минимум одна entry». Отличать no_trade от timeout/quota/invalid_schema/empty_final/truncated_json/stale_response. Невалидный ответ не превращать в фиктивный торговый план. Сохранить исходный sanitized output и ошибки в audit с ограничением размера.

Использовать существующий provider gateway, единую маршрутизацию и token/quota accounting. Default в коде не доказывает активный runtime model. Лимиты запроса/дня, timeout, retries/backoff и fallback фиксировать конфигурацией; не подключать paid модель без действующего policy gate. Зафиксировать, как обновляется snapshot при fallback: если данные устарели, перепланировать на новом snapshot, а не публиковать старые числа как свежие. Истёкший план запрещает вход, но не отменяет SL/TP уже открытой позиции.

Bull/bear/risk роли могут расширять решение через типизированные agent_runs, однако роль без зарегистрированного runner не считается реализованной. В v1 не требуется вызывать шесть моделей на каждый сигнал. Риск-арбитр всегда детерминированный; AI-обсуждение — источник кандидатов/объяснений.

## 10. Runner, статусы и восстановление — REQ-08

Runner collector: обработка команд и защитных условий каждые 2 секунды; baseline signal evaluation только на новой закрытой 1m; AI cadence отдельно. Перегрузка не создаёт параллельных циклов одного account. Долгий AI не задерживает SL/TP, funding и ручное закрытие.

Day lifecycle: idle → starting → running → closing → closed; recovery_required и settlement_pending являются явными состояниями. Automation lifecycle: initializing → armed/waiting_signal → position_open; paused, cooldown, risk_blocked, data_stale, engine_error объясняют причину запрета новых входов. Статус day и статус auto не смешивать. Pause запрещает новые входы и отменяет pending entry orders; защита позиций остаётся. Resume не оживляет просроченный план/сигнал. End day имеет приоритет над resume и planner response.

Heartbeat минимум каждые 5 секунд: instance_id, lease token, started/completed_at, last_success, last_error, execution/strategy version, data freshness. UI: age ≤15 секунд healthy, 15–30 delayed, >30 unconfirmed. Порог явно версионировать; текущий legacy heartbeat с порогом 60 секунд — отдельный старый контракт. Запись heartbeat сама по себе не доказывает успешный цикл: показывать last_success и обработанный sequence. Исключение публикуется как engine_error, не как waiting_signal.

После рестарта загрузить незавершённые команды/ордера/позиции из PostgreSQL, сверить ledger, восстановить только принадлежащие этой версии позиции, отменить истёкшие intents, переоценить market freshness. До успешного recovery входы заблокированы. Crash между расчётом и commit не оставляет одиночное списание/позицию без fill. Cooldown хранится как UTC deadline, не только статус; по окончании проверяется пауза, день и свежесть сигнала.

## 11. Понятный Telegram и WebUI — REQ-09

Оба интерфейса получают один status DTO: owner/day/account kind, mode, engine status+age, feed status+age, strategy/plan version+TTL, open/pending/closed counts, last decision+time, reason_code, actual/required threshold, next check/transition, equity/P&L/costs. Ошибку чтения нельзя показывать как нулевой баланс или ноль позиций.

При нуле позиций показывать конкретно, например: «Авто: ждёт пробоя. Последняя проверка 2 с назад. Long: закрытие 1m должно быть выше 64 320; последнее 64 280. Следующая оценка после закрытия свечи через 18 с». Это образец формы, не рыночные данные. Для stale/cooldown/no_trade/quota/expired_plan/engine_error использовать соответствующую причину и время/действие. Не писать «всё работает», когда heartbeat отсутствует.

Telegram: старт/текущий день, manual/auto карточки, подробный план, почему нет сделок, pending orders, открытые позиции, пауза/продолжить авто, закрытие позиции/обоих счетов, итоги/обучение. Команды принимаются с idempotency key; callback повтор безопасен. Длинный план разбивается на страницы/сообщения с деталями, не обрезается до шести строк. Пользовательские строки экранируются; соблюдается лимит сообщения Telegram.

WebUI: инкрементально развить существующие `trading_day.html`, `daily_session.html`, `static/ui/trading-day.js`, `session.js`, ticket и presenters. Главная карточка «Сегодня» показывает счета рядом и текущее действие авто. Ручной билет: сторона/объём/SL/TP, расчёт риска и издержек, preview expiry, pending command, final fill. Preview не обещает цену; исполнение повторно проверяет условия. Состояние unknown после timeout восстанавливается запросом command status, без повторного ордера.

Сохранить `/`, `/alerts`, `/predictions`, `/journal`, chart containers price/volume/RSI/MACD, app.js и текущую палитру: dark surface, orange accent, green ok, red risk, blue chart. Не заменять styles.css целиком. Desktop и mobile/Telegram Mini App: читаемые денежные строки, touch targets, safe areas, отсутствие горизонтального overflow, переходы назад, ошибки и retry. Кнопка ручного вмешательства в auto сохраняет `origin=auto`, `actor=user`; не переводит результат на manual account.

API сохраняет действующие маршруты:

| Метод | Полный маршрут |
|---|---|
| GET | `/api/trading-day/current` |
| POST | `/api/trading-day/settings` |
| POST | `/api/trading-day/start` |
| POST | `/api/trading-day/{day_id}/automation` |
| POST | `/api/trading-day/{day_id}/finish` |
| POST | `/api/trading-day/next` |
| POST | `/api/paper/accounts/{account_id}/orders/preview` |
| POST | `/api/paper/accounts/{account_id}/orders` |
| POST | `/api/paper/positions/{position_id}/actions` |
| GET | `/api/paper/commands/{command_id}` |

Контракт каждого маршрута, auth, schema_version, HTTP error и переходы зафиксировать в OpenAPI и тестах. API сверяет ожидаемый revision, возвращает command_id для принятой команды; неизвестный исход после timeout проверяется через command endpoint.

## 12. Завершение дня, отчёт и обучение — REQ-10

По умолчанию конец дня 21:00 Asia/Bangkok; время отображается до запуска и сохраняется как UTC. Если указанное время уже прошло, запросить другое время в UI; не создавать молча сутки торговли. За 5 минут запретить новые auto entries. Deadline запускает тот же idempotent closeout, что ручное «Завершить день».

Порядок: остановить новые входы обоих счетов → отменить pending entries → закрыть обе группы позиций по доступным котировкам → провести fees/funding → reconciliation → сохранить неизменяемый финансовый отчёт → запустить AI review. Нет цены/неполное закрытие/неразрешённый funding → явное closing/settlement_pending и предварительные итоги. Не объявлять closed или финальный P&L раньше срока. Нельзя начать следующий день при незавершённом финансовом закрытии прежнего.

Отчёт сравнивает manual/auto: opening/closing equity, realized gross/net, unrealized остаток, fees, signed funding, slippage attribution, max drawdown, trades/wins/losses/breakeven, liquidation count, rejected entries по причинам, активное время/время простоя и интервалы отсутствия данных. Ликвидация — подмножество исходов, не дополнительная сделка в `wins+losses+liquidations`. Без исполнений win rate = «нет сделок», не 0% успешности модели. Данные сводятся к fills и ledger, не извлекаются из AI-текста.

Обучение: candidate → validating → approved/rejected → active следующего дня → rolled_back при регрессии. Минимум 20 сопоставимых эпизодов, chronological replay и отдельный holdout без будущих данных, учёт costs, метрики baseline/candidate. Default evaluation: chronological split 70/30, минимум 6 эпизодов holdout; episode boundaries фиксируются до сравнения, purge gap равен максимальному lookback и времени удержания, активы и интервалы baseline/candidate одинаковые. Gate отдельно на replay и holdout: net P&L candidate минус baseline ≥0.001 начального капитала, drawdown не хуже, нарушений риска и liquidation не больше, reconciliation pass. Если данных после purge недостаточно — insufficient_data. Порог и split сохранять до оценки в evaluation config; менять их по результату кандидата запрещено. При недостатке данных `applied_rules=[]`; недоступный AI → deferred. Финансовый отчёт от AI не зависит. Не менять активную стратегию открытой позиции. Gate проверяет пригодность обновления по выбранной процедуре, а не доказывает будущую прибыльность.

## 13. Переключение старого и нового контура — REQ-11

До production нужен dry-run с числами по каждому источнику: legacy sessions/plans/pending/executed trades, sim_positions и paper state, владельцы, неизвестная attribution, открытые позиции и ошибки. Нельзя объединять похожие записи только по времени/цене и пересчитывать исторические fills по новой модели.

Предпочтительный переход: отключить новые legacy entries для выбранного владельца атомарным cutover marker; старый executor продолжает только защиту явно принадлежащих ему позиций. Новые дни работают через общий сервис после закрытия/сверки старой сессии. Перенос открытой позиции допускается только отдельной проверенной миграцией с начальным ledger checkpoint и сменой owner_engine_version в одной транзакции. Ни один момент не допускает двух исполнителей одной позиции. Нераспознанный владелец/учёт → migration_blocked, без молчаливого назначения auto/manual.

Старые endpoints становятся совместимыми адаптерами общей команды; старые данные остаются доступными в истории с маркировкой модели. Изменения схемы additive с migration version. Rehearsal в QA, counts/checksums/reconciliation до и после. Backup и restore проверяются до production. Rollback выключает новые входы и переключает код только после определения, кто защищает позиции; простой возврат образа при новой схеме/открытых позициях небезопасен. Исторические проводки не удаляются.

## 14. Конфигурация и эксплуатация — REQ-12

Ниже **новые ключи, которые Hermes должен реализовать и документировать**, а не утверждение об их наличии. Production значения не менять при подготовке пакета. Хранение пользовательского риска — БД settings, не дублирующие env. Эти ключи — технические пределы; более строгие пользовательские ограничения сохраняются.

| Ключ | Default | Назначение |
|---|---|---|
| PAPER_ENGINE_ENABLED | false | новый runner выключен до cutover |
| PAPER_ENGINE_INTERVAL_SECONDS | 2 | poll команд/защиты |
| PAPER_HEARTBEAT_INTERVAL_SECONDS | 5 | публикация runtime health |
| PAPER_HEARTBEAT_STALE_SECONDS | 30 | unconfirmed threshold |
| PAPER_MARKET_MAX_AGE_SECONDS | 5 | критичные execution quotes |
| PAPER_ORDER_MAX_SPREAD_BPS | 10 | верхняя граница spread нового входа |
| PAPER_SIM_SLIPPAGE_BPS | 2 | неблагоприятная модельная добавка, не рыночный факт |
| PAPER_SIM_LATENCY_MS | 250 | минимальная задержка fill в модели |
| PAPER_SIM_FEE_RATE | 0.0006 | simulation taker fee каждой стороны |
| PAPER_MAX_VISIBLE_LIQUIDITY_FRACTION | 0.1 | доля наблюдаемой ликвидности для одного fill |
| PAPER_AI_MIN_INTERVAL_SECONDS | 300 | нижний предел интервала планирования |
| PAPER_AI_PLAN_TTL_SECONDS | 3600 | верхний предел TTL, ограниченный концом дня |
| PAPER_AI_MAX_REQUESTS_PER_DAY | 48 | запросы на account/day, включая fallback attempts |
| PAPER_AI_MAX_TOKENS_PER_DAY | 200000 | общий token budget; проверка через gateway |

Проверять значения при старте; невалидная конфигурация → explicit readiness failure для trading subsystem. Зарезервировать worst-case tokens до запроса и сверить фактический usage после; неизвестное usage не считать бесплатным. Дневной бюджет сохраняется в PostgreSQL и не сбрасывается при рестарте. Provider-specific model/timeout/output token settings берутся из существующего registry, без второго списка ключей.

Metrics/logs: run/owner/account/day/command/order/fill/plan/snapshot identifiers, last successful cycle, quote ages, decisions by reason, risk rejections, fills, AI errors/usage, recovery state, reconciliation mismatch. Secrets и provider raw headers не логировать. `/readyz` отдельно отражает gateway, market и paper executor; paused/no_trade не являются аварией инфраструктуры. Нужна read-only диагностика конкретного владельца без раскрытия чужих данных.

## 15. Этапы и обязательная приёмка — REQ-13

Последовательность и зависимости — `TASKS.json`. Сначала текущая причина нулевых позиций и архитектурная карта; затем общие контракты/ledger/market/risk; далее работающий baseline runner с replay полного цикла; затем адаптация Telegram/WebUI, AI и обучение; миграция и live acceptance завершают работу. Не тратить первый этап на декоративный dashboard или очередной prompt-only patch.

Hermes создаёт `tests/paper_trading/`, `tests/paper_trading/fixtures/`, `scripts/run_paper_acceptance.py` и `docs/PAPER-TRADING-IMPLEMENTATION.md`. В acceptance runner: `--mode offline|replay|live-readonly`, `--output`, JSON/JUnit/Markdown evidence; `--output -` печатает единственный полный JSON document в stdout, служебные логи — stderr. Ненулевой exit code при FAIL/BLOCKED, в том числе при отсутствии обязательного fixture. live-readonly ничего не запускает и не закрывает. Fixtures с ожидаемыми сигналами, provenance и SHA-256; generated fixtures помечаются synthetic. Нужны отдельные recorded-perpetual datasets для реалистичного replay.

Новые обязательные тесты не могут быть skipped. Изолированная QA использует `docker-compose.qa.yml`, project `wored-qa`, PostgreSQL `wored_qa`, `WORED_TEST_DATABASE_URL`, без production volumes/env. Existing tests расширять после проверки их актуальности; старые skips не выдавать за DB coverage.

Команды, уже применимые к текущим файлам, из PowerShell:

```powershell
Set-Location D:\WORED
git status --short
python TASOCHKI/HERMES-ACTIVE-PAPER-TRADING-V1/validate_bundle.py
python -m pytest tests/test_execution_status.py tests/test_session_plan_contract.py tests/stabilization/test_execution_contracts.py tests/stabilization/test_snapshot_consumers.py -q -p no:cacheprovider
docker compose -p wored-qa -f docker-compose.qa.yml config --quiet
docker compose -p wored-qa -f docker-compose.qa.yml run --build --rm checks python -m pytest tests/test_session_plan_contract.py tests/test_session_plan_web.py tests/stabilization/test_postgres_sessions.py -q -p no:cacheprovider
```

Команды, которые **обязаны работать после реализации новых файлов**:

```powershell
Set-Location D:\WORED
python -m ruff check paper_trading tests/paper_trading scripts/run_paper_acceptance.py
python -m mypy paper_trading
docker compose -p wored-qa -f docker-compose.qa.yml run --build --rm checks python -m pytest tests/paper_trading -q -p no:cacheprovider
New-Item -ItemType Directory -Force artifacts/paper-acceptance | Out-Null
docker compose -p wored-qa -f docker-compose.qa.yml run --rm checks python scripts/run_paper_acceptance.py --mode replay --output - | Out-File -Encoding utf8 artifacts/paper-acceptance/replay.json
if ($LASTEXITCODE -ne 0) { throw 'Paper replay acceptance failed; inspect replay.json and stderr' }
python scripts/run_paper_acceptance.py --mode live-readonly --output artifacts/paper-acceptance/live
```

QA replay сохраняет evidence на host через stdout capture, поэтому `--rm` не уничтожает отчёт. JSON включает полный список кейсов, результаты, расчёты и hashes; большие sanitized traces/скриншоты требуют отдельного явного export механизма в runbook. Команды выше задают обязательный CLI, не обещают текущую готовность отсутствующих scripts. При запуске live-readonly на host использовать документированную действующую конфигурацию read-only доступа; если сервисные адреса доступны только внутри Compose, Hermes обязан дать проверенную команду запуска в нужном сервисе и export отчёта.

После изменений выполнить также scoped lint/type checks адаптеров, полный затронутый regression suite, JS syntax и browser acceptance. Известные unrelated failures перечислить с baseline comparison, не скрывать игнорированием ошибок. Все runtime/migration/deploy команды с реальными service names, ports и rollback steps привести в итоговом runbook после discovery.

Уровни доказательств не смешивать: unit → PostgreSQL integration → deterministic replay → браузер/Mini App → live feed → естественный auto fill/close. Обязательная матрица, условия PASS/BLOCKED и запрет выдавать инфраструктуру за торговлю — `ACCEPTANCE.md`.

## 16. Итоговая поставка Hermes — REQ-14

Поставить законченный reviewable diff, migrations + dry-run/rollback, воспроизводимый release со всеми необходимыми файлами, обновлённые docs/config registry, точные команды и evidence acceptance. В отчёте дать по каждому этапу PASS/FAIL/BLOCKED, counts passed/failed/skipped, environment/commit/dirty hashes, model audit отдельно от mock, browser evidence отдельно от backend tests.

`docs/PAPER-TRADING-IMPLEMENTATION.md` содержит назначение, архитектуру и data mapping, prerequisites, env/ports/build/mounts, команды QA/replay/live, ожидаемый результат, причины нулевых позиций, recovery/closeout, migrations/backup/restore/rollback и остаточные ограничения. Обновить существующие конфигурационные и эксплуатационные документы, не создавать противоречащую им параллельную инструкцию.

Финальная формулировка «бот автоматически торгует» допустима только с ID естественного сигнала, auto-order, fill, позиции, выхода и согласованного net P&L в live_paper. До этого писать точный уровень: например «replay принят; live feed и runner подтверждены; естественный полный цикл ещё не наблюдался». Не добиваться этой фразы принудительной сделкой. Если инфраструктура/доступ блокирует работу, завершить независимые этапы и предъявить конкретный блокер, не выдумывать runtime результат.

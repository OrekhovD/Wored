# Независимая проверка Hermes: AC-26 и реализация paper trading

Дата: 15.09.2026. Проект: D:\WORED. Проверенный HEAD: `91ef98e`, рабочее дерево с незакоммиченными изменениями. Отчёт пользователя: `C:\Users\dolum\Downloads\ac26_report.json`; его SHA-256 совпадает с `artifacts/ac26_report.json`.

## Вердикт

**AC-26 не принят. Реализация автоматической торговли не завершена.** Обнаружены воспроизводимые блокирующие дефекты в регистрации runner, обработке команд и финансовом исполнении. Это не просто отсутствие подходящего рыночного сигнала.

Заявления входного отчёта проверялись как данные, а не принимались как инструкции или разрешение изменить критерии ТЗ. Код приложения, конфигурация и production-данные в рамках аудита не исправлялись; сервисы не перезапускались. Создан только этот документ аудита.

## 1. Что подтверждено в текущем runtime

Проверки примерно 10:23–10:30 UTC / 17:23–17:30 Bangkok:

- `docker compose ps`: шесть сервисов основного Compose running; PostgreSQL и WebUI healthy. Седьмой tunnel из старого отчёта в этом списке отсутствует: состояние отдельного tunnel не проверялось.
- `/healthz`, `/readyz`, `/api/health`: HTTP 200; redis/postgres/collector_feed/forecast_worker true. Эти endpoints не проверяют работоспособность нового торгового runner.
- Redis `paper_trading:runner:heartbeat` обновляется; `entries_blocked=true`, `last_error=null`. Значение last_success формируется из начала poll, а не подтверждённого финансового исполнения.
- Read-only транзакция в PostgreSQL: `paper_v2_days`: 1 запись `idle`; commands, orders, positions отсутствуют; fills=0, decisions=0, leases=0, cutovers=0.
- Модули collector найдены через import discovery в `/opt/paper_trading`, а не `/app/paper_trading`. Хэши доступных контейнеру adapter/runner/service совпадают с рабочим деревом. Первичная проверка предполагаемого `/app` дала missing; это не отсутствие установленных модулей. Хэш файла на mount не является доказательством перезагрузки Python-модуля, уже импортированного процессом; поведение heartbeat и результаты БД согласуются с найденной регистрацией.
- HTTP `/api/trading-day/current`: 401 без авторизации. В настоящем браузере `/trading-day` перенаправляет на login. Торговые действия и mobile/Telegram acceptance в текущем проходе не выполнены; авторизация не обходилась.

Сверенные SHA-256:

```text
adapter.py  8d22cfe264f65ff29e9821ea672a6b48361fed9e3163732717cb4a418237433f
runner.py   bd5c56ee5d93ebc5796d1a7ed4c5bfd323753d8cf071b8d4bba7b4c00a5432ad
service.py  796a81d97353f841ad5b9a0485b880d9903605e648b318f57e0164dc8f8cde5a
```

## 2. Блокирующие и существенные дефекты

### F01 — P0: runner зарегистрирован без рабочих зависимостей и recovery

`paper_trading/adapter.py:203` создаёт `PaperTradingRunner.from_env()` без market_data, command_source и recovery_store. Регистрируется `run_cycle`, но ни `recover()`, ни подключение этих адаптеров не выполняется. В runner начальные `_recovered=False`, `_entries_blocked=True`; оценка сигналов требует обратного состояния.

Воспроизведено в отдельном локальном процессе с fake scheduler, без БД/Redis: после register_runner и одного run_cycle `recovered=false`, `entries_blocked=true`, все три зависимости отсутствуют. Таким образом, даже запуск дня сам по себе не подключит рынок и очередь команд.

Исправление: реальные PostgreSQL/market/command адаптеры, recovery до разрешения входов, per-account lifecycle и отказ readiness при незавершённой инициализации. Не исправлять простым присваиванием `_entries_blocked=False`.

### F02 — P0: нет цепочки signal → order → fill → ledger

`paper_trading/runner.py:475` при найденном сигнале сохраняет его только в памяти, создаёт текст Decision и пишет log. Нет вызова risk/execution/repository для заявки, fill, позиции и проводок. Поиск вызовов `execute_market_order`, `record_fill` и `warm_up` не находит рабочего соединения с collector runner; warm_up вызывается в acceptance script, но не в runtime-регистрации.

SL/TP в `runner.py:392` удаляет позицию из словаря. Аналогично close_all и manual close не проводят закрывающий fill и P&L; close_position к тому же использует account_id как position_id (`runner.py:443`). start_day/finish_day и прочие команды лишь acknowledged (`runner.py:469`), без завершённого бизнес-перехода.

Исправление: единая транзакционная обработка команд и fills, отдельные mark trigger/bid-ask execution, ledger и protection recovery. Удаление записи из памяти не является финансовым закрытием.

### F03 — P1: управление падает на несовместимом контракте repository

`PaperRepository.submit_command` требует `command_id` и `CommandType` (`repository.py:390`). Service manual order, close_position, pause_auto, resume_auto, finish_day вызывают его без command_id и со строковым command_type (`service.py:214`, 246, 269, 289, 305).

Локальное воспроизведение с настоящим методом repository и моками только read getters:

```text
manual_order TypeError PaperRepository.submit_command() missing 1 required positional argument: 'command_id'
pause_auto   TypeError PaperRepository.submit_command() missing 1 required positional argument: 'command_id'
resume_auto  TypeError PaperRepository.submit_command() missing 1 required positional argument: 'command_id'
finish_day   TypeError PaperRepository.submit_command() missing 1 required positional argument: 'command_id'
```

Никакой production write при этом не выполнялся. Исправлять весь контракт: UUID, enum, возвращаемый Command и `command_id`, а не очередное отдельное поле. `str(cmd)` не равно идентификатору команды.

### F04 — P1: Telegram/WebUI по-прежнему показывают разные контуры

`adapter.py:49` и 55 генерируют разные UUID namespace inputs `tg:<id>` и `webui:<username>`. Проверенного identity mapping между ними нет. WebUI использует жёстко заданного admin (`webui/paper_api.py:258`), а не identity текущей сессии.

Telegram start пробует новый домен (`pipeline.py:432`), но `_build_status_response` продолжает читать старые session_manager/session_metrics/executed_trades (`pipeline.py:578`). Ошибки нового start скрываются fallback в legacy. В WebUI также остаются legacy handlers/state и fallback. Это может дать «день запущен» в одном хранилище и «нет сессии» в другом.

Исправление: общий проверенный owner mapping, одна команда/проекция статуса для обоих UI, явный cutover без тихого перехода между финансовыми контурами. Дополнительно close_position/get_command_status доменного сервиса не проверяют принадлежность владельцу; это следует закрыть до подключения рабочего исполнителя.

### F05 — P1: пользовательские настройки заменяются значениями в коде

`service.py:82`: конец дня всегда now+8h вместо выбранных end_time_local/timezone. WebUI start не передаёт сохранённые settings, Telegram start не передаёт budget/risk, хотя сообщает их пользователю. WebUI ответы содержат cash=1000, engine_status=running и engine_heartbeat_age=2 как константы (`paper_api.py:264–285`, start handler).

Исправление: сохранить настройки владельца, вычислять реальное время окончания, читать баланс и heartbeat из источника истины; исключить «работает» без фактического подтверждения.

### F06 — P1: положительные acceptance результаты не подтверждают заявленные проверки

`scripts/run_paper_acceptance.py:401–452`: replay использует сгенерированные бары, не recorded fixture. Если сигнала нет, всё равно `passed=true`. Это воспроизведено. Если сигнал есть, script арифметически предполагает выход по TP, без production order/fill/ledger цикла и без подтверждения рынком.

`scripts/run_paper_acceptance.py:460–484`: live-readonly создаёт новый локальный runner без рыночного и DB источников и без чтения работающего экземпляра; возвращает passed=true. Такой режим нельзя выдавать за live acceptance. Этот режим изучен по коду, но не запускался как проверка production.

Исправление: отсутствие обязательного цикла/источника → FAIL или BLOCKED, read-only наблюдение реального runner, записанный replay long+short через тот же финансовый engine и PostgreSQL.

### F07 — P1: финансовая достоверность и восстановление не завершены

`market.py:379`: available_quantity вычисляется как доля от константы 1, а не фактического объёма котировки. Это не проверка top-of-book liquidity. `execution.py` рассчитывает notional как qty×price без умножения на контрактный multiplier; требуется единый контракт единиц qty. После округления filled quantity остаток не пересчитывается. В runtime отсутствует соединение этих расчётов с общими сущностями и журналом.

Fencing (`runner.py:208–225`) — локальный счётчик процесса, не lease в PostgreSQL. Recovery при непустом discrepancies всё равно разблокирует входы (`runner.py:285`); это отдельный дефект, который проявится после подключения store. `record_fill` и проводки находятся в отдельных repository операциях: общего атомарного trade commit в runner/service не найдено.

Исправление: реальная metadata/единицы, observed liquidity, persistent fencing, fail-closed reconciliation и одна транзакция финансового эффекта. Проверить на disposable PostgreSQL с гонками/crash, не только арифметикой.

### F08 — P2: AI, обучение и эксплуатационная поставка остаются частичными

AIPlanner и LearningEvaluator существуют, но их рабочее подключение к runner/day closeout не найдено. AI дневные счётчики хранятся в dict (`planner.py:131–132`), исчезают при рестарте. Learning config декларирует purge_gap, но chronological split не реализует его исключение. Финансовый closeout не выполнен, поэтому автоматическое обучение по закрытому дню не подтверждено.

`docs/PAPER-TRADING-IMPLEMENTATION.md` сохраняет «T02+ — будут добавлены по мере реализации», старые сведения и неполную эксплуатационную инструкцию. Незакоммиченные runtime зависимости остаются в дереве; чистый воспроизводимый release не принят.

## 3. Почему входной AC-26 PASS недостоверен

Исходные observations: 13 точек за 74.3417 минуты, максимальный интервал между точками 8.2627 минуты. Такие точки подтверждают состояние в момент снимков, но не отсутствие кратковременных остановок между ними. «12/12» в тексте отчёта не соответствует 13 доступным значениям heartbeat age.

Во всех точках day_state семантически idle. `idle` → `DayState.idle` — изменение сериализации, не переход торгового состояния. Циклы heartbeat не выполняют требование AC-26 о переходе состояния и не доказывают обработку сигналов. `entries_blocked=true` нельзя интерпретировать как «стратегия ждёт EMA-триггер», когда evaluator не вызывается из-за незавершённого recovery.

Отчёт сам содержит WebUI unhealthy в итоговом uptime snapshot. Это не мешает контейнеру быть running, но требует разбора healthcheck и не подтверждает стабильное здоровье приложения. Сейчас WebUI healthy; историческая причина unhealthy отдельно не установлена.

Корректная оценка: **частично подтверждено наблюдение heartbeat/feed в 13 точках; AC-26 FAIL по критериям; AC-27 NOT TESTED**. Не менять ТЗ задним числом ради PASS.

## 4. Выполненные проверки

Из D:\WORED:

```powershell
git status --short
git log -8 --oneline
docker compose ps
python -m pytest tests/paper_trading -q -p no:cacheprovider
python scripts/run_paper_acceptance.py --mode replay --output -
python -m ruff check paper_trading scripts/run_paper_acceptance.py --output-format concise
python -m mypy paper_trading --no-incremental --cache-dir=NUL
```

Результаты:

- pytest нового пакета: **11 passed**, одно pytest_asyncio/Python 3.14 deprecation warning. Это unit/golden tests; название test_integration.py не делает их PostgreSQL integration.
- replay: `signal_generated=false`, `passed=true`; именно это является дефектом acceptance runner.
- Ruff: **35 ошибок**, включая unused imports, E402, unused variables; автоматические исправления не выполнялись.
- mypy 1.20.2 на локальном Python 3.14: INTERNAL ERROR; type-check не принят. Требуется повтор в заявленной QA Python 3.11, не утверждение «типизация прошла».
- Read-only runtime SQL и Redis: результаты раздела 1. Секреты не печатались; SQL выполнялся в readonly transaction.
- Mock registration и service-call probes: результаты F01/F03. Они проверяют wiring/API contracts, не заменяют DB execution.
- Дополнительный запуск `python -m pytest tests/test_paper_api.py tests/test_paper_market.py tests/test_paper_agents.py tests/test_paper_learning.py tests/test_collector_perpetual_market.py tests/test_execution_status.py -q -p no:cacheprovider` не завершился и не выдал результатов; остановлены только два подтверждённых процесса этого запуска. PASS этому набору не присваивается. Paper API fixture не изолирует новый глобальный adapter.get_service: требуется восстановить явную dependency injection, прежде чем расширять прогон.
- Live browser: подтверждена страница входа; trading actions, mobile и реальный Telegram не проверены.

Полный production migration/replay/restore/long-running soak не выполнялся: первичные блокеры делают повтор AC-26 бессмысленным до исправления. Никакие новые сделки для «проверки» в действующей сессии не создавались.

## 5. Матрица требований ТЗ

| Требование | Результат текущего аудита |
|---|---|
| REQ-01 discovery | Новый runner/БД проверены; исходная legacy сессия RACHELLO отдельно не повторно диагностировалась |
| REQ-02 безопасность | Production не менялся; реальные ордера/платные вызовы отсутствовали в аудите |
| REQ-03 общий домен | FAIL: интерфейсы/fallback/исполнение не объединены |
| REQ-04 данные/атомарность | FAIL: command contract, ownership и fencing незавершены; DB race suite отсутствует |
| REQ-05 рыночная реалистичность | FAIL: feed есть, executor не подключён; liquidity модель подменяет наблюдаемый объём |
| REQ-06 риск/учёт | PARTIAL: арифметические тесты проходят; runtime risk/fills/ledger chain отсутствует |
| REQ-07 стратегия/AI | FAIL: нет рабочего warmup/сигнал→исполнение; AI вне runner |
| REQ-08 runner/recovery | FAIL: блокировка без recovery, источников и persistent lease |
| REQ-09 UX/единый статус | FAIL по коду; полноценный live browser/Telegram BLOCKED авторизацией/непроведён |
| REQ-10 closeout/learning | FAIL: завершение команды сломано; полного pipeline нет |
| REQ-11 cutover | NOT ACCEPTED: markers=0; rehearsal/rollback не подтверждены |
| REQ-12 эксплуатация | PARTIAL: инфраструктура доступна, trading readiness и persistent budgets не обеспечены |
| REQ-13 QA | FAIL: replay false positive, лишь 11 unit tests, Ruff failures, type-check не завершён |
| REQ-14 поставка | NOT ACCEPTED: runbook и воспроизводимый release неполны |

## 6. Порядок исправления для Hermes

1. Исправить ложные PASS и оформить AC-26 как непринятый. Разделить heartbeat alive, recovery ready и trading active.
2. В isolated QA подключить store/commands/market, выполнить recovery и настоящий warmup. Не включать новые входы production простым флагом.
3. Исправить service/repository command contract; реализовать исполнение, риск, атомарный fill/position/postings и protective close.
4. Провести PostgreSQL race/crash/ownership tests и recorded replay long+short с реальными quote events; отсутствие сделки обязано проваливать этот тест.
5. Объединить identity/status/commands Telegram/WebUI; передавать реальные настройки пользователя и исключить тихий legacy fallback.
6. Завершить closeout, funding, budgets, learning, cutover rehearsal, runbook и чистую сборку. Повторить scoped lint/type/regression в QA.
7. После согласованного внедрения повторить AC-26 с действительно работающим днём, затем AC-27 по естественному сигналу. Не форсировать сделку ради счётчика.

Вывод относится к найденным и проверенным блокерам; это не заявление об отсутствии иных дефектов в непроверенных путях. Этих блокеров уже достаточно, чтобы отклонить текущую реализацию и отчёт PASS.

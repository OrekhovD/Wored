# ТЗ Hermes: завершение и сверка приёмки paper trading

Дата: 16 сентября 2026. Проект: `D:\WORED`. Основной бот: `@RACHELLO_BOT`.

## 1. Цель и нормативные источники

Продолжить `TASOCHKI/HERMES-ACTIVE-PAPER-TRADING-V1`, устранить противоречия в evidence и довести обязательные проверки до доказанного результата. Не реализовывать продукт заново. Сохранить два независимых симуляционных счёта manual/auto и путь «Сегодня → Итоги → Обучение».

Прочитать в указанном порядке:

1. `AGENTS.md` и актуальный Git diff.
2. `TASOCHKI/HERMES-ACTIVE-PAPER-TRADING-V1/README.md`.
3. `TASOCHKI/HERMES-ACTIVE-PAPER-TRADING-V1/ACCEPTANCE.md`.
4. `TASOCHKI/HERMES-ACTIVE-PAPER-TRADING-V1/START-HERMES.md` и `TASKS.json`.
5. `docs/CODEX-HERMES-AC26-AUDIT-20260915.md`.
6. `docs/PAPER-TRADING-IMPLEMENTATION.md`, `docs/PAPER-TRADING-FINAL-REPORT.md`, `docs/PAPER-TRADING-RUNBOOK.md`.
7. Этот документ и исходные артефакты, на которые ссылаются отчёты.

Исходная матрица AC-01…AC-28 уже содержит нужные требования. Это дополнение задаёт порядок закрытия и контроль достоверности отчёта; не ослабляет ни один исходный критерий. Не менять матрицу ради получения PASS. Не добавлять новый файл в исходный пакет без обновления его manifest; этот документ расположен отдельно, чтобы сохранить целостность переданного пакета.

## 2. Проверенные факты и неизвестные

Ниже — проверка файлов 16.09.2026, а не текущего production. HEAD рабочего дерева при чтении: `3f29a05`; есть tracked/untracked изменения. Перед исполнением зафиксировать новую ревизию и хэши значимых dirty-файлов.

| Источник | Что обнаружено | Действие Hermes |
|---|---|---|
| `docs/PAPER-TRADING-FINAL-REPORT.md` | В 28 строках матрицы 20 отметок PASS и 8 pending, но итог и метрика содержат 19/9 | Пересчитать машинно, проверить каждый заявленный PASS по нормативной матрице; не исправлять только число |
| Та же матрица | Pending: AC-06, AC-14, AC-16, AC-19, AC-20, AC-21, AC-24, AC-27 | Включить AC-21 в план; не придумывать отсутствующий девятый кейс |
| `artifacts/ac26_rerun_report.json` | Общий `status=PASS`, но `assertions.feed_freshness.result=FAIL` | Общий PASS недопустим до разрешения противоречия; проверить сырые данные и генератор отчёта |
| Тот же JSON | `day_states=[null]`, `day_transitions=0`; residual limits говорят, что start_day не выдавался; финальный Markdown утверждает idle→running | Установить, относятся ли заявления к разным запускам; дать точные ссылки и время. Не считать старый JSON доказательством перехода дня |
| Тот же JSON | Остаточные F07/F08 обозначены pending; финальный отчёт утверждает 8/8 исправлений | Сверить версии, регрессионные доказательства и время; старый артефакт не доказывает наличие дефекта в нынешнем коде и не доказывает его исправление |
| `tests/paper_trading/test_integration.py` | Docstring прямо указывает pure Python, без БД | Golden calculations учитывать как offline; название integration не доказывает PostgreSQL integration |
| `tests/paper_trading/fixtures/recorded_btcusdt_1m_candles.json` | Description: synthetic random walk, seed 42 | Не использовать как доказательство recorded-perpetual AC-14 |
| `docs/PAPER-TRADING-RUNBOOK.md` | Restore QA показан через production postgres container; есть placeholder пути и смешение shell-синтаксиса | Дать проверенный disposable QA restore и отдельные воспроизводимые команды PowerShell/контейнера |

Неизвестны: актуальный загруженный runtime-код, текущее состояние дня, наличие других доказательств AC-26, полнота всех заявленных PASS и доступ к настоящему Telegram-клиенту. Установить их явно; не переносить прежний runtime-статус на текущий момент.

## 3. Архитектура и границы исполнения

Сохранить существующие PostgreSQL/Redis, общий `paper_trading` domain service, исполнитель collector и адаптеры Telegram/WebUI. Active paths для проверки: `paper_trading/`, `collector/main.py`, `collector/scheduler/`, `chatbot/handlers/`, `chatbot/services/`, `webui/paper_api.py`, `webui/app.py`, миграция `migrations/paper_v2_schema.sql`. Реальный маршрут каждого вызова подтвердить импортами и trace. Legacy не объявлять активным по наличию файла.

Перед каждым исправлением назвать цель, active path, список файлов, риск регрессии и команды проверки. Передача этого дополнения Hermes на исполнение разрешает подготовку исходников, тестов и документации в рамках исходного ТЗ. Текущий запрос на написание ТЗ сам по себе не запускает эти работы.

Production env/Compose, миграции, перезапуск, управление действующим днём и Telegram-сообщения допустимы только в пределах уже выданного явного разрешения на конкретные действия. Недостающий доступ не должен останавливать независимые offline/QA работы. Вопрос на внедрение задавать после готового diff, QA evidence и проверенного rollback. Секреты, production volumes, реальные биржевые ордера и принудительные сигналы исключены из QA.

## 4. Файлы поставки

Ниже целевые результаты Hermes; новые файлы ещё не считаются существующими или проверенными. Если эквивалент уже существует, использовать его и указать точный путь в отчёте вместо дублирования.

```text
docs/
  PAPER-TRADING-IMPLEMENTATION.md                 обновить
  PAPER-TRADING-RUNBOOK.md                        обновить
  PAPER-TRADING-FINAL-REPORT.md                   сформировать из evidence
scripts/
  run_paper_acceptance.py                        расширить по исходному контракту
  validate_paper_evidence.py                     создать валидатор результатов
tests/paper_trading/
  test_acceptance_evidence.py                    проверки агрегации и ложного PASS
  test_postgres_idempotency.py                   AC-05
  test_postgres_recovery.py                      AC-06
  test_postgres_plan_races.py                    AC-16
  test_recorded_replay.py                        AC-14
  test_closeout.py                               AC-21/22
  test_migration_rehearsal.py                     AC-24
  fixtures/recorded/manifest.json                происхождение и хэши dataset
artifacts/paper-acceptance/closure-20260916/
  acceptance.json                               ровно 28 актуальных case records
  summary.md                                    автоматически вычисленная сводка
  audit-fixes.json                               F01…F08 с regression evidence
  commands.json                                 команды, exit codes, время, среда
  qa/                                           JUnit и sanitized traces
  replay/                                       dataset manifest и trade chains
  browser/                                      assertions и screenshots
  telegram/                                     mock и real-client evidence отдельно
  migration/                                    dry-run, restore, reconciliation
  live/                                         сырые samples, health и assertions
```

Старые артефакты не переписывать ради успешного результата. Новые попытки сохранять в отдельные подкаталоги с UTC run ID. Корневой `acceptance.json` ссылается на выбранную актуальную попытку, а история сохраняет неуспешные запуски.

## 5. Единый контракт evidence и статусов

Для каждого AC обязательны: `case_id`, `requirement_ids`, `status`, `evidence_level`, `utc_start`, `utc_end`, `environment`, `git_sha`, `dirty_file_hashes`, `commands`, `expected`, `actual`, `assertions`, `artifacts`, `residual_limits`. Для BLOCKED дополнительно `blocker`, `next_action`, `required_access_or_data`. До запуска timestamps могут быть null только с явной причиной отсутствия запуска.

Каждый assertion содержит имя, required=true/false, результат и ссылку на первичное доказательство. Каждый artifact содержит относительный путь и SHA-256. Учитывать более узкие результаты отдельными subchecks: offline, PostgreSQL, recorded replay, fixture browser, live browser, real Telegram, live observation, live natural trade. Они не взаимозаменяемы.

Правила агрегации:

1. Ровно AC-01…AC-28, без повторов и неизвестных ID. Сумма PASS+FAIL+BLOCKED всегда 28.
2. PASS только при выполнении всех обязательных сценариев и уровней конкретного AC. Текст «код существует», число таблиц или UNIQUE constraint не заменяют проверку поведения.
3. Любой выполненный required assertion FAIL означает FAIL соответствующего кейса. FAIL имеет приоритет над BLOCKED.
4. Невыполненный обязательный сценарий, отсутствующий dataset/клиент/доступ или evidence означает BLOCKED с конкретной причиной. Pending из старого отчёта переводится в BLOCKED, а не PASS.
5. Противоречивый отчёт с вложенным FAIL не принимается. Если ошибся сборщик, сохранить исходный артефакт, исправить сборщик, пересчитать из достаточных первичных данных либо повторить наблюдение.
6. Итоговый exit code ненулевой при FAIL/BLOCKED или невалидном evidence. `--output -` выдаёт один JSON в stdout; служебный вывод идёт в stderr.
7. Числа summary вычисляются из case records. Валидатор отклоняет 19/9 при фактических 20/8 и отклоняет общий PASS при обязательном вложенном FAIL.
8. Новые обязательные тесты: 0 skipped. Missing PostgreSQL не превращать в успешную тестовую сессию через skip.
9. На торговых кейсах хранить обезличенные owner/account/day/strategy/plan/snapshot/signal/order/fill/position IDs и fees/funding/gross/net/reconciliation. Не сохранять токены, DSN или личные Telegram IDs.
10. Старое evidence можно переиспользовать только после проверки версии, неизменности относящихся к кейсу файлов/конфигурации и наличия всех исходных assertions; решение записать. Текущий runtime старым отчётом не подтверждать.

`validate_paper_evidence.py` должен проверять структуру, обязательные уровни, полноту case inventory, локальные artifact paths без выхода из evidence root, наличие файлов, хэши и агрегацию. Семантику утверждений дополнительно проверить ревьюером. Тесты валидатора: дубликат AC, отсутствующий AC, неверная сумма, вложенный FAIL, отсутствующий artifact, неверный hash, недостаточный evidence level и корректный полный отчёт.

## 6. Этап A — сверка уже заявленных результатов

Сначала составить acceptance.json по всем 28 кейсам из исходных требований. Статус 20/8 — только подсчёт значков старой таблицы, не новая приёмка. Не ограничиваться восемью pending: перепроверить основания ранее заявленных PASS.

Особое внимание: AC-02 требует чистой сборки release, а не import в текущем контейнере; AC-05 требует одновременных PostgreSQL запросов и конфликтующего payload, а не только DDL; AC-15 — проверки ошибок/квот; AC-23 — положительный и отрицательный learning pipeline; AC-25 — изоляция и scoped lint/type/tests; AC-28 — проверенные эксплуатационные команды. По каждому из F01…F08 сопоставить исправленные файлы/версии и регрессионный тест. Подтвердить либо снять утверждение «8/8 исправлены» на основании evidence.

Выход: честная исходная матрица, список недостающих suites/data/access и reviewable план. Не ожидать естественного сигнала, пока остаются независимые QA задачи.

## 7. Этап B — PostgreSQL, гонки и завершение дня

Среда: только `docker-compose.qa.yml`, проект `wored-qa`, `postgres-qa`, БД `wored_qa`, URL из `WORED_TEST_DATABASE_URL`. Проверить resolved target внутри тестового подключения; отказать при production database/host. Существующий QA использует tmpfs, internal network и не требует production env. Перед сборкой проверить, что build context не копирует секреты в образ.

### AC-05/AC-06

- Два конкурентных подключения с одинаковым idempotency key и payload: один финансовый эффект. Retry после потери ответа не создаёт второй fill; иной payload с тем же key отклоняется.
- Fault injection до commit и после commit до ACK; перезапуск нового экземпляра runner с той же QA БД. Проверять БД и ledger, а не только return value.
- Два runner, lease expiry, новый fencing token и запись старым token: старый исполнитель не коммитит; максимум один fill/набор postings, нет orphan order/position/debit.
- Открытая позиция после recovery сохраняет защиту; recovery с неполными данными блокирует новые entries.
- Управлять гонкой barriers/events и отдельными транзакциями; sleep без гарантированного столкновения не доказывает гонку.

### AC-16

Задержать ответ AI, затем отдельно выполнить pause, day_end и публикацию новой plan version. Освобождённый старый ответ не включает торговлю и не создаёт вход; старые pending superseded; защита открытой позиции сохранена. Проверить гонку между проверкой версии и commit, а не только последовательные вызовы.

### AC-21/AC-22

Подготовить оба счёта с позициями и pending orders. Проверить повторный и одновременный finish, deadline против fill, недоступную цену, funding settlement и отказ AI. Один closeout; новых entries нет; без допустимой цены нет фиктивного fill; closed только после settlement/reconcile. Следующий день сохраняет итоговый баланс каждого счёта. AI review допускает deferred без блокировки финансового завершения.

Сверить orders→fills→positions→postings→reports, cash/equity/margin, fees/funding/gross/net. Golden values из исходной матрицы обязательны, но не заменяют транзакционный lifecycle. Основные closeout сценарии закрываются в disposable QA: ожидание production-сигнала не оправдывает отсутствие этих тестов.

Выход: JUnit, fault/race traces, before/after ledger assertions, точные команды и реальная статистика тестов.

## 8. Этап C — AC-14 recorded replay

Нужен записанный HTX perpetual event dataset: instrument/market, источник, UTC диапазон, время получения, bid/ask, mark, funding по пересечённым событиям, contract metadata, последовательность/время событий и данные для 1m/15m/1h warmup. Указать gaps, SHA-256 raw-файлов и детерминированные преобразования. Если данные недоступны — BLOCKED missing_recorded_dataset, отдельно сохранить успех synthetic arithmetic tests.

Запустить ту же baseline strategy и execution/accounting code, которые применяются в live, с тем же хэшем параметров. Получить минимум один long и один short полный цикл: закрытая свеча→естественный сигнал стратегии→последующая котировка→order→fill→position→exit→postings→net reconciliation. Не подставлять intents вручную. Повтор одного dataset должен воспроизводить финансовые события; генерацию ID/clock сделать контролируемой.

Если выбранный период не содержит нужных условий, записать неуспешную попытку и выбрать другой recorded период без изменения правил. Заранее заявленный период/manifest и история выбора обязательны. Синтетические OHLC или только bars не заменяют quote/mark replay. Если данных достаточно, но реализация не исполняет ожидаемый допустимый сигнал — FAIL, а не объяснение отсутствием dataset.

Расширить существующий runner реальным источником recorded data и документировать фактический CLI. Не публиковать выдуманный `--dataset`, пока соответствующий аргумент не реализован и не проверен.

## 9. Этап D — AC-24 migration/rollback rehearsal

На disposable QA подготовить legacy+paper fixture: оба счёта, известные и неизвестные owner mappings, pending/open/closed, проводки и конфликт cutover. Не использовать production postgres container даже для создания отдельной QA БД.

Проверить dry-run counts/checksums, orphans, unknown attribution→migration_blocked, атомарное назначение owner_engine_version и гонку старого/нового исполнителя. Повтор миграции не дублирует баланс, позиции и ledger. Одного применения CREATE TABLE IF NOT EXISTS недостаточно.

Снять backup QA, восстановить его в отдельный disposable QA экземпляр, сравнить контрольные суммы и финансовые инварианты. Rehearsal rollback должен доказать, кто защищает уже открытые позиции; отключение нового runner без владельца защиты недопустимо. Исторические проводки сохраняются. Записать команды, exit codes, время и до/после reconciliation. Отдельно дать процедуру, если restore не прошёл: запрет cutover и продолжение диагностики в QA.

## 10. Этап E — AC-19/AC-20 UI и Telegram

AC-19: настоящие browser assertions и screenshots при 1440×900 и 390×844. Путь: «Сегодня»→manual preview/open/partial close/close→auto status/plan/pause/resume→«Итоги». Проверить разделение manual/auto, fees/funding/net, понятное ожидание, command pending→result, отсутствие overflow, сохранённые routes и charts. Fixture browser и live browser оформить отдельно; согласованную live среду и допустимые мутации указать до запуска.

AC-20: сначала mock commands/callbacks с повтором и ownership checks, затем настоящий Telegram client/Mini App: status/plan, Back, safe area, повтор callback без второго финансового эффекта, pending→result. Сверить тот же owner/day/account/position между Telegram, WebUI и БД. Если нужен новый доступ или действия пользователя в клиенте — конкретный BLOCKED real_telegram_access_required; mocks не дают общего PASS AC-20.

В тестах нет внешней рассылки. Для реальных сообщений и управляющих действий использовать только явно согласованный тестовый контекст. Не завершать и не изменять действующий день пользователя ради скриншота.

## 11. Этап F — AC-26 и AC-27

### AC-26: исправить достоверность наблюдения

Сначала разобрать `ac26_rerun_report.json` и соответствующие raw observations. `feed_freshness=FAIL` исключает общий PASS. Проверить, был ли неверен сборщик, источник timestamps или сам feed. Исправление текста без доказательства недопустимо. При нехватке raw data повторить 60 минут после нужного разрешённого внедрения.

Фиксировать непрерывно heartbeat/feed/decision/recovery timestamps, значения порогов freshness, фактический last successful cycle, reason/численные условия ожидания и health `/healthz`, `/readyz`, `/api/health`. Частоту sampling выбрать с учётом реальных freshness thresholds и heartbeat interval; редкие snapshots дополнить непрерывными логами/метриками, иначе нельзя утверждать отсутствие промежуточных сбоев. При startup/recovery явно определить начало 60-минутного измерения, не выбрасывать неуспешные точки задним числом.

Минимум один наблюдаемый семантический переход состояния по исходному AC-26. Recovery blocked→unblocked описывать именно как recovery; не переименовывать в idle→running. Если day state неизвестен, так и записать; отсутствие day state не доказывает переход дня. Переходы и health assertions подтвердить raw event ссылками. Ненулевой feed assertion или недостающий обязательный уровень не маскировать heartbeat PASS. 60 минут без ошибки сами по себе не подтверждают полный естественный trade cycle.

### AC-27: естественная автоматическая сделка

После согласованного запуска дня наблюдать natural signal→auto order→fill→position→обычный/защитный exit→postings→net P&L, с тем же объектом в Telegram/WebUI. Проверять актуальный feed, версию стратегии и настройку риска. Ручной вход, тестовый intent, изменённый feed или ослабленная стратегия ради входа не засчитываются.

Если за согласованное окно сигнала нет — BLOCKED awaiting_natural_signal с последними численными условиями и временем. Нет доступа к наблюдению — отдельный blocker; ошибка исполнения уже возникшего допустимого сигнала — FAIL. Не обещать мониторинг после окончания задачи без реально настроенного механизма. Продление окна и постоянный монитор согласуются отдельно; уведомлять только о существенном изменении, завершении или требуемом действии пользователя.

## 12. Команды и проверка

Все host-команды ниже — PowerShell из `D:\WORED`. Они задают исходный запуск проверок, а не утверждают успешность приложения. До запуска тестов проверить fixtures и отсутствие production-доступа. Существующий пакет спецификации валидируется stdlib; Docker/QA используется для зависимостей приложения.

```powershell
Set-Location -LiteralPath 'D:\WORED'
git status --short
git rev-parse HEAD
python TASOCHKI/HERMES-ACTIVE-PAPER-TRADING-V1/validate_bundle.py
if ($LASTEXITCODE -ne 0) { throw 'Specification package validation failed' }
docker compose -p wored-qa -f docker-compose.qa.yml config --quiet
if ($LASTEXITCODE -ne 0) { throw 'QA Compose config failed' }
docker compose -p wored-qa -f docker-compose.qa.yml run --build --rm checks python -m pytest tests/paper_trading -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw 'Paper trading tests failed' }
docker compose -p wored-qa -f docker-compose.qa.yml run --rm checks python -m ruff check paper_trading scripts/run_paper_acceptance.py tests/paper_trading
if ($LASTEXITCODE -ne 0) { throw 'Scoped lint failed' }
docker compose -p wored-qa -f docker-compose.qa.yml run --rm checks python -m mypy paper_trading --no-incremental
if ($LASTEXITCODE -ne 0) { throw 'Scoped type checks failed' }
docker compose -p wored-qa -f docker-compose.qa.yml run --rm checks python scripts/run_paper_acceptance.py --mode offline --output -
if ($LASTEXITCODE -ne 0) { throw 'Offline acceptance failed' }
docker compose -p wored-qa -f docker-compose.qa.yml run --rm checks python scripts/run_paper_acceptance.py --mode replay --output -
if ($LASTEXITCODE -ne 0) { throw 'Replay is FAIL or BLOCKED; preserve evidence' }
```

Проверки выполнять по этапам, сохраняя каждый exit code и sanitized stdout/stderr; ошибка одного этапа не отменяет независимые проверки. После изменения тестов/исходников пересобирать QA image: текущий Dockerfile использует COPY, а не bind mount исходников. В итоговом runbook указать проверенный экспорт JSON/JUnit/screenshots из временных контейнеров на host до их удаления; исчезнувший при `--rm` отчёт не считается evidence.

После реализации нового валидатора его обязательный CLI из корня проекта:

```powershell
python scripts/validate_paper_evidence.py --input artifacts/paper-acceptance/closure-20260916/acceptance.json
if ($LASTEXITCODE -ne 0) { throw 'Acceptance evidence is incomplete, failed, blocked or inconsistent' }
```

Последняя команда — контракт будущей поставки; файл валидатора должен быть создан Hermes. В runbook дополнить точные испробованные команды PostgreSQL suites, recorded replay, browser/Telegram evidence, migration/restore, live observation и scoped checks всех реально изменённых адаптеров. Не заменять команды словами «запустить нужные тесты», не публиковать placeholders. При недоступном Docker сохранить конкретную ошибку и завершить доступные проверки, не выдавая их за PostgreSQL/runtime.

## 13. Ожидаемые результаты и документация

Обновить implementation, runbook и final report согласованно. Финальный отчёт должен содержать:

- Все 28 AC с вычисленными PASS/FAIL/BLOCKED и ссылками на первичные evidence.
- Отдельную таблицу F01…F08, версии исправлений, тесты и остаточные ограничения.
- Полный список изменённых файлов, точные команды, среду, commit/dirty hashes, число passed/failed/skipped; результаты lint и type checks отдельно.
- PostgreSQL/replay/browser/real Telegram/live уровни раздельно; synthetic и recorded явно маркированы.
- Проверенные backup/restore/rollback, сохранность manual/auto и финансовую сверку.
- Для каждого BLOCKED: причина, что уже сделано, что требуется от владельца/среды и точное следующее действие. Невыполненный тест не называть «код готов, осталось наблюдение», если suite ещё не создан.

Условия выпуска остаются исходными: «готов к внедрению», «runtime подтверждён» и «автоторговля полностью принята» имеют разные наборы доказательств. Полная приёмка — только 28/28 PASS, включая AC-27. Частичный результат допустим как честный отчёт о блокерах; это не завершённая приёмка. Разрешение на production rollout не выводится из тестовых результатов автоматически.

## 14. Готовое поручение Hermes

> Продолжи исходное ТЗ HERMES-ACTIVE-PAPER-TRADING-V1 по дополнению `docs/HERMES-PAPER-TRADING-ACCEPTANCE-CLOSURE-20260916.md`. Сначала сверь все 28 AC с первичными доказательствами: старый отчёт считает 19/9, хотя строки дают 20/8; AC-26 JSON содержит feed_freshness FAIL при общем PASS. Проверь версии артефактов и F01…F08. Затем закрой PostgreSQL race/recovery, dual-account closeout, recorded replay, migration/restore rehearsal, browser и Telegram по исходной матрице. Недоступность естественного сигнала не блокирует эти работы. Исправления и изолированные проверки готовь самостоятельно в границах исходного поручения; production-изменения и внешние действия выполняй только в рамках явного разрешения. Предоставь воспроизводимый diff, команды и evidence. Не ставь PASS за наличие кода и не добивайся сделки изменением стратегии. Заверши полную приёмку либо предъяви точные внешние блокеры после выполнения всех независимых работ.

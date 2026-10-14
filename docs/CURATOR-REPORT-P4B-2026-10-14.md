# P4b отчёт — закрытие Q-24: управление торговым днём из чата и «стоп авто» в домене

Дата: 2026-10-14. Фаза: P4b (малый срез после P4).
Baseline: `4ba9803` (P1–P4 закоммичены по подписям куратора; срез идёт от него).
ТЗ: `docs/QODER-TELEGRAM-BOT-V1-TZ-20260928.md` §5 U07, §6.1 (управление днём),
TB-03 (одно действие — одна реализация), §6 (отказ называет реальное условие).
GATE среза: Q-24 закрыт на всех поверхностях, которые он перечислял; ни один
писатель legacy `trading_sessions` в чате не остался; боевое поведение подтверждено
тестами на реальном PostgreSQL-домене, а не только на duck-типах.

Итог: **гейт пройден, Q-24 закрыт кроме одного пункта** (закрытие одной позиции из
чата — долг P4c, зафиксирован в RFC). 1642 passed в авторитетном QA-прогоне, из них
24 теста этого среза, 13 из них — на живой QA-базе. Срез **развёрнут в живом контуре**
2026-10-14 и подтверждён read-only пробником на боевом образе чата: 24 проверки, PASSED (§6.2–§6.3).

## 1. Решения куратора, которые определяли объём

| Решение | Что сделано |
|---|---|
| «Всё, включая tighten/reduce» | `/day limits <ключ>=<число>` — реальная доменная команда `set_automation`, а не «перефразируй в WebUI»; pause/resume/closeall/limits идут одним путём |
| «Закрытие позиции из чата — нет, отдельный срез P4c» | `paper_deal` по-прежнему не импортирует `close_position` (AST-гейт P4 не ослаблен); долг назван в RFC Q-24 строкой «осталось» |
| «WebUI «Стоп авто» → доменный `close_all`, в этом же срезе» | `webui/paper_api.py::automation` для `close_auto` зовёт `_pt_close_all_positions`, а не `_pt_pause_auto`; закреплено тестом, что пауза не вызывается |
| «Legacy-писатели удалить, а не оставить на всякий» | `_handle_stop_safe` / `_handle_revision_safe` и 4 callback'а `pipeline_*` удалены; распознавание фразы сохранено и отвечает redirect'ом на `/day` |
| «Идемпотентность — по попытке, кроме finish» | ключ `pause-<day>-<uuid>` и т. д.; `finish-<day>` остаётся привязанным к дню, потому что его повторяет планировщик runner'а |

## 2. Что изменено в коде (runtime path, не legacy)

| Файл | Характер | Diff |
|---|---|---|
| `paper_trading/service.py` | `_auto_control_command` — одна реализация для pause/resume/close_all; `close_all_positions(scope=auto, block_entries=True)`; `load_day_limits`/`set_day_limits`; день и авто-счёт читаются в домене, а не принимаются от клиента | +199/−40 |
| `paper_trading/runner.py` | `_handle_set_automation` (реальная ветка для команды, которая раньше не исполнялась), `_finish_command`, терминальный `failed`+`limits_changed` вместо переиздания | +111/−3 |
| `paper_trading/repository.py` | `merge_day_limits` — `SELECT … FOR UPDATE` + CAS по `risk.canonical_limit`, пустой набор → `False`, посторонние ключи снапшота сохраняются | +81/−5 |
| `paper_trading/ticket.py` | `LIMIT_FIELDS` (ключ снапшота → поле `RiskSettings`), `current_limits`, `parse_limits`, `apply_day_limits` (заменяет только 5 лимитных полей), `DENIAL_TEXTS` для новых кодов | +86/−6 |
| `paper_trading/risk.py` | **новая** `canonical_limit` + исправление формы числа (§7) | +23/−1 |
| `paper_trading/adapter.py` | проброс новых методов в чат/WebUI-адаптер | +61/−5 |
| `chatbot/handlers/day_control.py` | **новый**, 377 строк: экран `/day`, `help`, `pause`, `resume`, `finish`, `closeauto`, `limits`, `confirm <id>`, `status`; callbacks `daycmd:*`; кнопка подтверждения только владельцу | новый |
| `chatbot/services/day_control.py` | **новый**, 520 строк: use-case слой, `DayControlError` c reason-code'ами, ALIASES (включая русские; `close` намеренно отсутствует), стор тикетов попыток | новый |
| `chatbot/handlers/pipeline.py` | **удалены** `_handle_stop_safe`, `_handle_revision_safe`, `cb_pause/cb_tighten/cb_reduce/cb_close_all`, кнопки `⏸ Pause / 🛡 Tighten / 📉 Reduce / 🛑 Close all`; фраза распознаётся и отвечает redirect'ом | +69/−99 |
| `chatbot/{handlers/chat,now,paper_deal}.py`, `main.py`, `application/presenters.py`, `services/paper_orders.py` | регистрация роутера, карточки дня, переиспользование шаблона отказа | +113/−32 (`paper_orders`), +72 (`presenters`), +11/+8/+1/+5 |
| `webui/paper_api.py` | `close_auto` → `close_all_positions`; guard настроек отвечает 409 с подсказкой `/day limits` посреди живого дня | +34/−2 |
| тесты среза | `tests/paper_trading/test_day_limits_command.py` (новый, 654 строки, 24 теста), `tests/telegram/test_day_control.py` (новый, 618 строк, 38 тестов), +95 в `test_paper_api_domain.py`, +51 в `test_session_domain_only.py`, +47 в `test_command_map.py`, +15 в `test_screens.py` | — |

`webui/static/ui/trading-day.js` **не изменён**: кнопка и в прошлых срезах отправляла
`close_auto` с текстом «Закрыть все позиции автомата?» — врал не фронтенд, а бэкенд,
который маппил это действие на паузу.

Схемы БД, миграции, `docker-compose.yml`, `.env*` — **не тронуты**. Ключи окружения не
добавлялись: `docs/ENV-REGISTRY.md` (раздел P4 «no new keys») остаётся в силе.
Legacy-зоны (`chatbot/loader.py`, `collector/alerts/detector.py`, `scheduler/briefing.py`) не тронуты.

## 3. Какие дефекты Q-24 закрыты и чем

| Дефект | Закрытие | Доказательство |
|---|---|---|
| Chat-кнопки писали в legacy `trading_sessions` | писатели удалены; `/day` — единственная mutating-поверхность | `test_session_domain_only.py` (8) + статический AST-гейт, расширенный на `day_control` |
| Привязанный к дню ключ превращал повтор в тихий replay | ключ = попытка; same key + same payload → replay отдаёт **тот же** `command_id`; same key + другой payload → строка помечается `conflict`, отвечаем `idempotency_conflict` + `command_id` (MC-15) | `test_day_limits_command.py` (PG): `first["command_id"] != second["command_id"]`, `second == first` при явном ключе |
| `CommandType.close_all` существовал, но не ставился в очередь | `close_all_positions` + ветка в runner'е; `scope="auto"`, `block_entries=True` | PG-тесты цепочки command → runner → snapshot |
| `set_automation` не имел ветки исполнителя | `_handle_set_automation`: `no_active_day` / `invalid_limits` / `limits_write_failed` / `limits_changed` / `completed` | матрица отказов (host) + матрица CAS (PG) |
| WebUI «Стоп авто» врал (pause вместо ликвидации) | `_pt_close_all_positions`; тест проверяет, что `_pt_pause_auto` не вызван | `test_paper_api_domain.py`: `calls == ["close_all"]` |
| tighten/reduce были текстом без действия | `/day limits` → `set_day_limits` → `merge_day_limits` (CAS) → следующий такт исполнителя работает по новым лимитам | PG: `after["limits"]["max_risk_per_order_usdt"] == "5"` после service → claim → runner |

## 4. Гейты на хосте

```
py -m pytest tests/paper_trading/test_day_limits_command.py -q
→ 11 passed, 13 skipped            (13 — PG-кейсы: на хосте DSN `postgres-qa` не резолвится)

py -m pytest tests/telegram/test_day_control.py tests/telegram/test_command_map.py \
    tests/telegram/test_screens.py tests/telegram/test_session_domain_only.py -q
→ 90 passed                        (38 + 33 + 11 + 8)

py -m pytest tests/telegram -q
→ 275 passed

py -m pytest tests/ui/test_ticket.py tests/test_paper_api_domain.py tests/test_paper_api.py -q
→ 42 passed                        (было 37 в P4: +5 новых WebUI-тестов этого среза)
```

## 5. Авторитетный гейт: QA-образ с `--build`

```
docker compose -f docker-compose.qa.yml -p wored-qa run --rm --build checks pytest tests/ -q
→ 1642 passed, 16 skipped, 1 xfailed in 127.47s      (лог: output/p4b_qa_gate.log)
```

Baseline P4 был `1576 passed, 16 skipped, 1 xfailed` — прирост +66 тестов, ни одного
исчезнувшего. Точечный прогон файлов среза в том же контейнере:

```
docker compose -f docker-compose.qa.yml -p wored-qa run --rm checks \
    pytest tests/paper_trading/test_day_limits_command.py tests/telegram/test_day_control.py \
        tests/telegram/test_session_domain_only.py tests/telegram/test_command_map.py -q
→ 103 passed, 0 skipped
```

`0 skipped` — это и есть доказательство, что 13 PG-кейсов (CAS, replay, conflict,
матрица отказов исполнителя) реально исполнились на PostgreSQL, а не были вежливо пропущены.

### 5.1 py3.9-контур чата

`chatbot/Dockerfile` — `python:3.9-slim`, поэтому класс ошибок P4 (PEP 604 при импорте)
проверялся аудитом:

```
py output/p4_py39_audit.py                                  → модулей 131, нарушений 0
py output/p4_py39_audit.py chatbot paper_trading webui collector → модулей 180, нарушений 0
```

Число модулей печатается намеренно: голый «0» неотличим от «сканировали пустоту».

## 6. Живой контур

### 6.1 До развёртывания: read-only пробник лимитов

Пробник `output/p4b_live_limits.sql` + `output/p4b_live_limits.py` через
`docker compose exec -T postgres … psql -At` → `output/p4b-live-limits.log`; два `running`-дня:

```
max_risk_per_order_usdt = '10'   max_daily_loss_usdt = '50'
max_leverage = '10'              max_open_risk_usdt = '20'
min_net_rr = None                settings_notes = ['min_net_rr']
исполнителем применяются: max_risk_per_order=10 max_daily_loss=50 max_leverage=10
                          max_open_risk=20 min_net_rr=1.0
```

Карточка `/day status` на боевом дне покажет отсутствующий лимит по имени, а не
подставит ноль или строку «None». Ни одна строка БД не изменена, команды не создавались.

### 6.2 Развёртывание 2026-10-14 (явная команда куратора «разверни»)

```
docker compose up -d --build chatbot webui      → лог: output/p4b_deploy.log
```

Пересобрались и пересоздались `chatbot`, `webui` и `collector` — последний затронут как
зависимость (image пересобран, кода P4b в `collector/` нет: `git diff 4ba9803..HEAD -- collector/`
пустой). `postgres` и `redis` не останавливались, `down` не выполнялся.

Стартовые логи (не «Up», а именно содержание):

* `chatbot` на **python 3.9.25** — `AUTHZ: deny-by-default guard installed`,
  `SURFACE: command token rule installed before all routers`, `Chatbot started polling`,
  `Run polling for bot @W_W_O_O_bot` — crash-loop на импорте (класс ошибки P4) не случился;
* `webui` — `Application startup complete`, `Postgres pool bootstrap complete (attempt 1)`,
  `/readyz` 200;
* `collector` — `PaperTradingRunner.run_cycle` каждые 2 с зелёный, то есть ветка
  `set_automation` доступна живому исполнителю, а не только тестам.

Роуты дашборда: `/`, `/alerts`, `/predictions`, `/journal`, `/trading-day`, `/system` — все 200.
Итог `docker compose ps`: chatbot/webui healthy, collector Up, инфраструктура без изменений.

### 6.3 Живой прогон пробника развёртывания (read-only)

`output/p4b_live_deploy.py` внутри контейнера `chatbot` → `output/p4b-live-deploy.log`:
**24 проверки, P4b LIVE DEPLOY PROBE: PASSED, `{'writes': 'none'}`**.

| Что проверено | Значение |
|---|---|
| `handlers.day_control` импортируется на 3.9 и его роутер не пуст | message-хендлеров 1 (`@router.message(Command("day"))`), callback-хендлеров 4 (`daycmd:confirm:*`, `daycmd:status`, `daycmd:status:*`, `daycmd:positions`) — подкоманды разбираются внутри одного хендлера, поэтому 1, а не 9 |
| удалённые писатели отсутствуют как атрибуты модуля | `_handle_stop_safe`, `_handle_revision_safe`, `cb_pause`, `cb_tighten`, `cb_reduce`, `cb_close_all` — все absent |
| `/day`-поверхность не имеет legacy-имён и импортов (то же, что pinned тестом, но на дереве) | AST-проверка по `handlers.day_control` и `services.day_control`: `retired ∩ names = ∅`, импортов `session_manager` нет |
| в задеплоенном `pipeline.py` нет мутирующего SQL по legacy-таблице | `INSERT/UPDATE/DELETE … trading_sessions` — 0 |
| доменные методы P4b в образе | `close_all_positions`, `set_day_limits`, `load_day_limits`, `runner._handle_set_automation`, `ticket.parse_limits` |
| исправление формы числа живое | `canonical_limit("10") == "10"`, `"100000" == "100000"`, `None is None` |
| WebUI-маппинг `close_auto` | grep по `/app/paper_api.py` в контейнере webui: `elif action == "close_auto"` → `result = await _pt_close_all_positions(owner_id)` (строки 709–712) |

Состояние БД после развёртывания (`output/p4b_live_state.sql` → `output/p4b-live-state.log`):
`day\|running\|2`, `day\|closed\|54`, команды — только `completed` (`start_day 110`,
`finish_day 50`, `submit_order 3`, `close_position 1`, `pause_auto 1`), ни одной
`pending`/`processing`. То есть перезапуск не создал ни одной команды и не завершил ни
одного дня: те же два бегущих дня, что и до деплоя.

### 6.4 Честная поправка: первая версия пробника была неправильной

Первый запуск дал **4 FAIL** — проверки вида «подстроки `trading_sessions` и
`pipeline_close_all` не должно быть в файле». Это бага пробника, а не продукта: те имена
разрешено называть в docstring'ах и в тексте редиректа для оператора, а в `pipeline.py`
остались **только читающие** legacy-обработчики деталей (`_handle_plan`, `_handle_revision`
делают `SELECT` в `readonly=True` транзакции и отвечают `ERR_NO_SESSION`, поскольку легаси-строк
нет). Контракт Q-24 — «нет мутирующих писателей», и именно он проверяется тестами
`test_session_domain_only.py`; пробник переписан на тот же AST-критерий плюс отсутствие
записи в legacy-таблицу, и только после этого стал зелёным. Отчёт фиксирует обе версии, а не
только финальную.

### 6.5 Живое сообщение оператора: read-путь `/day status` подтверждён (2026-10-15)

Оператор набрал `/day status` в боевом боте и прислал карточку. Это первое живое сообщение,
прошедшее через поверхность P4b, поэтому она разобрана построчно, а не «на глаз».

```
📊 День
день 890d7b7f · состояние running
лимиты дня (settings_snapshot):
· max_daily_loss_usdt = 50
· max_leverage = 10
· max_open_risk_usdt = 20
· max_risk_per_order_usdt = 10
· min_net_rr = N/A
⚠️ нет в снапшоте дня, действует дефолт RiskSettings: min_net_rr
посреди дня лимиты меняются этим экраном (/day limits) и только через подтверждение; условия нового дня — WebUI
```

| Строка карточки | Откуда она в коде | Соответствие |
|---|---|---|
| `📊 День` | `day_control.py:198` — заголовок `📊 <b>День</b>` | есть |
| `день 890d7b7f · состояние running` | строки 180–181: `day_id[:8]` и `day_state` | есть |
| `лимиты дня (settings_snapshot):` | строка 182 (именно это имя источника, а не «risk_policy») | есть |
| пять строк `· <ключ> = <значение>`, по алфавиту | строки 184–185: обход `summary["limits"]` через `sorted()`, форма — `ticket.current_limits` + `risk.canonical_limit` | есть; порядок `max_daily_loss → max_leverage → max_open_risk → max_risk_per_order → min_net_rr` — буквальный результат сортировки |
| `⚠️ нет в снапшоте дня, действует дефолт RiskSettings: min_net_rr` | строки 186–191 | есть |
| нижний курсивный колophon | строки 192–195 | есть дословно |

`N/A` и предупреждение — не заглушка, а честное состояние данных. Read-only сверка
(`output/p4b_live_day_status.sql` → `output/p4b-live-day-status.log`):

* незакрытых дней два — `890d7b7f` и `df5549c7`, оба `running`; карточка отдала `890d7b7f`, и это
  не «первый из списка»: `status()` → `load_day()` → `pt_adapter.load_day_limits(owner_id)`
  берёт активный день **владельца запроса** (`paper_v2_days` имеет уникальный индекс «один
  незакрытый день на владельца», поэтому два `running` — это два разных владельца);
* `min_net_rr_key_present=false`, `raw=ABSENT` — ключа в `settings_snapshot` действительно нет,
  поэтому значение отдаётся дефолтом `RiskSettings.min_net_rr = Decimal("1.0")` и обязан быть
  назван в `settings_notes`. Молча подставить `1.0` было бы дефектом Q-25, которого нет;
* sidecar-ключи снапшота целы: `mode=baseline_auto`, `opening_capital=1000`,
  `max_positions_per_instrument=1`, `timezone=Asia/Bangkok`, `end_time_local=21:00`,
  `strategy_version=baseline_v1`. CAS-команда лимитов их не затёрла — ровно то, что закреплено
  тестом на `repository.merge_day_limits`;
* `last_cmd` — ничего позже `2026-10-14 21:00:02`: **`/day status` не записал ни одной команды**,
  как и обязан (read-экран). Там четыре строки `start_day completed`, и на них стоит остановиться:
  первым чтением они выглядят как двойная постановка одной команды, но
  `output/p4b_live_startday_detail.sql` → `output/p4b-live-startday-detail.log` показывает, что на
  каждого владельца их две разные — `key=start-<day>-<account>-manual` и `…-auto`, разные
  `account_id` и разные `result.account_kind`. Владельца два, счетов по два на каждого:
  2 × 2 = 4 строки на такт. Дублей нет; ключ `start_day` детерминирован парой (день, счёт), что
  для него и правильно. Пересчёт по всей базе (`output/p4b_live_startday_ratio.sql`,
  `output/p4b_live_orphan_day.sql`): `start_day` = 114 строк = 57 `-manual` + 57 `-auto` на 57
  разных дней, дней всего 58 — ровно один день (`68338058`, закрыт, владелец `bb6a547d`, создан
  2026-09-15, в снапшоте нет ни `mode`, ни `strategy_version`) существует без команды старта; это
  историческая запись более раннего контура, а не следствие P4b, и в рамках этого среза она только
  зафиксирована. Итоговые счётчики: `day|running|2`, `day|closed|56`, команды все `completed`
  (`start_day 114`, `finish_day 52`, `submit_order 3`, `close_position 1`, `pause_auto 1`) —
  ни одной `pending`/`processing`.

Чем доказано, что карточку отдал именно новый код, а не старый экран: строка
«⚠️ нет в снапшоте дня, действует дефолт RiskSettings:» существует только в
`chatbot/handlers/day_control.py:189`; у presenter'а тикета (`presenters.py:1139`) другая формулировка
(«этих лимитов нет…»), а легаси-экран такого текста не выдавал никогда. Лог чата за 36 ч — без
`ERROR` и `Traceback`; входящее сообщение хендлер не логирует, поэтому лог здесь подтверждает
только отсутствие падения, а не факт обработки — фактом обработки служит сам текст карточки плюс
пробник развёртывания (§6.3), который проверил регистрацию `/day` в диспетчере внутри живого
контейнера на python 3.9.

## 7. Дефект, найденный тестами среза (исправлен в продукте)

`risk.canonical_limit("10")` возвращал `'1E+1'`: `str(Decimal("10").normalize())` —
научная нотация. Это не косметика: та же функция кормит и **сверку CAS** в
`merge_day_limits`, и **текст карточки** оператора. Правка —
`format(Decimal(str(value)).normalize(), "f")`; `None` остаётся `None` (отсутствие
ключа ≠ ноль ≠ «None»), неразбираемое значение отдаётся как есть, чтобы карточка
показывала базу, а не чинила её молча.

```
assert canonical_limit("10") == "10"      # test_canonical_limit_stays_readable_for_the_operator
assert canonical_limit("10.0") == "10"    # нормализация работает, научной нотации нет
assert canonical_limit("0.05") == "0.05"  # и на дробных лимитах тоже
assert canonical_limit(None) is None       # отсутствие ключа ≠ ноль ≠ строка «None»
assert canonical_limit("None") == "None"   # текст из базы не чинится молча
assert applied is True                     # «10» и «10.0» сверяются по значению, не по тексту
assert wrong is False                      # отсутствие ключа и строка «None» — разные состояния
```

Живой прогон (§6) подтверждает форму на боевых данных: `'10'`, `'50'`, `'20'`.

## 8. Отклонения от буквального плана (фиксирую)

1. **`finish_day` остался привязанным к дню.** Единое правило «ключ = попытка» применимо к
   пользовательским действиям; `runner._auto_finish_expired_days` вызывает `svc.finish_day()`
   каждые 2 с, и попыточный ключ плодил бы новую команду на каждый тик. Различие
   задокументировано в docstring'е метода, а не спрятано.
2. **WebUI не получает клиентский `idempotency_key`.** У кнопки нет тикета подтверждения,
   поэтому повторный POST создаёт вторую команду; обе одинаковые и идемпотентные по
   эффекту (пауза поверх паузы). Двойное нажатие — отдельный долг UI-слоя, не домена.
3. **`close_all` обязателен с `account_id`.** В домене `close_all` без счёта = «все
   позиции исполнителя», поэтому ручная позиция не может быть закрыта этой командой
   вообще; для ручной позиции — `close_position` (P4c).
4. **Два self-inflicted провала тестов, исправленных в тестах, а не в продукте**: тело
   настроек без обязательных полей (422) и spy на несуществующий
   `PaperStore._save` (запись идёт через `store.save`).
5. **Хост по-прежнему не даёт полного зелёна**: `test_promotion.py` и 27 ошибок в
   `test_postgres_recovery.py` / `test_trader_v1_migration.py` падают на
   `socket.gaierror: postgres-qa` — ограничение окружения из
   `docs/OPERATIONAL-PROCESS.md` §2, в QA-образе эти тесты зелёные.

## 9. Чем закреплён каждый инвариант

| Инвариант | Тест |
|---|---|
| `/day`-поверхность не имеет legacy-пути (статически) | `tests/telegram/test_session_domain_only.py`: `test_day_control_surface_has_no_legacy_session_path`, `test_day_control_reaches_the_domain_only_through_the_adapter` |
| ни один chat-писатель не ходит в `trading_sessions` | `tests/telegram/test_session_domain_only.py` (AST, расширен на `day_control`) |
| legacy-фраза распознаётся, но не исполняется | `tests/telegram/test_day_control.py`: `test_control_phrase_is_still_a_product_intent` / `test_control_phrase_never_reaches_the_legacy_writer` |
| повтор pause/resume/closeall = новая команда, явный ключ = replay той же | `test_day_limits_command.py` (PG) |
| чужой payload под занятым ключом → `conflict` + существующий `command_id` | `test_day_limits_command.py` (PG) |
| CAS не перезаписывает чужое изменение, терминальный `failed` не переиздаётся | `run_and_settle` + `assert repo.writes[0] == (…, failed, None, "limits_changed")` |
| лимиты применяются, посторонние ключи снапшота живы | PG: `after["limits"]…`, merge-тесты |
| tighten/reduce не могут отключить `require_risk_tier`/`cooldown`/`order_ttl`/`taker_fee` | `apply_day_limits`-тесты (только 5 полей) |
| форма канонического числа | `test_canonical_limit_stays_readable_for_the_operator` + живой лог §6 |
| «Стоп авто» = ликвидация, а не пауза | `test_paper_api_domain.py::…close_auto…` (`calls == ["close_all"]`), 502 при недоступном домене |
| настройки посреди живого дня → 409 с `/day limits` | `test_paper_api_domain.py` (409 содержит «/day limits»), 502 без состояния |
| закрытия одной позиции из чата нет | AST-гейт P4 (`test_the_screen_never_offers_to_close_a_position`) не изменён и не ослаблен |

## 10. Открытое и не сделанное намеренно

* **P4c (долг, назван в RFC Q-24)**: закрыть **одну** позицию из чата — `close_position`
  с тикетом подтверждения. Сейчас `paper_deal` этот метод не импортирует.
* **Срез развёрнут в живом контуре 2026-10-14** (§6.2–§6.3), **read-путь подтверждён живым
  сообщением 2026-10-15** (§6.5): оператор набрал `/day status`, карточка сошлась с кодом
  построчно и не записала ни одной команды. **Мутационный путь остаётся непроверенным живьём**:
  ни одна карточка `pause`/`resume`/`closeauto`/`limits` не подтверждалась кнопкой в боевом боте,
  эндпоинт WebUI `close_auto` не вызывался — он поставил бы в очередь реальную ликвидацию позиций
  автомата; его маппинг подтверждён чтением задеплоенного исходника (§6.3). Следующая проверка,
  которая закрывает долг, — одна карточка `/day pause` → `confirm` и `/day status` после неё.
* `/day limits` меняет лимиты **текущего** дня; исторический дрейф значений (Q-25)
  остаётся: часть тактов отработала по старым лимитам, и это правильно, но в
  дашборде не видно, по каким именно лимитам была конкретная сделка.
* WebUI-кнопка без тикета подтверждения → двойное нажатие даёт две команды (§8.2).

## 11. Документы среза

* `docs/TELEGRAM-BOT-RFC.md`: Q-24 → «ЗАКРЫТО P4b (2026-10-14), кроме одного» со списком
  удалённых писателей, политикой ключей и остатком P4c; Q-25 дополнен; §7.4
  «Пауза/продолжить»; §12 U07.
* `docs/TELEGRAM-BOT-COMMAND-MAP.md`: титул (P2…P4b), дата, список источников,
  `/day` в таблице §2, новый §2.4 (подкоманды, callbacks, пять инвариантов), инвариант §2.3
  про закрытие позиции сужен до «отдельной позиции» и отмечает, что позиции автомата закрывает
  `/day closeauto`, абзац об удалении в §4. Метки legacy-кнопок сверены с
  `git show HEAD:chatbot/handlers/pipeline.py` (английские `⏸ Pause / 🛡 Tighten / 📉 Reduce /
  🛑 Close all`); «Стоп авто» — метка WebUI, и в документе они разделены.
* Доказательства: `output/p4b_qa_gate.log`, `output/p4b-live-limits.log`,
  `output/p4b_live_limits.{py,sql}`, `output/p4b_deploy.log`,
  `output/p4b-live-deploy.log` + `output/p4b_live_deploy.py`,
  `output/p4b-live-state.log` + `output/p4b_live_state.sql`,
  `output/p4b-live-day-status.log` + `output/p4b_live_day_status.sql` (сверка живой карточки §6.5),
  `output/p4b-live-startday{,-detail}.log` + `output/p4b_live_startday{,_detail,_ratio}.sql` и
  `output/p4b-live-orphan-day.log` + `output/p4b_live_orphan_day.sql` (разбор четырёх `start_day`).

## 12. Команды повторной проверки

```
# хост
py -m pytest tests/paper_trading/test_day_limits_command.py -q                    # 11 passed, 13 skipped
py -m pytest tests/telegram -q                                                    # 275 passed
py -m pytest tests/ui/test_ticket.py tests/test_paper_api_domain.py tests/test_paper_api.py -q  # 42 passed
py output/p4_py39_audit.py chatbot paper_trading webui collector                  # 180 / 0

# авторитетный гейт
docker compose -f docker-compose.qa.yml -p wored-qa run --rm --build checks pytest tests/ -q   # 1642 passed

# PG-часть именно этого среза (0 skipped = база живая)
docker compose -f docker-compose.qa.yml -p wored-qa run --rm checks \
    pytest tests/paper_trading/test_day_limits_command.py tests/telegram/test_day_control.py \
        tests/telegram/test_session_domain_only.py tests/telegram/test_command_map.py -q        # 103 passed

# живой read-only пробник лимитов (только SELECT)
Get-Content output\p4b_live_limits.sql -Raw |
  docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' |
  py output\p4b_live_limits.py

# сверка живой карточки /day status (§6.5, только SELECT)
foreach ($f in 'p4b_live_day_status','p4b_live_startday','p4b_live_startday_detail',
               'p4b_live_startday_ratio','p4b_live_orphan_day') {
  Get-Content "output\$f.sql" -Raw |
    docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' }
```

### 12.1 Команды развёртывания и проверки боя

```
docker compose up -d --build chatbot webui                 # лог: output/p4b_deploy.log
docker compose ps --format "{{.Name}} {{.Status}}"        # chatbot/webui healthy
docker compose logs chatbot --tail=40                    # 3.9: polling without crash-loop
docker compose logs collector --tail=10                  # run_cycle каждые 2 с

# пробник развёртывания (read-only, внутри контейнера чата на python 3.9)
docker cp output/p4b_live_deploy.py htx_trading_bot_chatbot:/tmp/p4b_live_deploy.py
docker compose exec -T chatbot python /tmp/p4b_live_deploy.py     # 24 checks, PASSED
Get-Content output\p4b_live_state.sql -Raw |
  docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At'

foreach ($p in '/','/alerts','/predictions','/journal','/trading-day','/system') {
  (Invoke-WebRequest -UseBasicParsing "http://localhost:8080$p" -TimeoutSec 15).StatusCode }
```

Копия пробника `/tmp/p4b_live_deploy.py` из контейнера `chatbot` **удалена** — `ls /tmp` даёт только
предсуществующий `tmplyvue_fbcacert.pem`. Первая попытка (и `rm -f`, и `python -c os.remove`)
была заблокирована песочницей хоста, поэтому удаление выполнялось прогоном вне песочницы, по
явному разрешению куратора. Секретов файл не содержал, но оставлять копии пробников в живом
контейнере не принято: они переживают `restart` и путают следующую проверку.
Удаление не затронуло сервис: `docker compose ps` — chatbot/webui healthy, тот же аптайм,
в логах чата после 17:33:25 новых строк нет (polling и watchdog идут своим циклом).

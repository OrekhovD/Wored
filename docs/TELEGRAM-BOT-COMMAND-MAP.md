# WORED Telegram Bot — карта команд (P2…P3c.2)

Цель документа: зафиксировать, какая Telegram-команда к какому use case идёт,
где живут устаревшие имена и что ещё не перенесено в новую консоль.
ТЗ §2 / §10 требует этот файл; TB-03 проверяется по нему.

Обновлён: 2026-10-13 (P3c.2 — правила поверхности ввода: опечатка в команде и
холостой перерисов карточки).
Источник истины: `chatbot/handlers/now.py`, `chatbot/handlers/simulation_v3.py`,
`chatbot/handlers/telegram_surface.py`, `chatbot/services/simulation_plans.py`,
`chatbot/handlers/deprecated.py`, `chatbot/handlers/menu.py`,
`chatbot/handlers/start.py`.
Проверка соответствия: `python -m pytest tests/telegram/test_command_map.py -q`
(тесты читают исходники и падают, если у команды появляется второй владелец).

---

## 1. Порядок разрешения

aiogram использует первый подключённый router для одинаковых фильтров, поэтому
`chatbot/main.py` подключает роутеры в явном порядке:

1. `security.guards` outer middleware — deny-by-default, до любого хендлера;
2. `handlers.telegram_surface.command_surface_middleware` — outer middleware
   сообщений, который нормализует токен команды (`/plans,` → `/plans`) до того,
   как любой фильтр его промажет (P3c.2, Q-15). Ставится одной строкой в
   `main.py` сразу после guards и до всех `include_router` — правило принадлежит
   процессу, а не экрану (TB-03); проверяется тестом
   `test_main_installs_the_surface_middleware_once_before_every_router`; при
   старте в логи пишется `SURFACE: command token rule installed before all
   routers`;
3. `handlers.now` (console) — новые команды;
4. `handlers.simulation_v3` — канонический `/plans` (P3c). Подключается до
   `handlers.deprecated`, иначе исторический редирект `/plan` мог бы закрыть
   собой новый экран;
5. `handlers.deprecated` — старые имена, только редирект;
6. legacy-роутеры (`market`, `predictions`, `plans`, `portfolio`, `trader`,
   `start`) остаются подключёнными, но их `Command(...)`-привязки сняты,
   иначе одна команда получила бы две реализации с разными цифрами.
   `handlers/plans.py` в P3c не регистрирует вообще ни одного хендлера
   (проверяется `test_legacy_plans_router_registers_nothing`).

Правило TB-03: у одной команды ровно один владелец; кнопка reply-keyboard и
команда разрешаются в одну и ту же функцию-экран (не в две разные выборки).

### 1.1 Если команда набрана с опечаткой (P3c.2, Q-15)

Два случая, оба закрыты в `chatbot/handlers/telegram_surface.py`:

| Случай | Поведение | Почему так |
|---|---|---|
| хвостовая пунктуация прилипла к токену: `/plans,`, `/market!`, `/now.` | токен чистится, команда работает как обычная | aiogram читает токен до первого пробела: `plans,` ≠ `plans`, и раньше такое сообщение вообще не матчилось ни одному фильтру |
| команда неизвестна или имя набрано с ошибкой: `/wtf`, `/palns` | честный отказ «🤔 Команда не распознана … `/help`» без торговых чисел | текст, похожий на команду, **не уходит в модель**: раньше он попадал в `cmd_chat` → intent `trade_plan` → сгенерированный «торговый план» без `plan_hash`, violations и контракта (нарушение «no invented numbers») |

Аргументы не меняются: `/plans show <id>` остаётся байт-в-байт тем же вводом и
даёт `plan_not_found` (незаменённый плейсхолдер — это ошибка ввода, а не дефект).
Обычный разговорный текст (`объясни что такое funding rate`) и детерминированные
keyword-интенты (`покажи рынок`) работают как раньше: отказ стоит в `cmd_chat`
**после** product-intent ветвления, до обращения к LLM.

## 2. Активный набор команд (console)

| Команда | Экран | Источник данных | Фаза ТЗ |
|---|---|---|---|
| `/start` | `show_now` + reply-клавиатура | market + forecast + session | P2 |
| `/now` | `show_now` (главная карточка) | Redis snapshot, postgres forecast, `paper_trading.adapter` | P2 |
| `/market` | `show_market_detail` | Redis `market:perpetual:htx:<contract>` | P2 |
| `/forecast` | `show_forecast` | `storage.postgres_client.get_latest_prediction_request` | P2 |
| `/session` | `show_session` | `paper_trading.adapter.get_current_state` | P2 |
| `/positions` | `show_positions` | тот же session read-model | P2 |
| `/report` | `show_reports` | `paper_trading.adapter.list_day_reports` | P2 |
| `/help` | `show_help` | — | P2 |
| `/cancel` | служебный ответ `cmd_cancel` | — (wizard живёт с P3c, черновик — в хранилище планов) | P2/P3c |
| `/plans` | `handlers.simulation_v3.cmd_plans` | `trading_math.simulation_store` + `trading_math.plan_validation` + `trading_math.simulation_proposal` | P3c/P3d |
| `/deal` | `handlers.paper_deal.cmd_deal` | `paper_trading.ticket.build_ticket` (тот же, что у WebUI) + `paper_trading.adapter` | P4 |

### 2.1 `/plans` — экраны SimulationPlanV1 (P3c)

| Подкоманда | Экран | Право | Примечание |
|---|---|---|---|
| `/plans` | `show_plans` | owner/admin/viewer | список своих планов |
| `/plans help` | `show_help` (текст внутри роутера) | любой | синтаксис |
| `/plans list <status>` | `show_filtered` | любой | `draft` / `approved` / `archived` |
| `/plans show <id>` | `show_plan` | любой | id можно усечь; неоднозначная усечка → `ambiguous_plan_id` |
| `/plans new` | `new_draft` | только owner | валидный скелет-черновик |
| `/plans set <id> <field> <value>` | `set_plan_field` | только owner | поле из `PLAN_FIELDS`, иначе `field_unknown` |
| `/plans propose` | `show_questionnaire` | owner/admin/viewer | карточка анкеты; ключи и допустимые значения берутся из домена (`questionnaire_options()`), там же — честная подпись источника черновика (`source_label()`) |
| `/plans propose <ключ>=<значение> …` | `run_proposal` | только owner | единственный вход в конвейер предложения; ответ разбирает детерминированный парсер, не LLM |
| `/plans proposals` | `list_proposals` | owner/admin/viewer | последние ≤5 предложений с статусом и привязанным черновиком |
| `/plans proposal <id>` | `show_proposal` | owner/admin/viewer | полный uuid или однозначный префикс (тот же `plans.resolve_prefixed`, что и для планов) |
| `/plans adopt <id>` | `adopt_proposal` | только owner | предложение → обычный черновик; вето домена неотразимо |

Inline-callbacks: `plan:list`, `plan:new`, `plan:show:<id>`, `plan:appr:<id>:<hash12>`,
`plan:confirm:<id>:<hash12>`. Длина callback-data ≤ 64 байт, финансовых значений
в callback нет — только id и 12-символьный токен хэша.
Proposal-callbacks (P3d): `prop:help`, `prop:list`, `prop:show:<uuid>`,
`prop:adopt:<uuid>` — те же правила (только id; `prop:adopt:<uuid>` =
47 байт при лимите 64).

Политика записи (TB-03, один pipeline с дашбордом):

* значение, которое не проходит **разбор** (`field_range`, `field_enum`,
  `unknown_field`, `field_required`), не записывается вовсе — черновик остаётся
  байт-в-байт прежним (`ContractRefused`);
* значение, которое разобрано, но нарушает **смысловые** связи (`loss_hierarchy`,
  `live_window_past`, `missing_intervals`), записывается и блокирует утверждение —
  ровно как в форме дашборда;
* `violations` и `plan_hash` берутся из `trading_math.plan_validation.validate_plan`,
  того же вызова, что делает `POST /api/v3/simulation/plans/validate`.

Утверждение — два шага: карточка показывает хэш, первая кнопка требует
подтверждения, вторая прогоняет pipeline заново и только затем зовёт
`store.approve_plan`; повторное нажатие даёт `PlanImmutable`. Старт сессии в
боте отсутствует намеренно (Q-10, расширено Q-13).

Inline-навигация карточек (только переходы; финансовых значений в callback нет):
`nav:now`, `nav:forecast`, `nav:session`, `nav:positions`, `nav:report`,
`nav:more`, `nav:help`, `now:refresh`.

### 2.2 `/plans propose` — предложение агента (P3d, функция ТЗ U05)

Конвейер один на оба интерфейса: `trading_math/simulation_proposal.py` (анкета →
источник черновика → тот же `normalize_plan`/`check_plan_semantics`/`plan_hash`),
webui `POST /api/v3/simulation/plan-proposals` и чат идут через него же, поэтому
карточка в Telegram показывает ровно те же `violations` и ровно тот же
`plan_hash`, что отдаёт дашборд (гейт P3).

* Источник черновика по умолчанию — бесплатный детерминированный rule-engine
  (`wored-rules-v1`). Платный агент включается только `BOT_PROPOSAL_SOURCE=llm`;
  без этого флага клиент провайдера не открывается вовсе.
* Предложение ничего не утверждает и не запускает: кнопка «📥 Принять как
  черновик» создаёт обычный `draft` с полями предложения, утверждение остаётся
  двухшаговым с карточки плана, старт сессии — только WebUI (Q-10/Q-13).
* Вето домена неотразимо: `adoptable=false` (например `loss_hierarchy`) —
  кнопки «Принять» нет, а вместо хэша честная строка «plan_hash не вычислен».
* Отказ — это состояние, не исключение: `llm_timeout` / `llm_quota` /
  `llm_unavailable` / `llm_invalid_response` / `questionnaire_invalid` /
  `unknown_instrument`. Карточка отказа всегда оставляет ручной путь
  (`/plans new` + `/plans set`), а повторное нажатие «Принять» отвечает уже
  созданный черновик вместо двойника (`PROPOSAL_ADOPTED … bound=True`).

### 2.3 `/deal` — ручная paper-сделка (P4, функция ТЗ U07)

| Подкоманда | Экран | Право | Примечание |
|---|---|---|---|
| `/deal` | `show_form` | owner/admin/viewer | форма команды и список того, чем отвечает экран |
| `/deal help` | `show_form` | любой | тот же текст, отдельной реализации нет (TB-03) |
| `/deal preview <ключ>=<значение> …` | `show_ticket` | owner/admin/viewer | только карточка: до нажатия кнопки ни одна команда не записана |
| `<ключ>=<значение> …` (голые аргументы) | `show_ticket` | owner/admin/viewer | те же аргументы без префикса `/deal preview`; их распознаёт `paper_orders.recognises_ticket_args` |
| `/deal status <id>` | `show_status` | любой | свой `command_id` или однозначный префикс; чужой неотличим от несуществующего |
| `/deal positions` | `handlers.now.show_positions` | любой | существующий экран позиций, копия не введена |

Inline-callbacks: `deal:form`, `deal:confirm:<8hex>`, `deal:rebuild:<8hex>`,
`deal:status:<command_id>`, `deal:positions` плюс навигационные `nav:*`. В
callback нет ни цены, ни объёма — только id; максимальная длина в живом прогоне
21 байт при лимите 64 (`output/p4-live-preview.log`).

Инварианты среза (каждый закреплён тестом):

* **числа считает домен.** У ручной сделки одна реализация —
  `paper_trading/ticket.py::build_ticket`; и WebUI (`_preview` как presenter), и
  чат зовут её же, поэтому карточки побайтово совпадают на одинаковых входах
  (проверка на живом контуре: `output/p4_ticket_parity.py`, verdict
  `IDENTICAL`). Своей формулы объёма, маржи или ликвидации в чате нет;
* **TTL карточки 30 с** = `TICKET_TTL_SECONDS` из домена, а не локальная
  константа чата; просроченный тикет — `ticket_expired`, а не «не найден»;
* **идемпотентность детерминирована тикетом**: `tg-<owner>-<ticket_id>`, тот же
  тикет → повтор команды с тем же `command_id` и одним fill, изменённый тикет →
  новый id, а не конфликт; другой payload под тем же ключом →
  `idempotency_conflict` с существующим `command_id` (Q-22, MC-15);
* **подтверждение не имеет власти над старыми числами**: состояние перечитывается,
  тикет пересобирается, расхождение дня, аккаунта или свежести стакана →
  `ticket_changed` до любой записи;
* **статус, который не прочитан, — «не подтверждено»**, а не «ошибка»: карточка
  `RESULT_UNKNOWN` обязана назвать `command_id`;
* **закрытия позиции из чата нет** (объём P4; долг — RFC Q-24): экран не
  импортирует и не вызывает `close_position`, тест
  `test_the_screen_never_offers_to_close_a_position`;
* **права**: мутации — только owner (`MUTATING_ROLES`); кнопка подтверждения не
  рисуется тому, кто не может писать; авторизация — числовой `from_user.id`,
  `owner_id` из тела/аргументов входом не является;
* **день по-прежнему запускается только в WebUI** (Q-10/Q-13): без активного дня
  `/deal preview` отвечает `no_active_day` и не создаёт позицию в песочнице;
  «домен недоступен» и «дня нет» — разные тексты (ТЗ §6);
* **голые аргументы анкеты не уходят в LLM** (Q-20): `handlers/chat.py` даёт
  честный отказ с полным шаблоном команды, модель не вызывается вовсе.

## 3. Устаревшие имена → целевой use case

Механика: одно служебное сообщение-редирект + тот же экран-функция, что у
целевой команды. Тексты карточки при этом побайтово совпадают (проверяется
тестом `test_legacy_redirect_renders_identical`).

| Старая команда | Целевая команда | Экран | Примечание |
|---|---|---|---|
| `/predictions` | `/forecast` | `show_forecast` | second implementation удалена |
| `/portfolio` | `/positions` | `show_positions` | TB-03: одинаковые балансы |
| `/trader` | `/session` | `show_session` | legacy daily-pipeline, см. §4 |
| `/plan` | `/plans` | `handlers.simulation_v3.show_plans` | P3c: побайтово тот же экран, что `/plans`; подкоманды только в `/plans` |
| `/diff` | `/session` | `show_session` | legacy daily-pipeline, см. §4 |
| `/accuracy` | `/report` | `show_reports` | ledger-derived итоги |

После P3c `/plans` больше **не** редиректится: команда стала канонической и
принадлежит `handlers/simulation_v3.py`. Проверка идентичности текста `/plan` и
`/plans` закреплена тестом `test_plan_redirect_targets_the_v3_plan_screen`.

Явно НЕ редиректится в P2:

| Команда | Почему | План |
|---|---|---|
| `/settings` | управляет AI-routing предпочтениями, финансового read-model не дублирует; замена — U10/U11 | P6 |
| `/alerts`, `/news`, `/analytics` (`/analysis`), `/admin` | это не read-вертикаль консоли; свои экраны и свои права | P5/P6 |

## 4. Legacy daily-pipeline экраны

`/trader`, `/diff`, `/accuracy` исторически обслуживались
таблицами `trading_sessions` / `session_plans`. Решение куратора 2026-10-08
(Q-01): единственный путь — `paper_trading` (V3 + paper_v2). Поэтому:

* silent fallthrough в `handlers/pipeline.py` удалён: при недоступности
  домена старт возвращается ошибкой `BACKEND_UNAVAILABLE` и не пишет ничего;
* идемпотентная ветка «день уже идёт» рендерится из domain state, а не из
  legacy `_build_status_response` (иначе бот сказал бы «нет сессии» при
  работающем paper_v2 дне);
* редирект старой команды дописывает строку о том, что старые таблицы не
  затрагиваются;
* защита от регресса: `tests/telegram/test_session_domain_only.py` — runtime
  guard (legacy writer бросает исключение) и статическая проверка исходника.

## 5. Reply-keyboard кнопки

| Кнопка | Куда ведёт | TB-03 |
|---|---|---|
| ⚡ Command Deck | Mini App `web_app` (только https-origin) | — |
| 📊 Рынок | `console.show_market_detail` | тот же экран, что `/market` |
| 📦 Портфель | `console.show_positions` | тот же экран, что `/positions` |
| 🎯 Сессия | `console.show_session` | тот же экран, что `/session` |
| 🧠 Аналитика | `analytics.show_analytics_menu` | legacy surface, P6 |
| 🔮 Прогнозы | `predictions.send_prediction_menu` | legacy *создание* прогноза, P3/P6 |
| 🔔 Алерты | `alerts.send_alerts` | legacy surface, P5 |
| 📋 Планы | `plan:list` callback → `handlers.simulation_v3.show_plans` | P3c: тот же экран, что `/plans`; кнопка появляется только там, где уместен список (`nav_keyboard(with_plans=True)`) |

Кнопки «создания» (Аналитика/Прогнозы/Алерты) сохранены как есть: они открывают
не read-экран, а сценарии, которые переезжают в P3/P5/P6.

## 6. Права и deny-by-default

* Авторизация — только числовой `from_user.id` из allowlist. Имя пользователя,
  display name и любой клиентский `owner_id` входом не являются.
* Canonical ключ: `BOT_OWNER_IDS`. Временные алиасы: `TELEGRAM_ADMIN_IDS`,
  `TELEGRAM_ADMIN_ID` (использование алиаса логируется по имени, без значений).
* Роль читателя: owner/admin/viewer. Мутирующие действия: только owner.
* Лист читается fail-closed: один нечисловой токен обесценивает весь список.
* **Если allowlist пуст — отказано всем.** Перед первым рестартом chatbot
  необходимо задать `BOT_OWNER_IDS` (см. `docs/TELEGRAM-BOT-OPERATIONS.md`).
* Групповые чаты по умолчанию запрещены (`BOT_ALLOW_GROUPS`), отказ
  логируется без содержательных данных.
* Throttle на обновление карточки: `BOT_MARKET_REFRESH_MIN_SECONDS` (default 2 s).
* Wizard-черновик живёт в хранилище планов, а не в памяти чата; TTL черновика —
  `BOT_WIZARD_TTL_HOURS` (default 24, мусор/≤0 тоже дают 24). Просроченный черновик
  остаётся в списке, но любое действие на нём даёт `draft_expired`.
* Мутации плана (`/plans new`, `/plans set`, утверждение) — только роль `owner`
  (`MUTATING_ROLES`); у writer'а нет memory-fallback: если Postgres недоступен,
  бот отвечает `simulation_store_unavailable` и не создаёт невидимый дашборду
  черновик.

### 6.1 Холостая перерисовка карточки — не сбой (P3c.2, Q-16)

Повторное нажатие на кнопку навигации, когда данные не изменились, — это
`edit_text` с идентичным содержимым, а Telegram отвечает на него штатным
no-op (`message is not modified`). Раньше этот ответ ловился как
`except Exception` и рисовался красным `BACKEND_UNAVAILABLE / экран не
сформирован` — ложная авария сервиса данных.

Сейчас предикат один — `telegram_surface.is_no_op_edit`, и используют его
только `_reply` консоли и `_reply` план-роутера (они возвращают `bool`):
карточка остаётся на экране, callback отвечает нормально, в логе —
`… card already up to date`. `BACKEND_UNAVAILABLE` остаётся честным кодом
ошибки для настоящих сбоев чтения (тест
`test_genuine_screen_failure_still_produces_the_error_card`), поэтому
классификация ошибок из ТЗ §6 не размывается. Не путать с throttle
(`BOT_MARKET_REFRESH_MIN_SECONDS`) — то ограничение на частоту перечитов
рынка, а не на перерисовку.

## 7. Проверка

```powershell
cd D:\WORED
python -m pytest tests/telegram -q
python -m pytest chatbot/tests -q
python -m pytest tests/test_f16_telegram_contract.py tests/paper_trading/test_telegram_mock.py -q
```

Команды запускаются раздельно: в одном pytest-прогоне `chatbot/tests` и
корневые `tests/` дают ошибку сбора из-за коллизии пакета `tests`
(дефект D-12 в RFC), а рядом существует однотипная коллизия top-level пакета
`storage` — collector против chatbot (дефект D-13, обход зашит в
`tests/telegram/conftest.py`).

Ожидаемый результат: 167 passed / 26 passed / 14 passed (P3c: `tests/telegram`
вырос с 106 до 129 — `tests/telegram/test_plan_screens.py`, 23 теста;
P3c.2: 129 → 167 — `tests/telegram/test_slash_surface.py`, 38 тестов на обе
правки поверхности ввода).

Часть этого уже подтверждена живым Telegram-логом 2026-10-12: `/portfolio` даёт
строку редиректа и тот же экран `📦 Позиции`, что `/positions`. Live-подтверждение
P3c (данные, не Telegram-транспорт): черновик, созданный кодом бота в chatbot-
контейнере (Python 3.9), читается из webui-контейнера (Python 3.11) под тем же
каноническим uuid5-owner с идентичным `plan_hash` и пустым списком violations.
Живое подтверждение P3c.2 (13.10, Telegram Desktop, логи `chatbot`):
`command token normalized: '/plans,' -> '/plans'` → экран `📋 Мои планы` за
1055 мс, `unrecognised command declined without AI: '/wtf'` → карточка «Команда
не распознана» за 345 мс, `console card already up to date` на повторном
нажатии `Сессия` без `BACKEND_UNAVAILABLE` — см.
`docs/CURATOR-REPORT-P3C-2-2026-10-13.md` §7.

## 8. Rollback

Карта команд живёт только в коде хендлеров и в этом документе; миграций P3c
нет (хранилище планов создано миграцией в P3b).
Откат P2 = вернуть `chatbot/main.py` к прежнему порядку роутеров и удалить
импорты `handlers.now` / `handlers.deprecated` из `chatbot/handlers/menu.py`.
Откат P3c = убрать `simulation_v3_router` из `chatbot/main.py` и вернуть
`/plan` / `/plans` в `_REDIRECTS` `handlers/deprecated.py`; черновики, уже
записанные в `simulation_plans`, откат кода не трогает (они читаются дашбордом
как обычные draft-записи). Данные не менялись, rollback SQL не требуется.
Откат P3c.2 = убрать строку `dp.message.outer_middleware(command_surface_middleware)`
из `chatbot/main.py`, вернуть отказ в `handlers/chat.py` и холостой-edit-ветку в
`_reply`, удалить `chatbot/handlers/telegram_surface.py` и
`tests/telegram/test_slash_surface.py`; миграций и env-правок в этом срезе нет.

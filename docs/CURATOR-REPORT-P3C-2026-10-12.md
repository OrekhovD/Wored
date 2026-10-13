# P3c отчёт — экраны SimulationPlanV1 в Telegram поверх общего pipeline (WORED Telegram Bot V1)

Дата: 2026-10-12 · Commit baseline: `0f2359a` (праки не коммитятся — решение
куратора «Не коммитить») · Реализуемое задание:
`docs/QODER-TELEGRAM-BOT-V1-TZ-20260928.md`, фаза P3 (Plan), третий срез.
Exit-гейт фазы (ТЗ строка 221): **«Из UI виден тот же `plan_hash` и violations,
что в WebUI; AI failure safe»**.

Решения куратора, на которых стоит этот срез (заданы до PLAN, три ответа):

* **«Плюс каноническая `/plans`»** — `/plans` становится канонической командой,
  `/plan` остаётся редиректом на тот же экран (TB-03);
* **«Черновик + утверждение из чата»** — бот создаёт, правит и **утверждает**
  план; запуск сессии остаётся только в WebUI (расширение Q-10 → зафиксировано
  как **Q-13**);
* **«Да, вынести в trading_math»** — конвейер проверки и сборка рынка уходят в
  общий пакет, чтобы `violations` и `plan_hash` совпадали **по построению**, а не
  по договорённости двух копий (зафиксировано как **D-18**).

---

## 1. Цель и что было не так

После P3a/P3b бот умел *нормализовать* план тем же кодом и писать в *то же*
хранилище под *тем же* owner. Не хватала одного: сам **порядок проверки**
(normalize → semantics → market gate → coverage gate → worst case → `plan_hash`)
жил в теле эндпоинта `webui/simulation_api.validate_plan_endpoint`, а сборка
состояния рынка — в `webui/market_workspace`. Бот мог только продублировать
последовательность, а дубликат гейтов ТЗ запрещает: две копии расходятся field
по field, и «тот же `plan_hash` и violations» превращается в надежду.

Второй дефект всплыл при чтении переносимого кода: при несовпадении идентичности
снапшота (`identity_ok: False`) старый веб-слой обращался к
`state["quality"]["worst"]`, которого в этом ответе нет, → `KeyError` → HTTP 500
вместо честного отказа. Перенос закрывает это как violation `identity_mismatch`
(fail-closed), а не как падение.

## 2. Архитектура среза

```
trading_math/market_state.py     225 строк  НОВОЕ      (перенос 195 строк из webui: диапазоны 47, 50–66, 92–268)
trading_math/plan_validation.py  130 строк  НОВОЕ      (конвейер + инъектированные провайдеры)
webui/market_workspace.py        556 строк  РЕФАКТОР   (импорт-шим, презентация и SSE целы)
webui/simulation_api.py          523 строк  РЕФАКТОР   (эндпоинт = адаптер: отдаёт 2 reads, зовёт validate_plan)
chatbot/services/simulation_plans.py  436 строк НОВОЕ  (политика записи, TTL, approve-лестница, store-binding)
chatbot/handlers/simulation_v3.py     478 строк НОВОЕ  (экраны /plans, callbacks, rate-limit, ACK-before-work)
chatbot/application/presenters.py     731 строк  РЕФАКТОР (карточки плана)
chatbot/handlers/deprecated.py        105 строк  РЕФАКТОР (/plan → V3, /plans выведен из редиректов)
chatbot/handlers/now.py               400 строк  РЕФАКТОР (кнопка «📋 Планы», тексты /session и /cancel)
chatbot/main.py                       299 строк  РЕФАКТОР (simulation_v3_router между console и deprecated)
```

Зависимости новых модулей `trading_math` — stdlib + `plan_contract`; транспорт
(Redis/asyncpg) **инжектируется** провайдерами, поэтому модуль грузится в
контейнере бота на Python 3.9 и не тянет FastAPI. Именно это и было нужно:
у chatbot `PYTHONPATH=/app:/opt`, `/webui` в контейнер не смонтирован.

```
dashboard:  POST /api/v3/simulation/plans/validate
              └─ trading_math.plan_validation.validate_plan(market=redis-read, coverage=pg-read)
telegram:   /plans show <id> → services.simulation_plans.preview(rec.plan)
              └─ trading_math.plan_validation.validate_plan(market=redis-read, coverage=pg-read)
                     └─ trading_math.market_state.build_market_state(spec, payload, now)
```

Хранилище бота — `PostgresSimulationStore(await get_pool())`, **без**
memory-fallback: недоступный Postgres даёт `simulation_store_unavailable`, а не
невидимый дашборду черновик.

Порядок роутеров (`chatbot/main.py`, регрессионно-критично):

```python
dp.include_router(console_router)        # /now, /market, /session…
dp.include_router(simulation_v3_router)  # /plans — канонический экран
dp.include_router(deprecated_router)     # /plan → редирект; не должен затенить /plans
```

## 3. Доказательство дословности переноса

`trading_math/market_state.py` собран скриптом из строк источника (не
переписывался вручную), с проверкой якорей до записи и сравнением SHA тела
после: `source_sha == written_sha` → `verbatim=True`. Дополнительно в
двух-рантайм пробе зафиксированы дайджесты самих файлов, прочитанные **внутри
контейнеров**: `contract_sha=d3abd8d85bd06887`,
`validation_sha=339a45cee1d85c81` — идентичны в webui и chatbot, то есть оба
рантайма загружали один и тот же файл с диска.

## 4. Гейты

| Гейт | Команда | Результат |
|---|---|---|
| Telegram-вертикаль | `python -m pytest tests/telegram -q` | **129 passed** (было 106; +23 `test_plan_screens.py`) |
| Хост, сфокусированный набор | `test_market_workspace + test_simulation_api + test_plan_validation + test_simulation_proposal + test_instrument_registry + test_owner_identity` | **118 passed** |
| Хост, широкий прогон | `pytest tests --ignore=tests/test_trading_math.py --ignore=tests/ui --ignore=tests/paper_trading` | **816 passed, 33 skipped в 117.65 s** |
| MC-V3 browser (`-k simulation`) | `pytest tests/ui/test_v3_market_browser.py -k simulation` | **4 passed** |
| MC-V3 полный гейт | `pytest tests/ui/test_v3_market_browser.py tests/ui/test_v3_market_ui.py -q` | **73 passed в 115.20 s** (28 browser + 45 template/UI) |
| Линт | `ruff check` по 16 файлам среза | **All checks passed!** (pre-existing baseline: 76 ошибок в `chatbot/`, 39 в `webui/` — не трогались) |

Хост-пробелы (честно): `tests/test_trading_math.py` не собирается без
`hypothesis` (пакет не установлен), `tests/paper_trading/` требует QA-Postgres
из docker-сети на хосте → `socket.gaierror`. Оба существовали до P3c.

## 5. Два рантайма: идентичность violations и plan_hash

Проба `output/p3c_runtime_probe.py` запускалась внутри обоих контейнеров над
одним и тем же снапшотом, снятым с живого Redis-ключа
`market:perpetual:htx:BTC-USDT`, при зафиксированном `now` (детерминизм
обеспечен инжекцией входа, а не везением; `snapshot_id` нормализуется в дайджесте,
потому что это транспортная идентичность, а не семантика плана).

| Тело плана | Ожидаемое поведение | webui 3.11.16 | chatbot 3.9.25 |
|---|---|---|---|
| `valid_live` | aprovable | `plan_hash=sha256:1c74ca87…f96265`, `codes=[]`, `can_approve=true` | то же |
| `loss_hierarchy` (`max_daily_loss=400`) | смысловой блок | `codes=[loss_hierarchy]`, hash `null` | то же |
| `bad_range` (`max_leverage=99`) | блок разбора | `codes=[field_range]` | то же |
| `unknown_field` (`secret_sauce`) | блок разбора | `codes=[unknown_field]` | то же |
| `replay_gap` + coverage-провайдер | блок истории | `codes=[missing_intervals]` | то же |
| `missing_required` | 13× `field_required` + `field_enum` | идентично | идентично |

`identical_modulo_python_version=True`: два JSON-документа совпали побайтно за
пределом поля `"python"` (оно и обязано различаться). Кросс-чек: `plan_hash`
из pipeline равен `plan_hash(normalize_plan(body))`, посчитанному напрямую.
Качество рынка в пробе: `market_worst=live`, `can_enter=true`.

## 6. Живой round trip на боевой БД

Leg 1 — внутри `chatbot` (Python 3.9), кодом бота, ничего не замокано:
`create_draft` → `set_field(max_daily_loss=50)` → `preview` (живые Redis и
Postgres):

```
plan_id  12542dda-a501-460e-ade7-c437f4d686b2
owner    5f44d066-f323-5c87-8d38-b2d8f89058d8   (uuid5 канонический, D-17)
status   draft · stored_plan_hash == preview_plan_hash == sha256:d664910c…06655de1
preview_codes [] · preview_can_approve true · market_worst live · ttl_hours 24
```

Leg 2 — внутри `webui` (Python 3.11), чтение того же ряда своим
`PostgresSimulationStore` + своя Redis-ветка:

```
owner_matches true · visible_in_list true · owner_plan_count 1
stored_plan_hash == webui_plan_hash == sha256:d664910c…06655de1
webui_codes [] · webui_can_approve true
```

То есть черновик, рождённый в чате, **тот же объект** для дашборда: тот же
хэш, те же (пустые) violations, то же «можно утверждать» — на живых данных.

Рестарт и проверка surface (после подтверждения куратора):

* `docker compose restart chatbot webui` → `Up (healthy)`; polling возобновлён
  (`Run polling for bot @W_W_O_O_bot`), `Traceback`/`ERROR` в логах чата нет;
* регистрация роутера в живом 3.9-рантайме: `simulation_v3` →
  `['cmd_plans']`, `deprecated` → `['cmd_predictions','cmd_portfolio','cmd_trader','cmd_plan','cmd_accuracy','cmd_diff']`
  (`cmd_plan` = редирект, `cmd_plans` там отсутствует), legacy
  `handlers/plans.py` → **0 message-хендлеров**;
* маршруты дашборда: `/`, `/alerts`, `/predictions`, `/journal`, `/workspace`,
  `/futures-lab` → **200**, ошибок в логах webui за 10 минут нет;
* таблицы плана на месте: `simulation_plan_versions`, `simulation_sessions`,
  `simulation_plan_proposals`, `simulation_reports`;
* `BOT_WIZARD_TTL_HOURS` в живом контейнере → `24` (default).

### 6.1 Живой текст Telegram (13.10.2026, владелец) — выходной гейт P3 снят

`/plans new` в бою вернул карточку черновика, собранную тем же конвейером, что
и дашборд (лог БД: `PLAN_DRAFT_CREATED actor=5f44d066 plan=7eabfa1c
hash=sha256:45623d994d15`):

```
🆕 Новый черновик 7eabfa1c · черновик · v1
Инструмент htx:linear-swap:BTC-USDT · режим live_paper
ордер 10 · день 50 · сессия 100 · exposure 500 · поз. 2 · плечо 3x
Худший допустимый убыток: 20 USDT · min(session_loss, open*risk, daily*open)
Рынок: can_enter=true · качество=live · snapshot f0d27f92
Нарушений нет — контракт и гейты домена пройдены.
sha256:45623d994d159d9d0e2dca9c1e3c80e0f222b662ab86b76036d695bc37e72849
```

Это и есть выходной гейт фазы (ТЗ строка 221): в чате виден **тот же
`plan_hash` и тот же список violations**, что считает общий
`trading_math/plan_validation`, на живых Redis/Postgres. Хвост `p3c-telegram`
закрыт.

Тот же прогон выявил два поведения **вне диффа P3c** (в хендлере плана и в
слой AI-фолбэка); они были зафиксированы как открытые дефекты в §11.1/§11.2,
правка требовала решения куратора, потому что меняет логику `chatbot/`:

* **LIVE-1** — `/plans,` (команда с прилипшей запятой) вернул **выдуманный
  AI-торговый план** вместо экрана списков.
* **LIVE-2** — `🔴 Сервис данных недоступен / BACKEND_UNAVAILABLE` на
  **повторном** открытии неизменённого консоли-экрана.

Оба закрыты срезом P3c.2 (решение куратора 13.10: «Оба дефекта» + «Отсечь +
починить матчинг»): см. `docs/CURATOR-REPORT-P3C-2-2026-10-13.md`, строки
D-21/D-22 в §7 ниже и Q-15/Q-16 в RFC.

## 7. Дефекты, найденные и исправленные в этом срезе

| ID | Что | Как исправлено |
|---|---|---|
| **D-21** (LIVE-1, P3c.2) | `/plans,` не матчился ни одним `Command`-фильтром и уходил в `cmd_chat` → `trade_plan` intent → `premium` сгенерировал торговый план без `plan_hash` и контракта | `chatbot/handlers/telegram_surface.py`: нормализация токена в outer middleware до всех роутеров + отказ вместо LLM для slash-ввода; 38 тестов `tests/telegram/test_slash_surface.py` |
| **D-22** (LIVE-2, P3c.2) | холостой `edit_text` (`message is not modified`) рисовался как `BACKEND_UNAVAILABLE`; в план-роутере не ловился вовсе (мёртвая кнопка) | `surface.is_no_op_edit` — единственный предикат, `_reply` консоли и план-роутера возвращают `bool`; `show_now` переведён на общий `_reply`; красный — только настоящие сбои чтения |
| **D-20** | `create_draft`/`set_field` проверяли `if plan is None:`, а отказ контракта возвращает `{}` → при непрходимом значении бот сохранил бы **пустой план** и показал бы его дашборду | `if not plan:` (×2) + тест `test_refused_edit_writes_nothing`: после отказа `field_range` черновик байт-в-байт прежний |
| **D-18** | pipeline и сборка рынка были копией внутри webui-слоя; при `identity_ok: False` — `KeyError`/500 | вынос в `trading_math/plan_validation.py` + `market_state.py`; `identity_mismatch` как violation; тесты `tests/test_plan_validation.py` |
| **D-19** | два теста падали по wall-clock-арифметике, а не по поведению: import-time фикстура `LIVE` состаривалась в 117-секундном прогоне; MC-04 читал строку статуса раньше XHR | `_live()` на каждый тест (`test_now_card.py`, `test_screens.py`); `wait_for_function(...\d+\s*свечей...)` в `test_v3_market_browser.py`; после правок 5/5 прогонов зелёные |
| — | `ui.build_plan_refused_card(reason, msg, violations)` вызывался позиционно в **keyword-only** параметр → `TypeError` на каждом пути отказа (3 места) | `violations=` ×3 |
| — | `title_prefix` карточек содержал вложенный `<b>` внутри уже размеченного заголовка | обычные префиксы + `ui.esc(field)`; в `presenters.py` — один уровень экранирования, default `📋 План`, опечатка «гейды» → «гейты» |
| — | мёртвые `default_values_shown()` / `worst_case()` и ставшие лишними импорты `PLAN_DEFAULTS`, `estimate_worst_case_risk` в сервисе | удалены, docstrings приведены к фактической политике записи |

## 8. Политика записи (почему «не записано» и «записано, но нельзя утверждать» — это разные случаи)

Граница проведена по **разбору**, ровно как в форме дашборда:

* `field_range` / `field_enum` / `unknown_field` / `field_required` →
  `ContractRefused`, запись не тронута;
* разобранное, но смысловое (`loss_hierarchy`, `live_window_past`,
  `missing_intervals`, `stale_quote`) → сохраняется и блокирует утверждение.

Утверждение — три шага защиты: 12-символьный токен хэша из показанной карточки →
полный перепрогон pipeline в момент нажатия (`can_approve`) →
`store.approve_plan` с `PlanImmutable` как защитой от двойного нажатия.
Длина callback-data ≤ 64 байта (`plan:appr:<uuid>:<hash12>` = 59,
`plan:confirm:…` = 62). AI-предложения (P3d) и старт дня в этот срез не входят.

## 9. Что НЕ менялось

* форма и read-модели дашборда, `/workspace`, SSE, Lightweight Charts —
  только импорт вынесенного кода; WebUI развивается инкрементально;
* `chatbot/handlers/plans.py`, `collector/alerts/detector.py`,
  `chatbot/{loader,context,ui}/*` — legacy-зоны не трогались;
* `.env`, `.env.wored`, `.env.example`, `docker-compose.yml` — **не
  редактировались** (см. §10);
* миграций БД в P3c нет; схема та же, что после P3b;
* права: мутации по-прежнему только `owner` (`MUTATING_ROLES`), deny-by-default
  middleware не изменялся.

## 10. Сознательное отклонение от PLAN

В PLAN значилось «задокументировать `BOT_WIZARD_TTL_HOURS` в env-реестре».
Фактически BOT-ключи живут не в `.env.example`, а в `.env.wored` (+
`docs/TELEGRAM-BOT-OPERATIONS.md` §3). Правка env-файла — это mutating-шаг и
лишний `up -d`, при том что отсутствие ключа даёт ровно то же значение 24 ч
(`wizard_ttl_hours()` fail-safe на мусор и на ≤ 0). Решение: **ключ
задокументирован в OPERATIONS §3 и COMMAND-MAP §6, файлы окружения не
тронулись**. Проверка живого значения добавлена в runbook.

## 11. Риски и открытое

1. **Telegram-текст карточки снят живьём (13.10, §6.1) — exit-гейт P3 закрыт.**
   Два дефекта, найденные тем же прогоном (оба вне диффа P3c), **исправлены в
   срезе P3c.2** — `docs/CURATOR-REPORT-P3C-2-2026-10-13.md`; там же корневые
   причины, проба матчинга в живом 3.22-контейнере, гейт-числа и тесты.
   Кратко, как это выглядело на момент прогона: LIVE-1 — `/plans,` не матчился
   `Command("plans")` (запятая прилипает к токену), сообщение доходило до
   `handlers/chat.py::cmd_chat` (`F.text`, последний роутер) → intent
   `trade_plan` → `premium` нагенерил вход/СТО/тейк (карточка без `plan_hash`,
   violations и контракта); LIVE-2 — `edit_text` с идентичным содержимым
   (идемпотентное повторное нажатие Сессия/Позиции) ловился `except Exception`
   в `now.py::_render` и рисовал ложный «сервис данных недоступен». Литеральный
   `/plans show <id>` (незаменённый плейсхолдер) дефектом не является: пробой по
   живому хранилищу → `plan_not_found`, утечки существования чужого плана нет.
2. **Видимость чата-черновика дашборду требует того же идентичности.**
   Leg 2 подтвердил чтение под uuid5 от Telegram-id владельца (`5f44d066…`);
   password-логин `admin` — это `uuid5("wored:owner:admin")` = `4f4d18fb…`,
   другой человек. В дашборде черновик из чата увидит сессия, несущая тот же
   Telegram-аккаунт (Mini App / telegram-auth), а не admin-логин. Это следствие
   D-17 и ожидаемое поведение, но его стоит проговорить при приёмке UI.
3. **QA-черновик `12542dda…` оставлен в БД намеренно** (решение куратора
   «оставить как evidence»); через 24 ч он станет `draft_expired` и останется
   в списке как запись истории.
4. Хвосты, перенесённые из P3b: третий owner-неймспейс
   `webui/paper_store.paper_owner_key:288-300`; `db/` в `.gitignore` (миграции
   не в vcs); P2-хвосты E3 (deny со второго аккаунта, мобильный E4,
   `drop_pending_updates`/TB-06).
5. `tests/ui`-гейт требует локально поднятого fixture-сервера
   `uvicorn tests.ui.fixture_app_v3:app --port 18080`; без него браузер-тесты
   падают на `ECONNREFUSED`, а не молча скипаются — стоит считать отдельным
   инфраструктурным шумом (в P3c не чинилось).
6. Ничего не коммитится (решение куратора «Не коммитить»), baseline `0f2359a`.

## 12. Уборка

Удалены временные скрипты extraction-фазы и пробы этого среза:
`output/p3c_extract_market_state.ps1`, `output/p3c_drop_market_state.ps1`,
`output/p3c_header_market_state.txt`, `output/p3c_runtime_probe.py`,
`output/p3c_runtime_router_probe.py`, `output/p3c_leg2_chatbot.py`,
`output/p3c_leg2_webui.py`, `output/p3c_probe_status.py`,
`output/p3c_snapshot_raw.txt`, `output/p3c_probe_{webui,chatbot}.json`,
`output/qa_v3_server.{log,err}`. Внутри контейнеров остались `/tmp/probe.py`,
`/tmp/snap.json`, `/tmp/leg1.py`, `/tmp/leg2.py`, `/tmp/router_probe.py` —
не переживают рестарт контейнера, в репозиторий не попадают. Пробы среза P3c.2
(`output/p3d_container_probe.py`) удалены в том же срезе.

## 13. Следующий шаг — P3d (PLAN ещё не применён)

До P3d ничего не остаётся: живое подтверждение P3c.2 из чата **получено 13.10**
(§7 отчёта `docs/CURATOR-REPORT-P3C-2-2026-10-13.md`): `/plans,` открывает экран
планов, `/wtf` даёт честный отказ, повторное нажатие Сессия не краснеет
(`console card already up to date`). Хвосты LIVE-1/LIVE-2 закрыты.

Кандидат: AI-предложение плана (U-предложение → черновик в том же хранилище),
где бот показывает **тот же** `plan_hash`-кандидат и тот же список
неизвестного, что и дашборд, и где отказ AI-провайдера остаётся безопасным
(ТЗ: «AI failure safe»). Очередь зависимостей: P3d опирается на
`chatbot/services/simulation_plans.py` и на `ai_autonomy="propose_only"` как
значение контракта; жить она должна в `chatbot/ai/*` + новый сервис, а не в
хендлере. Альтернатива — закрыть P2-хвосты live-приёмки (E3/E4, TB-06) перед
дальним ростом write-поверхности.

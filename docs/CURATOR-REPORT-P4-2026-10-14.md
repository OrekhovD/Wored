# P4 отчёт — ручная paper-сделка и позиции в чате (функция ТЗ U07)

Дата: 2026-10-14 (хост и контейнеры: 02:56 +07, что 2026-10-13T19:56Z). Фаза: P4.
Baseline: `0f2359a`, **ничего не закоммичено**.
ТЗ: `docs/QODER-TELEGRAM-BOT-V1-TZ-20260928.md`, §5 U07, TB-03 (одно действие — одна реализация),
§6 (таксономия отказов называет реальное условие), гейт фазы: «ручная сделка из чата
проходит тот же доменный тикет-гейт, что и WebUI, и подтверждение идемпотентно».

Итог: **гейт фазы пройден**. Единая реализация тикета доказана не только тестами, но и
живым прогоном: карточка чата и карточка WebUI на одинаковых входах совпали побайтово
по 25 из 26 полей (26-е — TTL-метка), вердикт `IDENTICAL` (§5).

## 1. Решения куратора, которые определяли объём

| Решение | Что сделано |
|---|---|
| «Делегировать доменному гейту» | `paper_trading/ticket.py::build_ticket` — единственная реализация расчёта и проверки; 12 локальных проверок и локальная формула ликвидации из `_preview` удалены, а не «оставлены на всякий» |
| «Гейты в `paper_trading.service`» | `submit_manual_order`/`close_position` проверяют тип счёта, владельца и день до записи команды; `IdempotencyConflict` из репозитория больше не глотается |
| «Объём чата = сделка + статус + позиции, без закрытия» | закрытия из чата нет; экран даже не импортирует `close_position` — закреплено AST-тестом; долг вынесен в RFC Q-24 |
| «Q-20 = явный отказ с подсказкой» | голое сообщение-анкета отвечает шаблоном команды, LLM не вызывается вовсе (шпион на `ai.router.call_raw` подтверждает ноль вызовов) |
| «Коммит только после P4» | рабочее дерево изменено, индекс чистый от коммитов; baseline не двигался |
| «Гейт на изолированном QA» | полный след на `postgres-qa` (`docker-compose.qa.yml`, internal-сеть, tmpfs), живой контур только перезапущен и читался |

## 2. Что изменено в коде (runtime path, не legacy)

| Файл | Характер |
|---|---|
| `paper_trading/ticket.py` | **новый**, 545 строк: `TicketRequest`, `TicketResult`, `build_ticket`, `TicketDenied`, `DENIAL_TEXTS`, `TICKET_TTL_SECONDS = 30.0`, `risk_settings_from_day` (лимиты из снапшота дня + явные заметности о дефолтах) |
| `paper_trading/service.py` | гейты исполнения: `no_manual_account`, `account_not_owned`, `day_mismatch`; `position_not_found` на чужой позиции (не различает «нет» и «не твоё»); `idempotency_conflict` + `command_id` вместо проглатывания конфликта |
| `webui/paper_api.py` | `_preview` стал presenter-ом: `_domain_ticket` → `build_ticket`, `TicketDenied` → HTTP; `_preview_prototype` (ветка без домена) не тронута и не расширена |
| `chatbot/services/paper_orders.py` | **новый**, 756 строк: парсер `ключ=значение`, `preview`/`confirm`/`command_status`/`positions`, in-memory стор тикетов с TTL, детерминированный `tg-<owner>-<ticket_id>` |
| `chatbot/handlers/paper_deal.py` | **новый**, 357 строк: `/deal`, `/deal preview│status│positions│help`, голые аргументы, callbacks `deal:*`, кнопка подтверждения только при праве писать |
| `chatbot/handlers/chat.py` | `_bare_payload_refusal` — ветка Q-20 до AI-роутинга |
| `chatbot/application/presenters.py` | `build_deal_ticket_card` (переиспользует `build_plan_refused_card` для отказов, новой карточки не введено) |
| `chatbot/main.py` | регистрация роутера `paper_deal` (callbacks среза живут в самом роутере) |
| `chatbot/security/principal.py` | **без изменений** — срез переиспользует `resolve`/`MUTATING_ROLES`, новых прав не вводилось |
| `tests/paper_trading/test_ticket_gate.py` | **новый**, 23 теста |
| `tests/paper_trading/test_service_guards.py` | **новый**, 20 тестов |
| `tests/telegram/test_paper_deal.py` | **новый**, 33 теста |
| `tests/telegram/test_questionnaire_routing.py` | **новый**, 10 тестов |
| `tests/ui/test_ticket.py` | пересчёт golden-чисел под доменную математику (шаг объёма, изолированная маржа), а не подгонка под фактический вывод |

Схемы БД, миграции, `docker-compose.yml`, `.env*` — **не тронуты** (см. `docs/ENV-REGISTRY.md`, раздел «Telegram Manual Deal (P4 — no new keys)»). Legacy-зоны не тронуты.

## 3. Гейты на хосте

```
py -m pytest tests/paper_trading/test_ticket_gate.py tests/paper_trading/test_service_guards.py \
    tests/telegram/test_paper_deal.py tests/telegram/test_questionnaire_routing.py -q
→ 86 passed in 15.40s                       (весь P4-срез)

py -m pytest tests/ui/test_ticket.py tests/test_paper_api_domain.py tests/test_paper_api.py -q
→ 37 passed in 3.70s                        (контракт WebUI не сломан)

py -m pytest tests/telegram -q
→ 233 passed                                (после правки текстов карточки)

py -m pytest tests -q --ignore=tests/test_trading_math.py
→ 1514 passed, 61 skipped, 1 xfailed, 1 failed, 27 errors in 444.80s
```

Два последних числа требуют пояснения, а не «зелёного» пересказа:

* `tests/test_trading_math.py` не собирается на хосте — `ModuleNotFoundError: hypothesis`
  (пакет есть только в QA-образе). Это ограничение окружения, поэтому весь набор
  пересобран и прогнан в контейнере (§4) — там этот модуль собирается и проходит;
* 27 errors + 1 failed (`tests/paper_trading/test_promotion.py::test_promote_activates_through_gate_on_db`)
  — тесты, которым нужен Postgres: на хосте DSN-resolve не работает
  (`socket.gaierror`). В QA-контейнере с `WORED_TEST_DATABASE_URL` ровно эти тесты зелёные.

```
py -m ruff check <15 файлов: 7 исходников, 4 теста, 4 пробника>
→ 3 findings: F841 в paper_trading/service.py (closed, open_pos, realized_net)
```

Все три **предсуществующие**: `git show HEAD:paper_trading/service.py | py -m ruff check
--stdin-filename paper_trading/service.py -` даёт ровно те же 3. Ни одного нового замечания.

## 4. Гейт фазы: изолированный QA-контур

```
docker compose -p woredqa -f docker-compose.qa.yml up -d postgres-qa
docker compose -p woredqa -f docker-compose.qa.yml run --build --rm checks \
    python -m pytest tests -q -p no:warnings
→ 1576 passed, 16 skipped, 1 xfailed in 70.86s      (python 3.11.16, hypothesis есть, БД живая)
```

Это единственный полный зелёный прогон всего репозитория: он закрывает и хостовые
исключения из §3 (DB-тесты, hypothesis), и новые файлы.

### 4.1 Полный след ручной сделки (`output/p4_qa_trace.py` → `output/p4-qa-trace.log`)

Запуск в контейнере `checks`, схема `p4qa_ceb071242870` (изолированная, удалена в конце прогона),
владелец-сид `p4-qa-*`, `python 3.11.16`, `elapsed 0.27 s`, **`P4 QA TRACE: PASSED`,
`failed: []`**.

| Проверка | Значение в логе |
|---|---|
| тикет досчитан доменом | `quantity 10`, `notional 1000`, `reserved_margin 100`, `entry_fee 0.5`, `net_rr 1.899` |
| объём округлён вниз до шага | `1E+1 vs 10.000` (равенство по значению, не по строке) |
| ликвидация — доменная, не упрощённая | `90302.847974`, `liquidation_quality = isolated_tier_maintenance`, ниже стопа для long |
| TTL | `expires_at = now + 30 s` |
| лимиты из снапшота | `settings_notes = []` (снапшот полный — заметностей нет) |
| цепочка | `commands 3 → orders 1 → fills 1 → positions 1 → postings 3`, из них `fill_postings 2` |
| состояние и позиция | `order_state filled`, `position_status open` на ручном счёте |
| объём в ленте = объёму тикета | `10.00000000 vs 1E+1` |
| баланс = Σ проводок | `899.47990000 vs 899.47990000` (`deposit 1000 − margin 100.02 − fee 0.5001`) |
| повторный confirm тем же ключом | тот же `command_id 1514f450-65f0-4cbc-9bd6-a9317bc3f77f`, **fill остался один** |
| другой payload под тем же ключом | `ok=false`, `error=idempotency_conflict`, существующий `command_id` отдаётся обратно, строк команды под ключом 1, статус строки `conflict` (MC-15), новых fill'ов 0 |
| чужой владелец читает чужую команду | `command_not_found` (не «нет прав», чтобы не раскрывать существование) |
| авто-счёт ручной заявкой | `no_manual_account` |

Два наблюдения, вынесенные в лог как `observation`, а не как «зелёное»:

* **оценка ≠ исполнение** (по построению): тикет обещал вход `100000`, исполнение `100020` —
  `market.apply_slippage(DEFAULT_SLIPPAGE_BPS)`, неблагоприятно покупателю; маржа и комиссия
  пересчитаны по исполненному notional (`100.02` / `0.5001`). Карточка и не должна быть копией
  сделки: она оценка с указанием `price_as_of`;
* **`fills.fee_rate` хранит `0.0006` (DDL-default), а списано по `0.0005`** — то есть поле
  строки fill'а не описывает то, что ушло в проводки. Это дефект данных, а не P4:
  запись делает runner. Долг вынесен в §8 — исправлять нужно в схеме/раннере, а не подгонкой
  отчёта.

### 4.2 Таксономия отказов (ТЗ §6) — что реально отвечает пользователю

| Код | Чат | WebUI HTTP |
|---|---|---|
| `day_not_active` / `no_active_day` | «день не активен — запустите в WebUI», позиция в песочнице не создаётся | 409 |
| `not_manual_account` | «ручная заявка доступна только на ручном счёте» | 403 |
| `account_not_found` | «счёт не найден» | 404 |
| всё остальное: `risk_gate_denied` с кодами `check_order_risk` в `reasons` (`stale_snapshot`, `leverage_out_of_range`, `insufficient_margin`, `risk_per_order_exceeded`, `max_open_risk_exceeded`, `daily_drawdown_exceeded`, `net_rr_below_minimum`, `stop_after_liquidation`, `spread_too_wide`, …) | «Заявка отклонена риск-гейтом: <коды>» — карточка называет реальное условие, а не «не удалось» | 422 |
| `ticket_expired` | «тикет просрочен», а не «не найден» | — |
| `ticket_changed` | день/аккаунт/свежесть ушли — пересборка до записи команды | — |
| `idempotency_conflict` | существующий `command_id` в ответе | 422 + тело |
| `RESULT_UNKNOWN` | «не подтверждено» + обязательный `command_id` | — |
| домен недоступен vs дня нет | два разных текста, не один «не удалось» | разные статусы |

Тексты — одна таблица (`paper_trading.ticket.DENIAL_TEXTS`): один код не может означать
разное в двух консолях — WebUI берёт detail из неё, чат из неё же.

## 5. Живой контур: только перезапуск и чтение

```
docker compose restart chatbot webui
→ htx_trading_bot_chatbot Up (healthy) · htx_trading_bot_webui Up (healthy)
→ лог старта: SURFACE: command token rule installed before all routers
              Chatbot started polling            (роутер paper_deal зарегистрирован)
```

Живой день и живые позиции **не открывались и не закрывались**; `confirm` и
`submit_manual_order` на боевом контуре не вызывались ни разу. Оба пробника оканчиваются
строкой `"writes": "none"`.

### 5.1 Дефект, который поймал только живой рестарт

Контейнер чата собран на `python:3.9-slim` (`chatbot/Dockerfile:2`), тогда как WebUI — 3.11,
QA-образ — 3.11, хост — 3.14. Первая сборка P4 упала в crash-loop именно на 3.9:

```
main.py → handlers/menu.py:13 → handlers/analytics.py:16 → handlers/chat.py:96
TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'
```

PEP 604-объединение `str | None` в сигнатуре, вычисляемой при импорте, без
`from __future__ import annotations`. Ни хостовый pytest, ни ruff, ни QA-контейнер его
не видели: там 3.10+, где `X | Y` — штатное выражение, а ошибка именно на 3.9, где
оператор `|` над типами не определён. Исправление — `typing.Optional[str]`
в `chatbot/handlers/chat.py`; «массовый» future-import по всем модулям не выбран,
потому что aiogram вычисляет аннотации хендлеров в рантайме (DI) и должен видеть
реальные классы, а не строки.

Класс закрыт аудитом, а не единичной правкой:

```
docker exec htx_trading_bot_chatbot python /tmp/p4_py39_audit.py /app /opt
→ PEP 604 аннотаций вне future-import: 0
```

Сам аудит проверен на находке: он честно помечает заведомо «плохой» файл
(`output/p3d_probe_dash.py:51`), то есть ноль — это отсутствие находок, а не сломанный сканер.

Дополнение того же дня: у вывода не было счётчика просканированных модулей, и «0»
был неотличим от «сканировали пустоту» (в контейнере чата пути `/app` и `/opt/<pkg>`,
опечатка в корне даёт тихий ноль). `output/p4_py39_audit.py` теперь печатает число
модулей; перезапуск с явными корнями: `output/p4-py39-audit.log` —
**модулей просканировано 129, нарушений 0**. Процедура прогона закреплена в
`docs/OPERATIONAL-PROCESS.md` §1.

### 5.2 Живой `/deal preview` (`output/p4_live_preview.py` → `output/p4-live-preview.log`)

Владелец `5f44d066-…` (uuid5 из telegram-id, значения id нигде не печатаются),
день `50f7d585-f404-4dfb-b069-40eda206d90c` (`running`), ручной счёт
`4c2530aa-849f-495e-8bc6-0b0cfcbe12ff`, BTC-USDT bid/ask `83274.2 / 83274.3`,
качество `live`, `quantity_step 0.001`:

```
объём 12.007 · вход 83274.3 · плечо 10x · RR после комиссий 1.898767
стоимость позиции 999.87452 · маржа 99.987452 · комиссии 0.499937 + 0.509935
безубыток 83274.383274 · ликвидация 75199.064531 (isolated_default_maintenance)
при стопе -10.994304 · при цели 18.986585 · финансирование known · -0.000009
тикет действует 30 с
```

Карточка честно называет природу чисел: `⚠️ лимиты дня взяты из дефолта RiskSettings,
не из снапшота дня: min_net_rr` и отдельной строкой
`⚠️ тира риска в снапшоте дня нет — ставка поддержки для ликвидации взята из доменного дефолта`.
Две разные заметности не смешиваются в одну строку (иначе пользователь решит, что дефолт
подставлен в плечо); закреплено тестом
`test_defaulted_limits_and_absent_tier_are_named_separately`.

Длина callback-данных в живом прогоне: `deal:confirm:<8hex>` = 21 байт,
`deal:rebuild:<8hex>` = 21, `deal:positions` = 14, `nav:now` = 7 — при лимите 64;
финансовых значений в callback нет, только усечённый id.

Попутно исправлены два текста, видимых пользователю (найдены именно живым рендером,
тестами не ловились): вложенный `<b>` в заголовке (`Card.render()` уже оборачивает
заголовок) и «нотионал» → «стоимость позиции».

### 5.3 Сверка чисел чата и WebUI (`output/p4_ticket_parity.py` → `output/p4-ticket-parity.log`)

Вердикт: **`IDENTICAL`** — `compared_keys: 26`, `differing: []`, `only_in_chat: []`,
`only_in_webui: []`, исключено только `expires_at` (`excluded_time_dependent`),
`identity.matched: true` (`paper_api._pt_owner_id("admin", telegram_user_id=…) ==
paper_orders.owner_id_for(…)`).

`chat_source_at == webui_source_at == 2026-10-13T20:04:47.143000+00:00` — обе стороны
прочитали один и тот же тикер, поэтому сравнение чисел осмысленно; совпало всё, включая
`quantity 12.006`, `notional 999.949725`, `liquidation_price 75210.984507`,
`net_rr 1.898883` и `settings_notes`.

Почему один процесс и почему presenter, а не HTTP — это не вопрос удобства, а вопрос
dостоверности:

* живой стакан перезаписывается каждые ~4.5 с, а `PAPER_MARKET_MAX_AGE_SECONDS = 5`;
  сравнение из двух контейнеров ловило разные `source_at` и давало `MARKET_DRIFT`
  (первая версия пробы именно так и умерла); в одном процессе оба чтения подряд,
  попытка повторяется до совпадения (совпало с 1-й);
* парольная сессия WebUI выводит **другого** владельца: в env WebUI нет
  `TELEGRAM_ADMIN_IDS`/`BOT_OWNER_IDS`, а живой день принадлежит uuid из telegram-id
  (оператор входит через Telegram-виджет). Значит HTTP-путь `POST
  /api/paper/accounts/{id}/orders/preview` с паролем показал бы `404/403` не из-за бага,
  а из-за другого владельца — сверка через presenter `_preview` (тот же код, что стоит за
  роутом) единственная честная форма этого гейта.

## 6. Отклонения от плана (фиксирую, а не «сделано как в плане»)

1. **`liquidation_quality`**. План просил одно значение `"domain"`. Реализация даёт два:
   `isolated_tier_maintenance` (ставка поддержки взята из тира биржи) и
   `isolated_default_maintenance` (тира нет — ставка доменная). Это строго информативнее
   прежнего `"simplified"` и не ломает контракт (набор ключей ответа совпадает —
   `ui_ticket_keys`, 26 ключей, проверка в `test_ticket_gate.py`), но поле не то же самое.
2. **Заметности о дефолтах лимитов**. План просил `settings_defaulted:<key>` в `reasons`.
   Реализация возвращает голое имя ключа снапшота (`min_net_rr`, `max_leverage`, …) в
   отдельном поле `settings_notes` плюс маркер `risk_tier_absent`. Гарантия «не подставлять
   молча» соблюдена, форма — другая; зафиксировано в `docs/ENV-REGISTRY.md`.
3. **Ветка прототипа WebUI** (`_preview_prototype`) сохранила упрощённую математику и
   `liquidation_quality: "simplified"` — по плану она не трогается; в чате она недоступна
   по построению (чат не знает про «домен недоступен», он получает `no_active_day`).
4. **Метод живого гейта** вместо HTTP-сверки — см. §5.3 (у парольной сессии другой
   владелец, поэтому HTTP-путь показал бы отказ не из-за бага).
5. **Хост не может дать полный зелёный прогон** (нет `hypothesis`, нет доступного Postgres);
   полный прогон перенесён в QA-образ (§4). Числа хоста приведены с разбором, а не
   как «почти зелёный прогон».

## 7. Чем закреплён каждый инвариант среза

| Инвариант | Тест |
|---|---|
| числа считает домен, чат не имеет своей формулы | `test_ticket_gate.py` (23) + живая сверка §5.3 |
| объём округляется вниз до `quantity_step` | `test_ticket_gate.py`, QA-строка `1E+1 vs 10.000` |
| RR/маржа/плечо/свежесть — veto домена | `test_ticket_gate.py` |
| `settings_defaulted`-честность (дефолт назван) | `test_ticket_gate.py` + `test_defaulted_limits_and_absent_tier_are_named_separately` |
| набор ключей ответа = прежний UI-контракт | `test_ticket_gate.py`, `tests/ui/test_ticket.py` (22) |
| гейты `no_manual_account`/`account_not_owned`/`day_mismatch` | `test_service_guards.py` (20) |
| `position_not_found` на чужой позиции | `test_service_guards.py` + QA `ownership_read` |
| конфликт ключа отдаёт `command_id`, строка помечена `conflict` | `test_service_guards.py` + QA `conflict` |
| TTL 30 с и просрочка ≠ «не найден» | `test_paper_deal.py` (33) |
| confirm перечитывает состояние и пересобирает тикет | `test_paper_deal.py` |
| `RESULT_UNKNOWN` называет `command_id` | `test_paper_deal.py` |
| кнопки подтверждения нет у того, кто не может писать | `test_paper_deal.py` |
| закрытия позиции из чата нет | `test_the_screen_never_offers_to_close_a_position` (AST) |
| голая анкета не уходит в LLM | `test_questionnaire_routing.py` (10), шпион на `ai.router.call_raw` |

## 8. Открытое и не сделанное намеренно (долги)

* **Q-24 (RFC, ОТКРЫТО)**: `pipeline_pause`/`pipeline_close_all` в чате ходят в legacy
  `trading_sessions`, а не в домен; закрытие/частичное закрытие позиции из чата отсутствует.
  У `pause_auto`/`resume_auto` нет owner-гейта по аккаунту.
* **Q-25 (RFC, ОТКРЫТО)**: `risk_policy` настроек UI и `settings_snapshot` дня.
  P4 сделал источник одним (снапшот дня), но исторический дрейф значений остаётся —
  день, открытый со старыми лимитами, живёт по ним до конца дня.
* **`complete_command` обнуляет `result`** (`runner._settle_command` зовёт
  `repo.complete_command(cmd.command_id)` без результата, repository пишет `result=NULL`).
  Поэтому из `paper_v2_commands` нельзя достать `position_id`: экран статуса честно
  показывает позицию только если она прочитана, иначе — «дальше `/deal positions`».
  Исправление относится к runner-контракту, не к чату.
* **`fills.fee_rate` = `0.0006` (DDL-default) при списанном `0.0005`** (§4.1): поле строки
  не описывает фактическую проводку.
* **Ротация `WEBUI_ADMIN_PASSWORD`** — действие куратора, вне кода; перенесено из
  `docs/CURATOR-REPORT-P3D-2026-10-13.md` §0 и до сих пор не выполнено. Чек-лист с
  расположением значения (`.env`, `.env.wored`, recreate вместо restart) закреплён в
  `docs/OPERATIONAL-PROCESS.md` §7.
* ~~Окружение хоста~~ → **закрыто процедурно 2026-10-14**: `hypothesis` и доступного
  Postgres на хосте нет, поэтому полный зелёный гейт = QA-образ с `--build`, а хост даёт
  только быстрые офлайн-наборы. Закреплено в `docs/OPERATIONAL-PROCESS.md` §1 (матрица
  «что доказывает какой контур»), §2 (хост-наборы и обоснованный список 28 отказов
  окружения), §6 (правила 6 и 7). Числа этого отчёта (§3/§4) теперь совпадают с процедурой.

## 9. Команды повторной проверки

```
# хост (без DB и без hypothesis — см. §3)
py -m pytest tests/paper_trading/test_ticket_gate.py tests/paper_trading/test_service_guards.py \
    tests/telegram/test_paper_deal.py tests/telegram/test_questionnaire_routing.py -q   # 86 passed
py -m pytest tests/ui/test_ticket.py tests/test_paper_api_domain.py tests/test_paper_api.py -q  # 37 passed

# полный гейт (образ собирается из текущего дерева)
docker compose -p woredqa -f docker-compose.qa.yml up -d postgres-qa
docker compose -p woredqa -f docker-compose.qa.yml run --build --rm checks \
    python -m pytest tests -q -p no:warnings                                             # 1576 passed

# QA-след ручной сделки (workdir контейнера — /repo)
docker compose -p woredqa -f docker-compose.qa.yml run --build --rm checks \
    python output/p4_qa_trace.py
# (в живом контуре не запускать: тестовые владельцы изолированы сидом p4-qa-*)

# живой read-only preview (python 3.9 внутри chatbot)
docker cp output/p4_live_preview.py htx_trading_bot_chatbot:/tmp/p4_live_preview.py
docker exec htx_trading_bot_chatbot sh -c "python /tmp/p4_live_preview.py > /tmp/p4-live-preview.log 2>&1"
docker cp htx_trading_bot_chatbot:/tmp/p4-live-preview.log output/p4-live-preview.log

# побайтовая сверка чата и WebUI (значение telegram-id передаётся в env, не печатается)
docker cp output/p4_ticket_parity.py htx_trading_bot_webui:/tmp/p4_ticket_parity.py
docker exec -e P4_PROBE_TELEGRAM_ID=<id> htx_trading_bot_webui \
    sh -c "python /tmp/p4_ticket_parity.py > /tmp/p4-ticket-parity.log 2>&1"
docker cp htx_trading_bot_webui:/tmp/p4-ticket-parity.log output/p4-ticket-parity.log
```

## 10. Уборка

Удалены устаревшие артефакты межконтейнерного подхода: `output/p4_webui_ticket.py`,
`output/p4-live-handoff.json`, `output/p4-webui-ticket.log`, временный
`output/_audit_selfcheck.py`; копии пробников и логов из `/tmp` живых контейнеров
`chatbot` и `webui` удалены (в обоих `ls /tmp | grep -c p4` = 0). QA-схема
`p4qa_ceb071242870` удалена самой пробой (в логе: `schema_dropped`).

Остались как доказательство: `output/p4_qa_trace.py`, `output/p4_live_preview.py`,
`output/p4_ticket_parity.py`, `output/p4_py39_audit.py` и логи
`output/p4-qa-trace.log`, `output/p4-live-preview.log`, `output/p4-ticket-parity.log`
(UTF-8 байт-в-байт из контейнеров, без PowerShell-редактирования).

Документы среза: `docs/TELEGRAM-BOT-RFC.md` (U07 → «ЗАКРЫТО В ЧАТЕ (P4, 2026-10-14)»,
Q-20 решён, Q-21…Q-23 закрыты, Q-24/Q-25 — открытые долги),
`docs/TELEGRAM-BOT-COMMAND-MAP.md` (§2 строка `/deal`, §2.3 — подкоманды, callbacks,
девять инвариантов), `docs/ENV-REGISTRY.md` («no new keys», состояние
`PAPER_ENGINE_ENABLED=true` в бою).

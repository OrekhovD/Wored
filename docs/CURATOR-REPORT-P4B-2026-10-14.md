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
24 теста этого среза, 13 из них — на живой QA-базе.

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

## 6. Живой контур: только чтение, и что это значит

```
docker compose ps → chatbot, collector, postgres, redis, webui — Up
```

Сервисы запущены на **образе до P4b**: `/day` в бою ещё нет. Применение требует явной
команды куратора (`docker compose up -d --build chatbot webui`) — сам срез её не делал,
`down`/`restart` на живых сервисах не выполнялись.

Живой read-only пробник лимитов (`output/p4b_live_limits.sql` + `output/p4b_live_limits.py`
через `docker compose exec -T postgres … psql -At` → `output/p4b-live-limits.log`), два
`running`-дня:

```
max_risk_per_order_usdt = '10'   max_daily_loss_usdt = '50'
max_leverage = '10'              max_open_risk_usdt = '20'
min_net_rr = None                settings_notes = ['min_net_rr']
исполнителем применяются: max_risk_per_order=10 max_daily_loss=50 max_leverage=10
                          max_open_risk=20 min_net_rr=1.0
```

Карточка `/day status` на боевом дне покажет отсутствующий лимит по имени, а не
подставит ноль или строку «None». Ни одна строка БД не изменена, команды не создавались.

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
* **Срез не развёрнут в живом контуре** (§6): нужен явный `docker compose up -d --build
  chatbot webui` от куратора.
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
  `output/p4b_live_limits.{py,sql}`.

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
```

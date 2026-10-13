# P2 отчёт — WORED Telegram Bot V1 (read vertical slice)

Дата: 2026-10-08 · Commit baseline: `0f2359a` (праки этой фазы не коммитились —
решение куратора «Не коммитить») · UTC на момент прогонов: 2026-10-08 13:5x
· Среда evidence: Windows host, Python 3.14.3, aiogram 3.27.0, pytest
(asyncio_mode=auto). Runtime-статус Docker-сервисов в этой фазе НЕ проверялся и
не заявляется: изменение только в коде, restart не выполнялся.

Реализуемое задание: `docs/QODER-TELEGRAM-BOT-V1-TZ-20260928.md`.
Решение куратора для этой фазы: **«Сразу P2: read-vertical slice с кодом»**,
**«Только V3 + paper_v2»**, **«Заморозить монитор, данные read-only»**.
Выходной gate ТЗ §8 (P2): «Реальный source→backend→Telegram mock; old commands
redirected; permission negative tests».

---

## 1. Цель

Один вертикальный read-срез Telegram-консоли: deny-by-default права, `/start` и
`/now` с одной карточкой рынка/прогноза/сессии из общих с WebUI источников,
главная навигация и редирект старых команд на те же use case. Никаких
мутирующих сценариев, никаких новых таблиц, никаких пересчётов финансов на
стороне бота.

## 2. Допущения и неизвестное

* Реальных Telegram-смoke не было: отдельного test-бота/токена нет, а слать
  тестовые сообщения рабочему владельцу ТЗ запрещает → все статусы уровня E1
  (изолированные тесты), а не live acceptance.
* Identity: `owner_id = uuid5(NAMESPACE_URL, "wored:owner:{telegram_user_id}")`
  — то же выражение, что в `paper_trading.adapter.owner_id_from_telegram`;
  предполагается, что других носителей owner_id в домене нет.
* Calibrated range прогноза в общем read-model отсутствует → в карточке
  сознательно `N/A`, а не псевдо-диапазон из confidence (ТЗ U03).
* Неизвестное, оставшееся открытым: дedup `update_id` после рестарта (таблицы
  `bot_*` не введены), поведение на реальных ограничениях Telegram payload,
  поведение в группах при `BOT_ALLOW_GROUPS=true` (по умолчанию `false`).

## 3. Архитектурное решение

```
Telegram update
  └─ security.guards (outer middleware, установлен ДО роутеров)
       ├─ principal.resolve(from_user.id)  → allowlist / роль / timezone
       ├─ private-chat only, throttle refresh
       └─ deny → служебный отказ без деталей + аудит без контента
  └─ handlers.now (console)      ← новые команды, первый роутер
  └─ handlers.deprecated         ← старые имена → та же функция-экран
  └─ legacy routers              ← Command-привязки снятых команд удалены
        └─ integrations.wored_api (read-only фасад)
              ├─ Redis market:perpetual:htx:<contract>  (paper_trading.market)
              ├─ storage.postgres_client.get_latest_prediction_request
              └─ paper_trading.adapter.get_current_state / list_day_reports
                    └─ application.presenters (карточка, escaping, error taxonomy)
```

Ключевые решения:

1. **Один источник истины.** Бот читает те же Redis/postgres/domain-объекты,
   что WebUI, и проходит через доменные валидаторы (`snapshot_from_dict`,
   `is_fresh`). WebUI не изменялся ни одной строкой.
2. **Отсутствие в числе.** `None` рендерится как `N/A`; stale показывает
   последнее подтверждённое значение + возраст + причину; ошибка транспорта —
   `quality=error`, а не пустой/нулевой рынок.
3. **Один владелец команды.** Порядок роутеров зафиксирован тестом по исходнику
   `main.py`; дубликаты `Command(...)` в legacy-модулях разведены.
4. **Q-01 в коде.** Silent fallthrough на `services.session_manager` удалён;
   идемпотентная ветка «день уже идёт» рендерится из domain state; при
   недоступности домена — `BACKEND_UNAVAILABLE` и ноль записей.
5. **Права до логики.** Guard ставится раньше роутеров и читает env fail-closed.

## 4. Дерево файлов и критичные диффы

Новое (1 688 строк кода + 1 438 строк тестов):

```
chatbot/security/__init__.py                6
chatbot/security/principal.py             155
chatbot/security/guards.py                136
chatbot/application/__init__.py             6
chatbot/application/presenters.py         427
chatbot/integrations/wored_api.py         476
chatbot/handlers/now.py                   386
chatbot/handlers/deprecated.py             96
tests/telegram/conftest.py                307
tests/telegram/test_permissions.py        192
tests/telegram/test_now_card.py           240
tests/telegram/test_command_map.py        215
tests/telegram/test_navigation.py         156
tests/telegram/test_session_domain_only.py 148
tests/telegram/test_screens.py            180
```

Изменённые активные файлы (tracked, `git diff --stat`):

```
chatbot/main.py                |  83 +++--   (guard + порядок роутеров + redact логов)
chatbot/handlers/pipeline.py   | 131 ++---    (Q-01: нет legacy fallthrough/рендерера)
chatbot/handlers/start.py      |  49 ++--     (только reply-клавиатура)
chatbot/handlers/menu.py       |  27 ++--     (кнопки → console экраны)
chatbot/handlers/market.py     |  14 ++--     (Command-привязка снята)
chatbot/handlers/predictions.py|  14 ++--
chatbot/handlers/portfolio.py  |  20 ++--
chatbot/handlers/plans.py      |  12 ++--
chatbot/handlers/trader.py     |   5 +-
chatbot/handlers/callbacks.py  |  13 ++--     (menu_back → console)
chatbot/handlers/chat.py       |  11 +++-     (U9 ветка → console)
```

Критичные диффы (фрагменты):

`chatbot/handlers/now.py` — битый navigation-клавиатура экрана рынка (найден
тестом, а не ревью): `inline_keyboard` — список СТРОК, туда добавлялись голые
кнопки, aiogram отклонял весь markup и экран превращался в
`BACKEND_UNAVAILABLE`:

```diff
-    extra = [button] if button else [InlineKeyboardButton(text="⬅️ Сейчас", callback_data="nav:now")]
+    extra = [[button]] if button else [[InlineKeyboardButton(text="⬅️ Сейчас", callback_data="nav:now")]]
+    # ``inline_keyboard`` is a list of ROWS, so the extra button is wrapped in a
+    # row itself; passing bare buttons here makes aiogram reject the whole markup.
```

`chatbot/handlers/pipeline.py` — Q-01 (см. §3.4): идемпотентная ветка больше не
вызывает `_build_status_response`, а рендерит domain state; при отказе
`return ERR_DOMAIN_UNAVAILABLE, None`.

`chatbot/application/presenters.py` — error card всегда печатает machine-код
(`<code>BACKEND_UNAVAILABLE</code>`), неизвестный код не остаётся «голым».

Дефект, найденный прогоном этого слайса (не тестовая догадка): в `show_reports`
шаблон обращался к `{unit}`, которого не было в `format(...)` → KeyError в
рантайме; исправлено, заодно убран двойной вывод валюты (`USDT USDT`).

## 5. Команды запуска, миграции, rollback

Миграций нет. Env-блокировка: guard активен из bind-mount кода, но
`BOT_OWNER_IDS` (и алиасы) в `.env.wored`/`.env` отсутствуют → после restart
отказано всем. Требуется явное разрешение куратора на правку `.env.wored`
(AGENTS.md) и отдельное разрешение на `docker compose restart chatbot`.

```powershell
# проверка конфигурации (presence, без значений)
Select-String -Path .env,.env.wored -Pattern '^(BOT_OWNER_IDS|TELEGRAM_ADMIN_IDS|TELEGRAM_ADMIN_ID|BOT_ADMIN_IDS)=' | ForEach-Object { ($_.Line -split '=')[0] + '=***' }
# деплой после получения разрешения
docker compose config
docker compose up -d --build chatbot
docker compose logs chatbot --tail=120
```

Rollback: `git checkout -- chatbot` + удалить untracked каталоги
`chatbot/security`, `chatbot/application`, `chatbot/handlers/now.py`,
`chatbot/handlers/deprecated.py`, `chatbot/integrations/wored_api.py`,
`tests/telegram`; данные не менялись.

## 6. Фактически выполненные проверки (stdout)

```
PS D:\WORED> python -m pytest tests/telegram -q -p no:warnings
104 passed in 3.64s

PS D:\WORED> python -m pytest chatbot/tests -q -p no:warnings
26 passed in 7.89s

PS D:\WORED> python -m pytest tests/test_f16_telegram_contract.py tests/paper_trading/test_telegram_mock.py -q -p no:warnings
14 passed in 0.89s

PS D:\WORED> python -m ruff check chatbot/security chatbot/application chatbot/integrations/wored_api.py chatbot/handlers/now.py chatbot/handlers/deprecated.py chatbot/handlers/pipeline.py chatbot/handlers/menu.py chatbot/handlers/start.py tests/telegram
Found 3 errors.   # все — предсуществующий долг pipeline.py (Optional, liq_side, F541), проверено через git show HEAD
```

Пояснение к доказательствам: scratch-артефакты фазы (`output/p2-*`) удалены по
решению куратора 2026-10-12, поэтому stdout зафиксирован только в этом разделе и
в §6b; повторить его можно командами раздела (тесты в репозитории).

Отклонение от ТЗ §10: дословная команда
`pytest chatbot/tests tests/test_f16_telegram_contract.py tests/paper_trading/test_telegram_mock.py -q`
в одном прогоне даёт 2 collection ERROR (`No module named
'tests.test_f16_telegram_contract'`) из-за коллизии пакета `tests` (D-12);
прогоны разведены. Побочно другой дефект того же класса (`storage`, D-13)
ломал хост-тесты, импортируя `collector/storage/postgres_client.py` вместо
`chatbot/...`: тестовый harness теперь зеркалит контейнерный `PYTHONPATH` и
ассертит разрешение модуля.

Не выполнялось (осознанно): QA-контур, UI acceptance MC-V3, live Telegram
smoke (отправка сообщений в чат) — см. §6b ниже: сделана in-container
верификация реального read-пути против продакшн Redis/Postgres.

## 6b. Live-верификация в контейнере (E2), 2026-10-12 00:09–00:20 UTC+7

После решения куратора «BOT_OWNER_IDS=5249526259» и «рестарт после env»:

```powershell
# 1) allowlist добавлен в .env.wored (проверка presence, без значений)
Select-String -Path .env.wored -Pattern '^(BOT_[A-Z_]+)=' | ForEach-Object { ($_.Line -split '=')[0] + '=***' }
#   BOT_OWNER_IDS=***
# 2) пересоздание контейнера (env_file читается при СОЗДАНИИ, не при start)
docker compose up -d chatbot
docker compose exec -T chatbot python -c "import os;print(bool(os.getenv('BOT_OWNER_IDS')))"   # True
docker compose logs chatbot --since=8m | Select-String AUTHZ
#   chatbot.security - INFO - AUTHZ: deny-by-default guard installed (allowlist configured)
#   aiogram.dispatcher - INFO - Run polling for bot @W_W_O_O_bot id=8686265741
docker compose ps --format "{{.Name}} {{.Status}}"
#   chatbot Up (healthy) / collector Up / postgres Up (healthy) / redis Up / webui Up (healthy)
foreach ($p in "/","/alerts","/predictions","/journal","/command-deck") { (Invoke-WebRequest "http://localhost:8080$p" -UseBasicParsing).StatusCode }
#   200 200 200 200 200
```

Read-only probe реального read-пути внутри контейнера (`docker compose cp` +
`python /tmp/…`, ничего не отправляет и не мутирует):

```
contract: BTC-USDT | max_age: 5.0
resolve owner: ('owner', '5f44d066', True)
resolve unknown user -> None
allowlist size: 1 | key in use: BOT_OWNER_IDS
market: live BTC-USDT last= 83773.6 bid= 83773.5 ask= 83773.6 age= -0.55 fresh= True
        source= htx-public-rest live reasons= ()
forecast: live id= 231 base= 83743.4 pred= None pct= None model= None status= pending
        range= None None reasons= ()
session: live day= e126c8a2-b2ad-4ba3-a87c-2c37f96c3a38 state= running accounts= 2
        open_positions= 0 next= trade reasons= ()
  account: manual USDT cash= 1000.00000000 positions= 0
  account: auto USDT cash= 1000.00000000 positions= 0
```

Что это доказывает и что нашло:

1. P2-код работает на **Python 3.9** внутри контейнера (риск §9.4 снят) и читает
   реальные продукты: рынок `live` из того же Redis-ключа, прогноз `id=231`
   (статус `pending` → карточка обязана показать `N/A`, а не выдумывать число),
   торговый день `e126c8a2…` в состоянии running.
2. `age= -0.55` — часы фида впереди контейнера; свежесть считается с допуском,
   `fresh=True`, ложного «stale» нет.
3. **Новый дефект D-14** (найдено именно live-пробегом, зелёный хост-сьют его не
   ловил): домен возвращает сырой `DayState`, и `str()` даёт `DayState.running`
   там, где WebUI (JSON) показывает `running`. Исправлено нормализацией на
   границе (`wored_api.enum_value`, применено и в `pipeline._handle_start_safe`),
   фикстура `tests/telegram/conftest.py` теперь отдаёт настоящий enum, добавлены
   регресс-тесты `test_domain_enum_never_leaks_its_type_name` и
   утверждения в `test_session_domain_only.py`. Повторный probe: `state= running`.
4. Права: owner резолвится, неизвестный ID → `None` (отказ) на реальном
   контейнерном коде.

Не выполнено: **E3** — живой round-trip в Telegram (пользователь отправляет
`/now`), потому что отправка в чат рабочего владельца требует его действия.

## 6c. E3 live Telegram acceptance (2026-10-12 14:26, бот `@W_W_O_O_bot`)

Фактический лог владельца (реальный Telegram, не mock):

```
Rabotyaga: WORED · HTX BTC-USDT linear-swap · LIVE PAPER
🟢 Рынок: 84,073.5 USDT | mark 84,073.5 USDT | обновлено 14:26:29 (…), 1 с назад
🟢 Прогноз 4ч: +0.64% · 84,550.0 USDT · диапазон N/A · до 17:53:49 (…) · id 232 · completed
🟢 Сессия: RUNNING · id e126c8a2 · manual 1,000.00 USDT / auto 1,000.00 USDT · до 21:00:00 (…)
Риск: 0 откр. поз. · просадка N/A · лимит N/A (доменное read-model появится в P4/P5)
Источник: htx-public-rest · данные: свежие
req 284bac765c35 · 🟢 htx-public-rest

Dmitry O: /portfolio
Rabotyaga: ⚠️ Команда /portfolio устарела и вызывает тот же use case, что /positions.
Rabotyaga: 📦 Позиции
— manual (4c2530aa)
  нет открытых позиций
— auto (7cb58783)
  нет открытых позиций
итого открытых: 0 · P&L и ликвидационный порог не пересчитываются ботом — они в отчёте домена (P5)
```

Что это закрывает:

* рынок/прогноз/сессия в ОДНОЙ карточке с mode (`LIVE PAPER`), источником
  (`htx-public-rest`), возрастом (`1 с назад`) и quality-бейджами — TB-02 на
  реальном транспорте;
* прогноз показывает `диапазон N/A` вместо выдуманного коридора и печатает
  `id 232` — тот же идентификатор, что у WebUI (TB-07 часть): в этот момент
  домен уже имел завершённый прогноз (`completed`, `+0.64%`), т.е. вчерашний
  `pending`-случай тоже отработан честно;
* сессия `RUNNING` без `DayState.` — фикс D-14 подтверждён живым сообщением;
* `/portfolio` даёт редирект-строку и ТОТ ЖЕ экран позиций, что `/positions`
  (TB-03 на реальном транспорте), с одинаковыми балансами `manual/auto`.

Что это НАШЛО (D-15): подпись часов была `UTC+0 (Asia/Bangkok)` при корректных
Bangkok-часах — `strftime("%z")[:-3]` из `"+0700"` делает `"+0"`. Зелёный хост-
sьют это не ловил, потому что тест проверял наличие `UTC`, а не его истинность.
Исправлено: `presenters._offset_suffix()` считает смещение из `utcoffset()`;
тесты ужесточены (`UTC+7 (Asia/Bangkok)` обязателен, `UTC+0` запрещён) и
добавлен блок на получасовые зоны. Проверка после `restart` в контейнере:

```
format_time: ('14:26:29', 'UTC+7 (Asia/Bangkok)')
half-hour:  ('12:56:29', 'UTC+5:30 (Asia/Colombo)')
```

Не закрыто на E3: отказ неизвестному пользователю (для этой составляющей TB-01/TB-05
нужен второй аккаунт) и живой `/report` (последний пункт TB-04 — «итоги»).



## 7. Статусы TB и уровень доказательности

E1 = изолированные тесты/mock · E2 = QA-контур · E3 = live Telegram · E4 = mobile

| ID | Требование ТЗ | Статус | Доказательство |
|---|---|---|---|
| TB-01 | deny без утечки для unknown/group/foreign callback/viewer | **PASS** (E1+E2) | `test_permissions.py` ×14: unknown denied + handler не вызван, group denied, callback unknown не исполняется, viewer read-only, пустой allowlist = отказ всем, fail-closed парсинг, аудит без контента; в проде: `AUTHZ: deny-by-default guard installed (allowlist configured)` и `resolve unknown user -> None` |
| TB-02 | `/start`+`/now`: один рынок, mode/source/age/quality; no-data/stale ≠ 0/green | **PASS** (E1+E2+**E3**) | юзер-лог 2026-10-12: `LIVE PAPER` + рынок/прогноз/сессия в одной карточке, `1 с назад`, `Источник: htx-public-rest`, `диапазон N/A`; `test_now_card.py` ×23 (включая жёсткую проверку метки TZ) |
| TB-03 | старые команды → целевой use case или deprecation; `/portfolio` = `/positions` | **PASS** (E1+**E3**) | `test_command_map.py` ×13 (побайтово совпадающая карточка редиректа, один владелец команды) + живой лог: `/portfolio` → строка deprecation + тот же экран `📦 Позиции` с теми же `manual/auto` |
| TB-04 | 1–3 нажатия до цены/прогноза/позиций/итога | **PARTIAL** (E1+E3) | живой лог: цена/прогноз/сессия — 0 тапов от `/now`, позиции — 1 тап (`/portfolio`); `test_navigation.py`: 6 пунктов навигации, каждый callback → один экран; `/report` и мобильный журнал действий (E4) ещё не проверены |
| TB-05 | быстрый ACK; old/expired/replayed/foreign callback не мутирует | **PARTIAL** (E1) | ACK-before-render, foreign denied, неизвестный `callback_data` не матчится; expired/replayed требует `bot_callbacks` (P4) |
| TB-06 | dedup `update_id` после restart | **NOT RUN** | `bot_update_inbox` не вводился (вне P2) |
| TB-07 | forecast 15m/1h/4h/24h c ID/base/target/range/method | **PARTIAL** (E1) | карточка последнего прогноза: ID/model/base/target/value; `диапазон: N/A` осознанно; горизонт-сетка не в P2 |
| TB-08…TB-32 | остальное | **NOT RUN** | вне области P2 (P3…P7) |

## 8. Обновлённые документы

* `docs/TELEGRAM-BOT-COMMAND-MAP.md` — создан (требование ТЗ §10; на него
  ссылается `handlers/deprecated.py`).
* `docs/TELEGRAM-BOT-OPERATIONS.md` — создан в объёме P2: env-реестр среза,
  диагностика, инцидент «отказано всем», раздельные тест-команды, границы.
* `docs/CURATOR-REPORT-2026-10-08.md` — отчёт P0 (без изменений, базовое
  решение куратора там же).
* `docs/TELEGRAM-BOT-RFC.md` — P0 RFC; добавлен **D-13**: коллизия top-level
  пакета `storage` (collector vs chatbot) молча подменяет модуль в хост-тестах,
  хотя в контейнере `PYTHONPATH=/app:/opt` разрешается в chatbot-копию.

Не обновлялись (зарезервированы ТЗ за более поздними фазами): README,
`docs/04-env-vars.md`, `docs/09-telegram-commands.md`, `docs/10-testing.md`,
`docs/11-troubleshooting.md`, `docs/13-weekly-refresh.md`, `docs/14-security.md`,
`docs/15-acceptance-checklist.md`, `CHANGELOG_WEEKLY.md`, `docs/KNOWN_LIMITS.md`.

## 9. Риски и следующий шаг

Риски (обновлено после live-верификации 2026-10-12):

1. ~~**Блокирующий:** guard + пустой allowlist~~ — **снято**: `BOT_OWNER_IDS` задан,
   контейнер пересоздан, guard залогировал «allowlist configured».
   Важно на будущее: `env_file` читается при **создании** контейнера, `docker
   compose restart` новые ключи НЕ подтягивает — нужен `up -d` (recreate).
2. `drop_pending_updates=True` остаётся: без `bot_update_inbox` update может
   быть потерян при рестарте (TB-06 не закрыт).
3. Legacy-поверхности живы: `pipeline._handle_stop_safe` и `_build_*_response`
   всё ещё читают `trading_sessions`; старые `trader_*` callback и кнопки
   Аналитика/Прогнозы/Алерты ведут к прежним экранам — двойственность в этих
   сценариях ещё существует (P3/P4/P6).
4. ~~Код писался под хост 3.14~~ — **снято**: probe отработал на python 3.9 в
   контейнере. Полный прогон `tests/telegram` внутри образа ещё не делался.
5. Тесты P2 моделируют домен «аккуратнее», чем он есть (строка вместо enum) —
   D-14 пойман не suite-ом, а live-пробегом. Дальнейшие фикстуры обязаны
   использовать реальные доменные типы.
6. В `output/` остались только текстовые прогоны; probe-файлы удалены по
   решению куратора.

Следующий шаг: E3 частично закрыт (см. §6c) — осталось (a) отказ с второго,
не-allowlist аккаунта, (b) живой `/report`, (c) мобильный журнал (E4). Затем по
ТЗ §8 — P3 Plan: persisted wizard + deterministic validator с тем же
`plan_hash`/violations, что в WebUI. Discovery для P3 показал, что весь
план-контракт живёт в `webui/simulation_api.py` (normalize → validate →
`plan_hash`, `PostgresSimulationStore` поверх `simulation_v3`), а `./webui` НЕ
смонтирован в контейнер chatbot → для P3 нужно выбрать путь: HTTP в тот же API
или разделение детерминистического ядра; решение требуется до кода.

**Итог 2026-10-12:** куратор выбрал вынос ядра в общий пакет; это сделано как срез
**P3a** и описано в `docs/CURATOR-REPORT-P3A-2026-10-12.md` (дефект зафиксирован
как **D-16** в RFC; там же — перенос хранилища как P3b и права owner-only).
Хвосты E3 из (a)–(c) решено закрывать по ходу P3, а не как блокер.

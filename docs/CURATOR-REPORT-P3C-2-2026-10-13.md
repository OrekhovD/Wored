# P3c.2 отчёт — правка двух живых дефектов поверхности ввода (LIVE-1 / LIVE-2)

Дата: 2026-10-13. Фаза: хвост P3 (выходной гейт P3 закрыт 13.10 в
`docs/CURATOR-REPORT-P3C-2026-10-12.md` §6.1; этот срез чинит то, что выявил
тот же живой прогон, а не дифф P3c).

Решение куратора (AskUserQuestion, 13.10): объём — **«Оба дефекта»**, политика
для `/plans,` — **«Отсечь + починить матчинг»**. То есть одновременно:
хвостовая пунктуация нормализуется (`/plans,` открывает экран) **и** неизвестная
slash-команда получает честный отказ вместо AI-плана. Плюс тесты на оба и
рестарт `chatbot`.

**Выходной гейт среза: закрыт 13.10** живым прогоном из Telegram Desktop (§7) —
`/plans,` открывает экран планов за 1055 мс вместо 17-секундного AI-ответа,
`/wtf` даёт честный отказ за 345 мс, повторное нажатие `Сессия` даёт в логе
`console card already up to date` и не даёт `BACKEND_UNAVAILABLE`.

---

## 1. Корневые причины (проверено в живом рантайме, не по догадке)

### LIVE-1 — команда с прилипшей пунктуацией уходила в LLM

`Command("plans")` читает токен до первого пробела, поэтому `plans,` ≠ `plans`.
Проба в боевом контейнере (aiogram **3.22.0**, Python 3.9.25) до правки:

| ввод | матчинг `Command("plans")` |
|---|---|
| `/plans` | matched |
| `/plans,` | **not matched** |
| `/plans new` | matched |
| `/plans show <id>` | matched |

Не сматчённое сообщение доходит до последнего роутера —
`handlers/chat.py::cmd_chat` (`F.text`) → `route_request` →
`ai/router.py` классифицировал intent как `trade_plan` → tier `premium`
сгенерировал вход/СТО/тейк/R-R/уверенность. В логах 13.10 это ровно
`Update id=888276600 … Duration 56824 ms` + `AI 'premium_ollama' responded in
17.23s`, что совпало с шапкой карточки `17.2s`. Это прямое нарушение
«no invented numbers»: карточка, похожая на план, без `plan_hash`, без violations,
без контракта.

### LIVE-2 — штатный no-op edit красился как падение сервиса

`edit_text` с идентичным содержимым — нормальный ответ Telegram
(`message is not modified`). Обработчик консоли `now.py::_render` ловил его
как `except Exception` и рисовал `🔴 Сервис данных недоступен /
BACKEND_UNAVAILABLE`; в `simulation_v3._reply` то же условие не ловилось вовсе
(холостой «Перепроверить» обрывал callback — мёртвая кнопка). Строка
`экран не сформирован` имеет ровно одного производителя во всей кодовой базе —
`now.py::_render`, поэтому третья карточка из живого склеина была приписана
без гипотез.

Литеральный `/plans show <id>` (незаменённый плейсхолдер) дефектом **не**
является: пробой по реальному хранилищу — `plan_not_found` (и `''`, и `zzzz`),
утечки существования чужого плана нет.

## 2. Дифф

| Файл | Изменение |
|---|---|
| `chatbot/handlers/telegram_surface.py` | **новый** — единственное место обеих правил: `normalize_command_text`, `looks_like_command`, `is_no_op_edit`, `set_event_text`, `command_surface_middleware`, текст отказа `SLASH_HINT` |
| `chatbot/main.py` | `dp.message.outer_middleware(command_surface_middleware)` сразу после `guards.install(dp)` и до всех `include_router` + startup-строка `SURFACE: command token rule installed before all routers` |
| `chatbot/handlers/chat.py` | отказ до обращения к модели: текст, похожий на команду, не уходит в `route_request`; стоит **после** детерминистического product-intent ветвления, поэтому «покажи рынок» работает как раньше |
| `chatbot/handlers/now.py` | `_reply` возвращает `bool` и втягивает холостой edit; `_render` остаётся честным про настоящих сбоев чтения; `show_now` переведён с собственного дубля `answer/edit_text` на `_reply` |
| `chatbot/handlers/simulation_v3.py` | `_reply` — та же обработка холостого edit (карточка плана остаётся, callback живой) |
| `tests/telegram/test_slash_surface.py` | **новый** — 38 тестов на оба правила |

AI-роутинг (`ai/router.py`, `ai/*`), fallback-цепочка провайдеров, права, схема БД,
WebUI, legacy-зоны — **не тронуты**.

## 3. Одно место для правила (TB-03)

Правила не принадлежат конкретному экрану, поэтому живут в одном модуле, а
хендлеры только делегируют. Защищается тестами, а не устной договорённостью:

* `test_main_installs_the_surface_middleware_once_before_every_router` — ровно
  одна установка, до `dp.include_router(console_router)`, и startup-лог есть;
* `test_surface_module_is_the_only_place_that_knows_the_rule` — в `now.py` и
  `simulation_v3.py` есть вызов `surface.is_no_op_edit`, и ни в одном хендлере
  не повторена строка-признак (дословный текст Telegram живёт только в
  `telegram_surface.py`);
* `test_no_console_screen_reimplements_the_reply_branch` — ветка
  `target.message.edit_text(` в консоли одна (`_reply`); именно из-за неё
  `show_now` перестал быть вторым экземпляром той же логики.

## 4. Почему запись в `event.text` сделана через `object.__setattr__`

Модели aiogram собраны на pydantic с `frozen=True` — наивный `event.text = …`
бросает `ValidationError: Instance is frozen`. Проверено в **обоих** рантаймах:
в контейнере (`Message.model_config["frozen"] is True`, aiogram 3.22.0) и на хосте
(aiogram 3.27.0, тот же вывод в тесте). Поэтому нормализация пишет поле через
`set_event_text()` — тот же обход, которым пользуются тест-даблы
(`conftest.FakeMessage`). Меняется только `text`; `chat`, `from_user`,
`message_id`, `entities` остаются как пришли, так что авторизация (которая
смотрит на `from_user.id`) и любое другое чтение события не изменяются.
Инвариант закреплен тестом
`test_aiogram_message_text_is_frozen_so_the_rewrite_uses_the_bypass` — иначе
правка «починить матчинг» упала бы ValidationError-ом в продакшене на каждой
опечатке.

## 5. Гейты

```powershell
cd D:\WORED
$env:PYTHONPATH="D:\WORED;D:\WORED\chatbot"
python -m pytest tests/telegram -q                                  # 167 passed
python -m pytest chatbot/tests -q                                   # 26 passed
python -m pytest tests/test_f16_telegram_contract.py `
  tests/paper_trading/test_telegram_mock.py tests/test_ui_presenters.py -q   # 62 passed
python -m ruff check chatbot/handlers/telegram_surface.py chatbot/handlers/now.py `
  chatbot/handlers/simulation_v3.py chatbot/handlers/chat.py chatbot/main.py `
  tests/telegram/test_slash_surface.py                              # All checks passed
```

Было до правки в этом наборе: 129 passed в `tests/telegram` (38 новых). Полный
`pytest tests` на хосте: `1360 passed, 29 failed, 27 errors` — и те, и другие
шум окружения, к срезу не относятся: `tests/ui/test_v3_market_browser.py` требует
локально поднятого fixture-сервера на `127.0.0.1:18080` (в P3c-отчёте §11.5),
`tests/paper_trading/test_postgres_*` требуют БД, `tests/test_trading_math.py`
не собирается на хосте из-за отсутствия `hypothesis`. Ни один упавший файл не
тронут этим диффом.

## 6. Живое подтверждение рантайма

Рестарт (`docker compose restart chatbot`, разрешён решением куратора):

```
03:52:07,160 chatbot.security - INFO - AUTHZ: deny-by-default guard installed (allowlist configured)
03:52:07,160 chatbot - INFO - SURFACE: command token rule installed before all routers
03:52:09,175 chatbot - INFO - Chatbot started polling
03:52:10,035 aiogram.dispatcher - INFO - Run polling for bot @W_W_O_O_bot id=8686265741
```

Проба матчинга внутри боевого контейнера (тот же код, что в рантайме, без сети):

```
raw='/plans,'          text_after='/plans'  matched_before=False matched_after=True
raw='/plans'           text_after='/plans'  matched_before=True  matched_after=True
raw='/plans new'       text_after='/plans new' matched_before=True matched_after=True
raw='/plans show <id>' text_after='/plans show <id>' matched_before=True matched_after=True
raw='/palns'           text_after='/palns'  matched_before=False matched_after=False
```

то есть `/plans,` открывает экран планов, а опечатка в имени (`/palns`) экран не
открывает и уходит в честный отказ, а не в генерацию.

## 7. Живое подтверждение из Telegram Desktop (13.10, владелец открыл клиент)

Клиент вёл subagent (ComputerUse), только диалог `@W_W_O_O_bot`; мутаций не
производилось. Скриншоты:

| Проверка | Скриншот |
|---|---|
| `/plans,` | `…qoder-computer-use-images\24f75bb9\img-1791846563076422300-341127.png` |
| `/wtf` | `…qoder-computer-use-images\24f75bb9\img-1791846592482256700-147926.png` |
| `/session` | `…qoder-computer-use-images\24f75bb9\img-1791846629161093300-818617.png` |
| повторное нажатие `Сессия` | `…qoder-computer-use-images\9e6e4f84\img-1791868165682110600-879939.png` |

**LIVE-1 закрыт.** `/plans,` (06:09) вернул экран `📋 Мои планы` — «Показано: 2 ·
порядок: новые сверху», `7eabfa1c черновик v1 · live_paper ·
htx:linear-swap:BTC-USDT … hash 45623d99`, `12542dda … hash d664910c`,
`req 15bc8d26a69c · 🟢 хранилище планов v3 · Asia/Bangkok`. Ни входа, ни СТО,
ни «Уверенность …%» в ответе нет. Лог подтверждает и цену правки — время
обработки: было `Update … Duration 56824 ms` + `AI 'premium_ollama' responded in
17.23s`, стало:

```
06:09:05,345 chatbot.surface - INFO - command token normalized: '/plans,' -> '/plans'
06:09:05,852 aiogram.event - INFO - Update id=888276610 is handled. Duration 1055 ms
06:09:37,477 handlers.chat - INFO - unrecognised command declined without AI: '/wtf'
06:09:37,805 aiogram.event - INFO - Update id=888276611 is handled. Duration 345 ms
```

`/wtf` — карточка «🤔 Команда не распознана: похоже, в ней опечатка. Полный
список — /help, планы — /plans, текущий день — /session.» с пояснением, что
tекст, похожий на команду, не отправляется в модель; 345 мс вместо LLM-вызова.

**LIVE-2 закрыт.** На карточке `🎯 Сессия` (ID `0673a8ab`, RUNNING) навигация
была переключена туда-обратно (`Позиции` → `Сессия` — это доказательство, что
callback'и доходят до бота и сообщение редактируется на месте), затем `Сессия`
нажата повторно при неизменных данных:

```
12:09:20,906 chatbot.console - INFO - console card already up to date (4099799529388684093)
12:09:20,908 aiogram.event - INFO - Update id=888276617 is handled. Duration 503 ms
```

Карточка осталась прежней, красного нет. За всё окно 12:00+ в логе `chatbot`
**ни одной** строки `console screen … failed`, `message is not modified`,
`BACKEND_UNAVAILABLE`, `ERROR`/`Traceback` — то есть ложной аварии сервиса
данных больше нет, а `BACKEND_UNAVAILABLE` не обесценен (он по-прежнему
производится только на настоящих сбоях чтения — тест
`test_genuine_screen_failure_still_produces_the_error_card`).

### 7.1 Честная заметка о ходе живой пробы

Первый заход (06:10–06:11) зафиксировал только команды: два нажатия `Сессия`
до бота не дошли — в логе после `Update id=888276612` (06:10:14) не было ни
одного callback-обновления, поэтому результат «карточка не изменилась» тогда
был бы ничем не подтверждён и в зачёт не шёл. Проба повторена (12:03–12:09)
с калибровкой наведения и с обязательным контрольным переключением
`Позиции`↔`Сессия`.

Побочные эффекты повтора, зафиксированы как есть: из-за смещения клика (~24 px)
одно нажатие попало в кнопку `📋 Планы` — открылся read-экран списка планов
(мутации нет, `PLAN_DRAFT_CREATED` в логе отсутствует); одно нажатие задело
скрепку «прикрепить файл», системный диалог выбора файла закрыт через Escape,
ничего не выбрано и не отправлено; на секунду открывались соседние чаты, их
содержимое не читалось и в отчёт не попало. Никаких `Утвердить` /
`Перепроверить` / `Новый черновик` не нажималось, черновиков не создавалось.

## 8. Откат

Правка не требует миграций и не трогает env. Откат = удалить строку установки
middleware в `chatbot/main.py`, вернуть ветку `answer/edit_text` в
`show_now`/_reply-хелперах и убрать отказ в `cmd_chat`;
`handlers/telegram_surface.py` и `tests/telegram/test_slash_surface.py`
удаляются вместе. Оба дефекта после этого возвращаются как есть.

## 9. Уборка

Временные пробы этого среза удалены: `output/p3d_container_probe.py`
(матчинг в живом контейнере). Проба живёт в тексте отчёта §6, в репозитории
файлов проб нет.

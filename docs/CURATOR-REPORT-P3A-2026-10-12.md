# P3a отчёт — общий детерминистический план-контракт (WORED Telegram Bot V1)

Дата: 2026-10-12 · Commit baseline: `0f2359a` (праки не коммитятся — решение
куратора «Не коммитить») · Реализуемое задание:
`docs/QODER-TELEGRAM-BOT-V1-TZ-20260928.md`, фаза P3 (Plan), первый срез.

Решения куратора, на которых этот срез стоит:

* **«Вынести детерминистическое ядро в общий пакет»** (Q-09) — вместо HTTP-обхода
  V3 API и вместо второй копии валидатора в боте;
* **«Перенести и хранилище тоже»** — P3b (этот отчёт её только готовит);
* **«owner-only, старт только вручную»** (Q-10) — права на мутации план-домена;
* **«Сразу P3, хвосты по ходу»** — неисполненные хвосты P2 (отказ со второго
  аккаунта, живой `/report`, мобильный E4) не блокируют P3.

---

## 1. Цель и что было не так

ТЗ требует, чтобы бот показывал **те же** значения, что WebUI, и чтобы план,
подтверждённый из Telegram, нёс **тот же** `plan_hash`. До этого шага весь
детерминистический контракт — каталоги, `normalize_plan`,
`check_plan_semantics`, `estimate_worst_case_risk`, `plan_hash` — и реестр
инструментов жили только в `webui/simulation_api.py` и `webui/instrument_registry.py`,
а `./webui` **не** смонтирован в контейнер `chatbot` (`PYTHONPATH=/app:/opt`).
Значит бот мог либо ходить в HTTP, либо молча завести второй валидатор — второй
вариант ТЗ запрещает. Зафиксировано как дефект **D-16** в
`docs/TELEGRAM-BOT-RFC.md`.

## 2. Архитектурное решение

Ядро перенесено **дословно** в уже общий пакет `trading_math`:

```
trading_math/plan_contract.py        454 строк  НОВОЕ  (ядро: каталоги → plan_hash)
trading_math/instrument_registry.py  198 строк  НОВОЕ  (перенос из webui/, stdlib-only)
webui/instrument_registry.py           34 строки  ШИМ   (+31 / −183 по git numstat)
webui/simulation_api.py               833 строки  РЕФАКТОР (+94 / −414; stores и endpoint'ы целы)
```

(`git diff --numstat` по этим двум файлам — цифры взяты из него, а не «на слух».)

Почему `trading_math`, а не новый пакет: каталог уже смонтирован как
`/opt/trading_math:ro` в **chatbot, webui и collector**, и `/opt` входит в
`PYTHONPATH` всех трёх → **правка `docker-compose.yml` не потребовалась** (зона
«только с подтверждением» не тронута).

Что сознательно осталось в `webui`: `router`, `_owner_id`, CSRF, `PlanRecord`/
`SessionRecord`, `MemorySimulationStore`/`PostgresSimulationStore`, start-gates
(`_market_state`, `_coverage`) — им нужны request, пул и Redis-ключ, тащить это
в ботовый образ означало бы добавить ему web-зависимости.

Граница честности: `webui/simulation_api.py` больше **не определяет** контракт, а
переэкспортирует его (`import` + `__all__`), поэтому ни один существующий
`from simulation_api import …` не менялся — включая приватные по имени
`_dec_str`/`_parse_*`, на которые опираются QA-сьюты, и `from instrument_registry
import load_registry` в 13 файлах (шим сохраняет и текстовый ассерт
`tests/test_forecast_command_v3.py:1020`).

## 3. Как доказано, что перенос дословный

`git diff --no-index webui/simulation_api.py trading_math/plan_contract.py` перед
удалением: единственный изменённый участок — docstring и import-заголовок; на
строках 56…464 **ни одного** hunk, то есть тело совпадает побайтово. Это важно не
для красоты, а для хеша: порядок `PLAN_FIELDS` и нормализация Decimal/ISO
входят в canonical JSON, и любая «оптичская» правка сдвинула бы все `plan_hash`
и сломала MC-11 (immutability).

## 4. Фактические прогоны (stdout)

Хост, `C:\Users\dolum\AppData\Local\Python\pythoncore-3.14-64\python.exe`:

```
tests/test_simulation_api.py        37 passed
tests/test_instrument_registry.py   12 passed
tests/test_market_workspace.py      31 passed
tests/test_positions_workspace.py   28 passed
tests/test_forecast_command_v3.py   55 passed, 4 skipped
tests/ui/test_v3_market_ui.py       45 passed
tests/test_simulation_proposal.py   22 passed   # с PYTHONPATH+=tests (D-12, см. RFC §14)
tests/telegram                     106 passed
chatbot/tests                       26 passed
tests/test_f16_telegram_contract.py + tests/paper_trading/test_telegram_mock.py   14 passed
ruff check webui/simulation_api.py webui/instrument_registry.py trading_math       All checks passed!
```

Браузерная приёмка MC-V3 (headless Chromium против `tests.ui.fixture_app_v3:app`
на 127.0.0.1:18080, сервер поднят и снят вручную):

```
tests/ui/test_v3_market_browser.py -k simulation
4 passed, 24 deselected in 22.59s
# MC-09 persist/restore, MC-10 stale quote, MC-10 missing intervals,
# MC-11 approve pins plan_hash and the approved version stays immutable
```

In-container read-only probe (`output/p3-hashprobe.py`, `docker compose cp` →
`/tmp`, ничего не мутирует), один и тот же план:

```
WEBUI    python 3.11.16 | zoneinfo ok | registry ['htx:linear-swap:BTC-USDT']
                     | violations [] semantics [] | webui re-export identity: True True
CHATBOT  python  3.9.25 | zoneinfo ok | registry ['htx:linear-swap:BTC-USDT']
                     | violations [] semantics [] | webui module not importable here
HOST     python 3.14.3  | violations [] semantics []
hash во всех трёх: sha256:e934197cb67099a2eda70169fd15081ac4ca3e75f18d8fd5efa7f48d9f5c53fb
```

Probe-файл (`output/p3-hashprobe.py` и его копии в `/tmp` обоих контейнеров) удалён
по тому же правилу гигиены, что и `output/p2-*`: артефакты фазы не остаются, а
stdout зафиксирован здесь. Повторить можно, собрав тот же план из
`trading_math.plan_contract` в любом из трёх окружений.

Три вещи, которые probe проверяет по-настоящему: код ядра живёт на **Python 3.9**
в ботовом образе (аннотации `dict[str, …]` не падают, потому что
`from __future__ import annotations` остался первой строкой перенесённого файла);
в образе есть база часов (`ZoneInfo("Asia/Bangkok")` ok — иначе каждый план
получал бы ложную `unknown IANA timezone`); реестр читается из `/config`, который
смонтирован и в chatbot.

## 5. Что НЕ менялось

Миграций нет; `simulation_plan_versions`/`simulation_sessions` не тронуты. Env не
менялся. `docker-compose.yml` не менялся. Данные не писались. Ничего не коммитилось.
Bot-код (`chatbot/**`) в этом срезе не менялся — бот начнёт импортировать ядро в P3b.

## 6. Состояние прода и один незакрытый хвост

Проверено в этом же обороте: 5 контейнеров `Up` (`chatbot Up 32 minutes (healthy)`,
`webui Up 15 hours (healthy)`), и **запущенный процесс webui всё ещё держит старый
модуль** — bind-mount меняет файлы, а Python импортирует их при старте процесса.
Практически это безвредно (новый код даёт идентичный хеш, MC-11 пройдена), но
пока restart не выполнен, работающий дашборд и файлы на диске — разные ревизии.
`docker compose restart webui` — действие из зоны «только с подтверждением»
(AGENTS.md), поэтому не выполнено; запрос вынесен куратору.

## 7. Риски

1. **Двойная интерпретация имён.** `simulation_api.PLAN_FIELDS` и
   `plan_contract.PLAN_FIELDS` — один объект; но если следующий агент скопирует
   каталог «на место», тесты это не поймают (они сверяют значения, а не
   идентичность). Защита: `__all__` + докстринг + ассерт идентичности в probe.
2. **Дрейф хеша при «мелкой правке» нормализации.** Единственный страж —
   MC-11 и `test_plan_hash_*`; перед правкой ядра обязателен прогон
   `tests/test_simulation_api.py` **и** браузерной приёмки.
3. **P3b ещё не построена:** бот не читает и не пишет планы; `draft → approve →
   start` из Telegram нет. Перенос хранилища (решение куратора) — следующий
   шаг, и он заметнее задевает webui, чем P3a.
4. **Хвосты P2** (E3-отказ с второго аккаунта, живой `/report`, E4 mobile) не
   закрыты по-прежнему.

## 8. Следующий шаг

P3b: перенести `PlanRecord`/`SessionRecord`/stores в общий пакет (или в
`trading_math/simulation_store.py`), затем bot-side экраны плана: draft →
валидация с теми же violations → `plan_hash` → превью worst-loss; права —
owner-only, старт сессии только вручную (Q-10). До кода — PLAN→DIFF по AGENTS.md,
потому что затрагивается активный runtime path `webui/simulation_api.py`.

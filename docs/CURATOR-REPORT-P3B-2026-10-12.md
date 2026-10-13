# P3b отчёт — общие хранилища плана/сессии и единый owner (WORED Telegram Bot V1)

Дата: 2026-10-12 · Commit baseline: `0f2359a` (праки не коммитятся — решение
куратора «Не коммитить») · Реализуемое задание:
`docs/QODER-TELEGRAM-BOT-V1-TZ-20260928.md`, фаза P3 (Plan), второй срез.

Решения куратора, на которых стоит этот срез:

* **«Перенести и хранилище тоже»** — stores уходят в общий пакет вместе с
  контрактом P3a;
* **«uuid5 + миграция 9 строк»** — план-домен переходит на каноническое
  owner-пространство `paper_trading` / WebUI / бота;
* **«Файл миграции + применить сейчас»** — миграция написана и выполнена на живой
  БД в этом же срезе;
* **«Вынос хранилища + гейты, без bot-экранов»** — экраны плана в Telegram
  вынесены в P3c, чтобы не смешивать рефакторинг с новыми write-путями.

---

## 1. Цель и что было не так

После P3a в боте стал доступен **тот же валидатор**, но не **то же хранилище**:
`PlanImmutable`, `PlanRecord`, `SessionRecord`, оба store-а и два сериализатора
`*_to_dict` жили только в `webui/simulation_api.py`, а `./webui` не смонтирован в
контейнер `chatbot` (`PYTHONPATH=/app:/opt`). Бот, напиши он план, был бы вынужден
либо ходить в HTTP, либо заново реализовывать state machine — второе ТЗ запрещает.

Второй, более серьёзный дефект всплыл при discovery: **два owner-пространства для
одного человека**.

| Место | было |
|---|---|
| `webui/simulation_api._owner_id` (план-домен) | `"telegram:<user_id>"` / `"password:<username>"` |
| `webui/paper_api._owner_from_request`, `webui/positions_workspace`, `chatbot/security/principal.py`, `paper_trading/adapter.owner_id_from_telegram` | `uuid5(NAMESPACE_URL, "wored:owner:{...}")` |

Живая проверка до правки: `simulation_plan_versions` = 3 строки,
`simulation_sessions` = 3, `simulation_plan_proposals` = 3, `simulation_reports` = 0,
и **у всех `owner_id = password:admin`**. Если бы бот начал писать планы под своим
uuid5-owner, обе стороны увидели бы пустой список — не ошибку, а невидимые данные.
Зафиксировано как дефект **D-17** в `docs/TELEGRAM-BOT-RFC.md`.

## 2. Архитектура среза

```
trading_math/simulation_store.py    376 строк  НОВОЕ   (перенос 147–489 из webui, дословно)
webui/simulation_api.py             538 строк  РЕФАКТОР (было 833 → −295; endpoint'ы и CSRF целы)
db/migrations/20261012_simulation_owner_unification.sql   84 строки  НОВОЕ (applied)
```

Перенесено 7 символов: `PlanImmutable`, `PlanRecord`, `SessionRecord`,
`_plan_to_dict`, `_session_to_dict`, `MemorySimulationStore`,
`PostgresSimulationStore`. Зависимости нового модуля — stdlib и
`trading_math.plan_contract` (`_iso`, `SCHEMA_VERSION`, `PLAN_TYPE`,
`SESSION_TRANSITIONS`); пул asyncpg **инжектируется** и остаёт duck-typed
(`pool: Any`), поэтому модуль грузится в контейнере бота на Python 3.9, не тянет за
собой FastAPI. `webui/simulation_api.py` импортирует эти имена и перечисляет их в
`__all__` — внешние импортеры (`tests/test_simulation_api.py:19,232`,
`tests/ui/fixture_app_v3.py:37,370`, `webui/simulation_proposal.py`) продолжают
импортировать как раньше.

`_owner_id` теперь возвращает каноническое значение: берёт
`paper_trading.adapter.owner_id_from_webui` (тот же источник, что `paper_api`), с
stdlib-fallback на случай отсутствия `/opt/paper_trading`. Fail-closed сохранён:
неаутентификационный запрос по-прежнему **401**, implicit `admin`-fallback из
`paper_api` сюда не перенесён.

## 3. Доказательство дословности переноса

Файл-приёмник собран скриптом из строк 147–489 исходного файла (не переписывался
вручную), затем до удаления оригинала выполнен сравнительный diff:

```
git diff --no-index -U0 webui/simulation_api.py trading_math/simulation_store.py
@@ -1,36 +1,17 @@          (docstring)
@@ -40,2 +20,0 @@         (убран hashlib/hmac-строки импортов)
@@ -43 +21,0 @@           (убран logging)
@@ -49,16 +27 @@          (заголовок импортов)
@@ -66,2 +28,0 @@ / @@ -69 +29,0 @@ / @@ -71,8 +30,0 @@ / @@ -80,8 +31,0 @@
@@ -90,57 +33,0 @@        (webui-only блок: __all__, log, router, pointer)
@@ -490,344 +376,0 @@     (всё, что шло ПОСЛЕ stores, в новый файл не вошло)
```

Ключевое: между `@@ -90,57` (кончается на строке 146) и `@@ -490,344` **нет ни
одного хэка** — то есть диапазон 147–489 перенесён побайтово идентично. Логику
stores никто не правил.

## 4. Унификация owner: миграция применена

Снимок до (только чтение):

```
plans|password:admin|3      sessions|password:admin|3      proposals|password:admin|3
reports: 0 строк
```

Backup затронутых строк (id + owner) снят перед UPDATE и вставлен сюда же, потому что
`output/` в `.gitignore` и как рабочий артефакт не переживает уборку:

```
plan|fce37216-197c-4c60-9c97-b4ab95da8ff2|password:admin
plan|b43840c0-95f8-47b2-a282-bf5ee6c0c281|password:admin
plan|04a1a46e-87e1-4bf8-a72f-3b2d351cc711|password:admin
session|014443c1-f82b-4b30-b6a0-01e7bf76ea46|password:admin
session|44edd101-b025-4814-b48e-0f09c04f166a|password:admin
session|776e1bca-c378-4cd7-84e6-27d452822521|password:admin
proposal|a547dca4-8ec6-4d23-8a95-a9688eec377c|password:admin
proposal|00af9709-3177-4abc-b5f7-b857ebee4cba|password:admin
proposal|76d34bd6-5d6e-4581-8723-44b7d381403e|password:admin
```

Выполнение (`psql -v ON_ERROR_STOP=1`, stdin):

```
BEGIN
 legacy_a | legacy_b | idempotency_key  →  (0 rows)   -- pre-flight: коллизий
UPDATE 3 / UPDATE 3 / UPDATE 3 / UPDATE 0             -- plans / sessions / proposals / reports
 table_name | owner_id | count  →  (0 rows)          -- post-flight: префиксов не осталось
COMMIT
```

После:

```
plans|4f4d18fb-a0bc-5a1a-aaa0-09f86fde8a03|3
sessions|4f4d18fb-a0bc-5a1a-aaa0-09f86fde8a03|3
proposals|4f4d18fb-a0bc-5a1a-aaa0-09f86fde8a03|3
```

`4f4d18fb-…` = `uuid5(NAMESPACE_URL, "wored:owner:admin")`, посчитан в контейнере
webui (Python 3.11.16) и на хосте (Python 3.14) — одно значение. Повторный запуск
файла миграции идемпотентен (0 затронутых строк, `EXIT=0`). Тип колонок `TEXT`
(`20260929_simulation_v3.sql:13,35,63`, `20260930_simulation_reports.sql:19`),
схема не менялась, строки не удалялись; откат — реверс-UPDATE в конце файла.

⚠️ `db/` игнорируется `.gitignore:8` (существующие 4 миграции добавлены с `-f`).
При коммите файл нужно добавить явно:
`git add -f db/migrations/20261012_simulation_owner_unification.sql`.

## 5. Гейты

| Гейт | Результат |
|---|---|
| `ruff check trading_math/simulation_store.py webui/simulation_api.py tests/test_simulation_proposal.py` | All checks passed! |
| `tests/test_simulation_api.py` | **37 passed** |
| `tests/test_simulation_proposal.py` + `tests/test_instrument_registry.py` | **34 passed** (22 + 12) |
| весь `tests` кроме browser/telegram/`test_trading_math` | **1185 passed, 61 skipped, 1 xfailed**, 1 failed, 27 errors |
| `tests/telegram` | **106 passed** |
| MC-V3 browser, `-k simulation` | **4 passed**, 24 deselected (15.47s) |
| MC-V3 browser, весь файл | **28 passed** (91.92s) |
| `docker compose restart webui` | `Up (healthy)`, `/`, `/alerts`, `/predictions`, `/journal`, `/command-deck`, `/futures-lab` = **303**, `/healthz`, `/readyz` = **200** |
| неаутентификационный `GET` и `POST /api/v3/simulation/plans` | **401 / 401** (auth не ослаблен) |
| логи webui после рестарта | только access-строки, ни Traceback, ни ImportError |

Две оговорки к цифре 1185: `tests/test_trading_math.py` не собирается на хосте
(`ModuleNotFoundError: hypothesis` — окружение, не код), 27 errors и 1 failed —
это DB-фикстуры, которые ходят в Postgres по docker-имени хоста:
`test_promotion.py::test_promote_activates_through_gate_on_db` падает с
`socket.gaierror: [Errno 11001] getaddrinfo failed`. Это limit хост-прогона
(он был и до P3b), поэтому DB-path закрыт отдельной проверкой ниже, а не pytest.

## 6. Живое поведение в двух рантаймах

Проба (только чтение, nothing persisted) в webui **3.11.16** и chatbot **3.9.25**:

```
violations []
plan_hash sha256:7b69569f299a8ad69e1dbbe30d30ff91905eaebeccc6cd31c01d309bea533525
worst_case {'worst_allowed_loss_usdt': '20', 'drivers': {...}, 'method': 'min(...)'}
webui re-export identity: True True True      (классы из webui is те же объекты)
PlanImmutable: already approved               (immutability работает в обоих)
transition running log entries 1
canonical admin owner 4f4d18fb-a0bc-5a1a-aaa0-09f86fde8a03   (в обоих)
```

`chatbot` печатает `webui re-export: n/a` — `simulation_api` там и не может
импортироваться, что и есть проверка корректности направления зависимости: общий
пакет ничего не знает про webui.

Чтение живой БД через `PostgresSimulationStore` после миграции (webui-контейнер):

```
owner for password/admin  : 4f4d18fb-a0bc-5a1a-aaa0-09f86fde8a03
plans visible to webui    : 3 [('04a1a46e','approved','sha256:2e219f0b5fa'),
                               ('b43840c0','approved','sha256:ab35d122f00'),
                               ('fce37216','approved','sha256:578f17ef442')]
roundtrip                 : True/True для всех трёх
leftover prefixed owners  : []
cross-owner isolation     : 0 (другой owner не видит чужих планов)
```

То есть re-key не прячет данные: прежние три approved-плана (id совпадают с
backup из §4) видны каноническому owner, изоляция по owner работает.

## 7. Что НЕ менялось

endpoint'ы и их коды ответов, `_require_csrf`, `_get_store`, `_market_state`,
`_coverage`, `market_workspace`, `webui/simulation_proposal.py` (кроме одной
ассерции в её тесте), таблицы и схема, `chatbot/*` (экраны P3b не трогали),
Compose, `.env*`, `plan_hash`-выражение. Значение `paper_store.paper_owner_key`
не менялось — см. §8.

## 8. Риски и открытое

1. **Третье owner-пространство осталось.** `webui/paper_store.py:288-300`
   (`paper_owner_key`) по-прежнему отдаёт `telegram:<id>` / `password:<name>`, но
   для другой семьи таблиц (`paper_trading_runtime`, `paper_trading_ledger`,
   колонка `owner_key`), к plan-домену отношения не имеет. Не трогал в этом срезе;
   требует отдельного решения, иначе P4/P5 унаследуют тот же класс дефекта.
2. **`legacy` owner-значения в чужих таблицах не мигрированы** — файл миграции
   намеренно покрывает только четыре simulation-таблицы; если где-то ещё
   хранится `password:admin`, он здесь не виден.
3. **DB-store не покрыт хостовым pytest** (см. §5, оговорка): `PostgresSimulationStore`
   проверен отдельной read-only пробой в контейнере, а не тестом. Перед P3c стоит
   решить, запускать ли `tests/paper_trading` внутри docker-сети как гейт.
4. **Мутации план-домена из Telegram всё ещё нет** — срез был вынос + унификация.
   Write-path бота обязан использовать `chatbot/security/principal.py` (uuid5) и
   `BOT_OWNER_IDS`, старт сессии — только руками (Q-10).
5. **Хвосты P2 не закрыты**: живой `/report`, отказ со второго аккаунта, мобильный
   E4, `drop_pending_updates=True` (TB-06).

## 9. Следующий шаг (P3c, PLAN пока не применён)

`chatbot/handlers/plan.py` + `chatbot/services/plan_service.py` поверх общего
`trading_math.simulation_store`: `/plan` — список своих планов, `/plan new` —
draft → `normalize_plan` → те же violations → `plan_hash` → превью
`estimate_worst_case_risk`; права owner-only; approve/start остаются в WebUI.
Gates: `tests/telegram`, `tests/test_simulation_api.py`, MC-V3 browser, in-container
probe, отчёт по ТЗ §10.

## 10. Уборка

Пробники `/tmp/p3b-probe.py`, `/tmp/p3b-owner-check.py` удалены из контейнеров
webui и chatbot (`grep -c p3b` = 0 в обоих), host-скрипты
`output/p3b-store-probe.py`, `output/p3b_extract_stores.ps1`,
`output/p3b_drop_stores.ps1`, `output/p3b-owner-check.py`,
`output/p3b-owner-backup-20261012.txt` удалены (снимок backup перенесён в §4 этого
отчёта). QA-сервер `fixture_app_v3` на 18080 остановлен, порт закрыт. Ничего не
закоммичено; baseline остаётся `0f2359a`.

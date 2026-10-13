# WORED — Операционный процесс

Восстановлен 2026-09-22 после переписывания истории (rebase на `origin/main`),
в котором оригинал этого файла был утрачен без следа. Содержит одну поправку,
которой в исходной версии не было: обязательный `--build` в команде гейта.

## 1. Авторитетный тестовый гейт

```bash
# ОБЯЗАТЕЛЕН --build. docker-compose.qa.yml собирает образ через build: (COPY
# репозитория), bind-монтажа нет. Без --build compose молча переиспользует
# старый слой и прогоняет ТЕСТЫ ДО ПРАВОК, честно зелёные.
docker compose -f docker-compose.qa.yml -p wored-qa run --rm --build checks \
  pytest tests/ -q -p no:cacheprovider
```

Точка входа — `pytest tests/`. `make test` в репозитории нет, `webui/tests/`
в гейт не входит. Практическое следствие: контрактный тест в
`webui/tests/test_prediction_engine.py` (`test_nvidia_fallback_requires_its_own_credential`)
не запускается гейтом вообще, и его расхождение с кодом обнаружилось только
ручной прогоном — правки в `webui/tests/` надо запускать отдельно:

```bash
python -m pytest webui/tests/test_prediction_engine.py -q
```

Браузерные доказательства (`tests/ui/test_v3_market_browser.py`) живут на хосте:
в QA-образе playwright не устанавливается намеренно. Файл импортировал
`playwright.async_api` на уровне модуля, и с коммита `69650fe` это роняло
**всю** коллекцию авторитетного гейта — 264 теста `tests/ui/` просто не
проверялись. Сейчас импорт обёрнут в `pytest.importorskip`, поэтому отсутствие
зависимости даёт один skip модуля, а не ошибку сборки тестов.

### Что доказывает какой контур

| Контур | Что доказывает | Чего доказать не может |
|---|---|---|
| **QA-образ** (команда выше) — авторитетный полный гейт | вся собранная база тестов: unit + контрактные + DB-тесты на tmpfs-Postgres, включая `hypothesis`-набор `tests/test_trading_math.py` | браузерные доказательства (playwright в образ не ставится намеренно), живой контур, поведение на python 3.9 |
| **Быстрые хост-наборы** (см. §2) | офлайн-логику среза до обращения к QA-контуру | не является «зелёным гейтом»: нет БД, нет `hypothesis`, `.md`-тесты падают на cp1251 |
| **Живой контур** (§3) | какой код крутится в проде прямо сейчас | не заменяет тесты; только на чтение без слова куратора |

Имя проекта QA. В команде выше это `-p wored-qa`, в отчётах P3d/P4 прогон зафиксирован
под `-p woredqa`. Оба варианта корректны ровно постольку, поскольку это **отдельное**
проектное имя: `docker-compose.qa.yml` без `-p` берёт имя из имени каталога и попадает в
прод-сеть `wored_default`, а `--remove-orphans` на таком прогоне снимает живые контейнеры.

Отдельно про python 3.9. Контейнер `chatbot` собран на `python:3.9-slim`, QA-образ — на
3.11, хост — на 3.14. Класс дефектов — PEP 604 (`X | Y`) в сигнатуре, которая вычисляется
при импорте: хост и QA зелёные, живой бот падает в crash-loop при старте. Единственная
защита — аудит AST внутри контейнера 3.9 плюс перезапуск живого `chatbot` после любой
правки `chatbot/`:

```powershell
# пути рантайма чата: /app (chatbot) и /opt/<pkg> (paper_trading, trading_math,
# forecast_engine). Счётчик «модулей просканировано» обязателен к прочтению:
# без него ноль неотличим от «просканировали пустоту».
docker cp output/p4_py39_audit.py htx_trading_bot_chatbot:/tmp/p4_py39_audit.py
docker exec htx_trading_bot_chatbot sh -c "cd / && python /tmp/p4_py39_audit.py /app /opt/paper_trading /opt/trading_math /opt/forecast_engine > /tmp/p4-py39-audit.log 2>&1"
docker cp htx_trading_bot_chatbot:/tmp/p4-py39-audit.log output/p4-py39-audit.log
# замер 2026-10-14: модулей просканировано 129, нарушений 0
```

Результат «0» без второй строки ничего не значит: опечатка в корне даёт тихий ноль.

### Как понять, что гейт проверял не тот код

Единственный надёжный признак — число собранных тестов не выросло после того,
как вы добавили тесты. Проверить напрямую:

```bash
docker compose -f docker-compose.qa.yml -p wored-qa run --rm checks \
  grep -c '<ИмяНовогоКласса>' /repo/tests/stabilization/test_contracts.py
```

`0` при живом классе на хосте = образ устарел, пересобрать.

### Предупреждения как утверждения

`-W error::RuntimeWarning` превращает «coroutine was never awaited» в отказ
теста. Дешёвый способ поймать ветку, которая тихо глотает исключение и никогда
не отрабатывает. Именно так, до правки `c2fe3bf`, себя вела калибровка
уверенности: она не применялась никогда, ни в асинхронном, ни в синхронном пути.

## 2. Реестр известных отказов

Актуальная базовая линия на 2026-10-14 (после P1–P4 чатного контура):
**1576 passed, 16 skipped, 1 xfailed, 0 failed** в QA-образе (`python 3.11.16`,
`python -m pytest tests -q -p no:warnings`, 70.86 s). Пропуски — те же 16 с теми же
причинами, состав отказов пуст.

Прежняя базовая линия на 2026-10-08 (правки `tests/` того хода, до коммита):
**1275 passed, 16 skipped, 1 xfailed, 0 failed** — `PYTEST_EXIT=0`.

Предыдущая линия (`b00351d`): 853 passed, 20 skipped, 1 xfailed, 5 failed.
Все пять записанных тогда отказов закрыты, и состав отказов сменился целиком,
так что таблица ниже заменена на актуальную:

| Было в реестре | Чем закрыто |
|---|---|
| `ProviderTests::test_native_adapter_rejects_thinking_only_and_truncated_response` | `8a2d403` принимает `done_reason=length`; тест переписан под фактический контракт (`ab14318`) |
| `TestEnvExample::test_env_example_exists`, `test_env_wored_example_exists` | `3b69725`: `.env.example` / `.env.wored.example` re-include в образ (проверены на отсутствие секретов) |
| `TestDocumentationFiles::test_required_docs_exist` | `3b69725`: markdown `docs/` попадает в образ |
| `TestQALockFiles::test_qa_requirements_exists` | `ab14318`: путь стал ROOT-относительным с override `WORED_HERMES_PAYLOAD_DIR`; `requirements-qa.txt` re-include |

Шесть отказов, найденных при восстановлении авторитетного гейта 2026-10-08 и закрытых в том же ходе:

| Отказ | Природа | Правка |
|---|---|---|
| `tests/test_episodes.py` ×4 (`net_pnl`, `both_loops`, `short_side`, `r_multiple`) | Протухшие fee-голдены: арифметика ставки 0.0006 при активном `TAKER_FEE_RATE = 0.0005`. Пересчитаны по примитивам (9.8740 → 9.8950, r 0.9874 → 0.9895) | только тест |
| `tests/test_paper_api.py::test_manual_fill_close_and_report_use_actual_demo_state` | То же: gross = 0, `realized_net` целиком из комиссий, 0.0005 × 2570 = 1.285 (было 1.542) | только тест |
| `tests/test_market_stream_unit.py::TestSSERouteGuards::test_stream_route_is_registered` | **Не рантайм**: FastAPI 0.141.1 представляет `include_router()` одним `_IncludedRouter` без `.routes`, поэтому скан `isinstance(r, APIRoute)` легитимно давал пустой dict. Проверено в образе: `in_schema True ['get']` | тест переведён на OpenAPI + `operationId` |
| `tests/ui/test_v3_market_ui.py::TestV3PositionsContract::test_cache_buster_bumped_for_p5` | Устаревший токен: `workspace.html:264` отдаёт `market-workspace.js?v=20261004-1`, тест ждал `20260930-2`. Был невидим, пока коллекция `tests/ui` падала | тест сверен с шаблоном |

Процессное следствие по fee-голденам: правило пересчёта голденов обязано включать
**repo-wide grep по старому значению ставки**, а не только файлы затронутого suite —
коммит `6b5e9cc` обновил `tests/paper_trading` и `webui/tests`, но пропустил
`tests/test_episodes.py` и `tests/test_paper_api.py`, и авторитетный гейт из-за
сломанной коллекции этого не увидел.

16 пропусков в базовой линии объяснены полностью: 8 и 7 —
`tests/paper_trading/test_browser_acceptance.py` (нет доступа к живому WebUI из-за
`internal: true` сети QA; не задан `WEBUI_ADMIN_PASSWORD`), 1 — skip модуля
браузерных доказательств без playwright.

Нельзя закрывать гейт фразой «ожидается N отказов» без сверки с этой таблицей:
состав отказов должен оставаться пустым, а пропуски — равными 16 с этими причинами.
Число тестов в заявлении «полный гейт зелёный» берётся **только** из QA-образа с
`--build`; хост-прогон не может дать эту строку в принципе и не должен цитироваться
как полный гейт ни в одном отчёте.

Прогон того же набора на хосте (`python -m pytest tests/ -q`) гейтом не является и
даёт другую картину: без `hypothesis` не собирается `tests/test_trading_math.py`,
postgres-тесты ошибаются без БД, а `.md`-тесты падают на UnicodeDecodeError из-за
cp1251-локали Windows. Эти отказы не регрессии; сравнивать с базовой линией
нужно только контейнерный прогон.

Хост при этом годен на быстрые офлайн-наборы, и именно они должны называться в
отчётах как хост-гейт (числа ниже — прогон 2026-10-14):

```powershell
$env:PYTHONPATH = "D:\WORED;D:\WORED\tests;D:\WORED\webui;D:\WORED\chatbot;D:\WORED\collector"
py -m pytest tests/telegram -q                                              # 233 passed
py -m pytest tests/test_paper_trade_ticket.py tests/test_paper_order_ticket.py `
    tests/test_chatbot_paper_orders.py tests/paper_trading/test_ticket.py -q  #  86 passed
py -m pytest tests/ui/test_ticket.py tests/test_paper_api_domain.py tests/test_paper_api.py -q
                                                                             #  37 passed
```

Полный же хост-прогон (`py -m pytest tests -q --ignore=tests/test_trading_math.py`) даёт
`1514 passed, 1 failed, 27 errors`, и все 28 отказов — природы «окружение», а не регрессия: это
tests/paper_trading/{balance_store, closeout, execution, fills, promotion, repository,
runner, session_execution_pg, session_report_pg}.py, которым нужен Postgres
(`socket.gaierror` при резолве DSN). В QA-контейнере с `WORED_TEST_DATABASE_URL` ровно эти
тесты зелёные. Если хост-прогон показывает что-то помимо этого списка — это уже отказ, а
не «известное ограничение окружения».

## 3. Проверка живого рантайма (тесты её не заменяют)

Прод-стек поднимается отдельно от QA (`docker compose up -d`). Минимальный
набор утверждений после правки модуля прогнозов:

```bash
docker compose ps
docker compose exec -T webui python -c "import inspect, prediction_engine as p; \
print('ollama_local' in inspect.getsource(p))"
foreach ($r in "/","/alerts","/predictions","/journal","/daily-session") { \
  (Invoke-WebRequest "http://127.0.0.1:8080$r" -UseBasicParsing).StatusCode }
```

Числа в логах и статусы HTTP — из текущего turn, иначе не утверждать.

После `241e15b`/`b00351d` к тому же списку относится честность полосы в карточке
трейдера. `/api/trader/forecast` защищён `_require_api_auth`, поэтому проверять
его либо изнутри контейнера с сессией, либо офлайновым прогоном агрегации на
прод-дампе: `python scripts/probe_forecast_role_coverage.py [id ...]` (дамп делает
`scripts/probe_forecast_role_coverage.sql`, в БД не пишет). Замер 2026-09-22 на
свежих строках: #137 — `basis=two-roles missing=['arbiter']`, #139 — `three-roles`,
но `models=2-2` (бык и арбитр ответили одной `glm-5.1` — это M6, не три мнения),
#141 — `three-roles models=3-3`. Ожидаемый смысл проверки: бейдж «роли 3/3» там,
где арбитр молчит, — это отказ. Живой `webui` до пересборки образа крутит
код до этих коммитов; перезапуск живых сервисов без явного слова не выполняется.

## 4. Локальная модель bonsai-27b: как она должна жить

На рабочей станции стоят два сервера Ollama, и это норма, а не поломка:

| Сервер | Порт | Хранилище | Роль |
|---|---|---|---|
| desktop-приложение (`ollama app.exe`) | 11434 | должно быть `C:\Users\dolum\.ollama\models` | UI, просмотр магазина |
| dedicated `serve` | 8088 | `C:\Users\dolum\.ollama\models` | контракт WORED, `bonsai-27b` |

VRAM-правило: RTX 2070 = 8192 MiB, одна копия `bonsai-27b` при ctx 8192 занимает
~4.1 GiB. Модель имеет право быть загруженной только в одном сервере; второй —
только просмотр. `OLLAMA_CONTEXT_LENGTH=262144` из конфига приложения (замер 2026-09-23 в таблице
`settings` его же БД) для этой модели нерабочий.

Супервизия (единственный способ назвать работу «бесперебойной»):

```powershell
# разовая проверка, ничего не меняет: 0 healthy / 1 порт молчит / 2 не то хранилище
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bonsai_health.ps1 -AsJson
# таск WORED-Bonsai-8088: логон + каждые 5 минут, один экземпляр, рестарт при падении
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_bonsai_supervision.ps1 -RunNow
# состояние и история: logs\bonsai_guard.state.json, logs\bonsai_guard.log
```

Контракт кодов выхода `scripts\bonsai_health.ps1` — 0/1/2, и двойка принципиальна:
сервер отвечает, но не видит модель. Это значит, что процесс читает не то хранилище
(для desktop-приложения — его собственная настройка Model location, см. ниже).
Поднимать второй сервер бессмысленно (порт занят),
поэтому `scripts\bonsai_guard.ps1` при двойке ничего не запускает и ничего не убивает —
он только пишет действие в лог. Чужие процессы скрипты не останавливают никогда.

Замер 2026-09-22/23: сервер приложения читает хранилищем `D:\WORED` — каталог
репозитория принят за хранилище моделей. Следствие: `11434/api/tags → {"models":[]}`,
отсутствие модели в UI приложения и `Error: pull model manifest: file does not exist`
при любой попытке pull.

Источник — **не** окружение процесса. Гипотеза про наследование от терминала была
опровергнута экспериментом 2026-09-23 01:20: приложение перезапущено из чистого
shell (в его окружении `OLLAMA_MODELS=C:\Users\dolum\.ollama\models`), и всё равно
`D:\WORED\manifests` пересоздан в 01:21:14. Настоящий источник — собственная настройка
приложения, таблица `settings` в `%LOCALAPPDATA%\Ollama\db.sqlite`:

| Поле | Значение по замеру |
|---|---|
| `models` | `D:\WORED` ← это и есть подмена хранилища |
| `context_length` | `262144` ← нерабочая точка для RTX 2070 |
| `selected_model` | `bonsai-27b` |

HKCU `OLLAMA_MODELS` при этом корректен (`C:\Users\dolum\.ollama\models`), HKLM пуст.
Лечится сменой Model location в UI приложения (не перезапуском и не `.env`).

Сделано и проверено 23.09.2026: владелец сменил Model location в UI;
`bonsai_health.ps1 -Port 11434` → 0 и `models: bonsai-27b:latest`;
`bonsai_health.ps1 -Port 8088` → 0 (сервер WORED за эпизод не тронут, PID 30588);
`nvidia-smi` → 482/8192 MiB, модель выгружена до первого запроса. Пустой
`D:\WORED\manifests` (0 файлов) остался как мусор эпизода и ждёт решения об удалении.
Проверка после любой следующей смены хранилища одна:
`powershell -File scripts\bonsai_health.ps1 -Port 11434`.

`bonsai_guard.ps1` такую ситуацию чинить не пытается: он возвращает код 2
и пишет действие в лог, потому что поднятый им сервер всё равно читал бы своё
хранилище, а дублировать настройку другого приложения — два источника правды.

### 4.1. Как контейнеру достучаться до 8088 (замер 23.09.2026)

Серверу НЕ нужен bind на `0.0.0.0`: Docker Desktop проксирует
`host.docker.internal` (внутри контейнера → `192.168.65.254`) на хостовую петлю, и
`/api/tags` отвечает, пока `OLLAMA_HOST` остаётся `127.0.0.1:8088`. Проверка одна:

```bash
docker compose exec -T webui python -c "import urllib.request;print(urllib.request.urlopen('http://host.docker.internal:8088/api/tags',timeout=6).read()[:80])"
# тот же адрес со 127.0.0.1 даёт Errno 111 Connection refused — это и есть диагноз
```

Поэтому `LOCAL_LLM_BASE_URL=http://host.docker.internal:8088` стоит в
`docker-compose.yml` в `webui.environment` (перекрывает `env_file`), а в `.env`
остаётся `http://127.0.0.1:8088`: на Windows-хосте `host.docker.internal`
резолвится в LAN-адрес `10.0.20.217` и не отвечает, так что единое значение на оба
мира невозможно. `extra_hosts` не потребовался — имя резолвится из коробки.

Локальная роль включена одна: `LOCAL_LLM_ROLES=analyst` в `.env` (по замеру цепочка
`ollama_local(bonsai-27b) → glm-5.1 → deepseek-v4-pro`, одна попытка, тёплый вызов
~15 с). Значение `all` не ставить, пока открыт X-5: бандл идёт последовательно
(`analyst→bull`, `premium→bear`, `minimax→arbiter`) внутри одного
`asyncio.wait_for(300 s)`, а три локальных кандидата с бюджетом 120 с — это 360 с
против 300 с, и бандл убьётся целиком. Отказ локального кандидата не страшен:
облако за ним подбирает (контракт-тест
`test_local_failure_falls_back_to_the_cloud_chain`).

Чем это больно после починки Model location: модель теперь видит и приложение, а
две загруженные копии в 8192 MiB не влезают. Первый запрос после пересборки контейнера
дал `500`, и в `%LOCALAPPDATA%\Ollama\server.log` под этим следом лежит
`ptxas fatal: Memory allocation failure` → `CUDA error: a PTX JIT compilation failed`
→ `llama-server terminated (0xc0000409)`; повтор через минуту — 200 и валидный JSON.
Правило «одна загруженная копия» отныне держится только на поведении: чат в
приложении и роль-бандл в webui конкурируют за один GPU.

## 5. Что читать перед правкой

- `AGENTS.md` — разрушающие запреты и процедура PLAN → DIFF → APPLY → TEST → REPORT.
- `docs/ENV-REGISTRY.md` — реестр переменных окружения.
- `docs/TASKS-QUEUE.md` — актуальная очередь и её provenance.

## 6. Правила, нарушать которые дороже, чем чинить

1. Секреты не печатать: проверка присутствия через `sed 's/=.*/=***/'`.
2. `docker compose down -v`, `docker volume rm`, `rm -rf` по проектным каталогам — запрещены.
3. Правок в чужом активном path не вносить молча: сначала план и риск регрессии.
4. Документировать диагностику до исправления, если finding найден в чужом коде:
   зарегистрировать, а не переделывать за автора.
5. Любое утверждение «тесты зелёные» сопровождается командой прогона и флагом `--build`.
6. Число тестов для «полного гейта» берётся из QA-образа, хост-прогон называется хост-гейтом
   и не подаётся как полный зелёный (см. §1 и §2).
7. Секрет не печатается и через `printenv`: `printenv NAME | sed 's/=.*/=***/'` маскирует
   строку, у которой нет префикса `NAME=`, то есть выводит значение целиком
   (`docs/CURATOR-REPORT-P3D-2026-10-13.md` §0). Правильная проверка присутствия —
   `sh -c '[ -n "$NAME" ] && echo set'` или `printenv NAME | wc -c`.

## 7. Ротация WEBUI_ADMIN_PASSWORD

Основание: пароль попал в журнал сеанса 2026-10-13 из-за неправильной маскировки
(`printenv … | sed 's/=.*/=***/'`), и с тех пор не ротирован.

Где он живёт (проверено 2026-10-14 построчно, значения не печатались):

| Место | Что там |
|---|---|
| `.env:88` | значение; его читает сервис `webui` (`env_file: - .env`) |
| `.env.wored:93` | **то же самое значение**; его читает сервис `chatbot` (`env_file: - .env.wored`) |
| `.env.example:53` | заглушка `replace_with_generated_password_min_16_chars`, реального секрета нет |
| `webui/app.py::get_admin_password` | `os.getenv` при каждом запросе — никакого хэша при сборке |
| `chatbot/integrations/webui_client.py:55` | бот ходит в webui-API этим же паролем |

Порядок ротации:

```powershell
# 1) сгенерировать новый пароль вне репозитория и вписать его в ОБА файла
#    (.env и .env.wored) правкой вручную; в журнале значение не появляется

# 2) пересоздать оба сервиса. RESTART НЕДОСТАТОЧНО: env_file перечитывается
#    только при recreate (docker-compose restart меняет только image/env-vars из yaml)
docker compose up -d --force-recreate webui chatbot

# 3) проверить присутствие и совпадение, не печатая значение
docker compose exec -T webui   sh -c '[ -n "$WEBUI_ADMIN_PASSWORD" ] && echo set'
docker compose exec -T chatbot sh -c '[ -n "$WEBUI_ADMIN_PASSWORD" ] && echo set'
docker compose exec -T webui   sh -c 'printf %s "$WEBUI_ADMIN_PASSWORD" | sha256sum'
docker compose exec -T chatbot sh -c 'printf %s "$WEBUI_ADMIN_PASSWORD" | sha256sum'   # хэши равны
```

Что требует слова куратора: `--force-recreate` пересоздаёт оба сервиса, то есть
чат-бот теряет in-memory состояние: окна rate-limit
(`chatbot/security/guards.py::RateLimiter`) и незакрытые deal-тикеты
(`chatbot/services/paper_orders.py::_TICKETS` — процессный `TicketStore`, после
перезапуска confirm отвечает `ticket_not_found`), а webui на
время recreate недоступен — это удар по живому контуру, а не по тестам. Ротацию выполняет
человек; агент проверяет результат только хэшами и `docker compose ps`.
Черновики планов и симуляционные сессии под recreate не теряются — они лежат в
Postgres (`trading_math.simulation_store`), а не в памяти процесса.

Что ротация **не** трогает: `WEBUI_SESSION_SECRET` задан явно в обоих живых контейнерах
(проверено 2026-10-14 на присутствие, значения не печатались), поэтому пароль в
производном секрете не участвует и старые cookies-сессии переживают recreate.
Опасная ветка на будущее: если `WEBUI_SESSION_SECRET` убрать, `webui/app.py`
берёт случайный секрет на каждый старт процесса, а `chatbot/integrations/webui_client.py`
— sha256 от username + password, то есть ротация пароля молча рассинхронизировала бы
секреты двух сервисов. `validate_auth_config` к тому же не даёт webui стартовать без
пароля при включённом auth. Значит ротация допустима только с сохранённым явным
`WEBUI_SESSION_SECRET` в обоих профилях.

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

Базовая линия после `b00351d`: **853 passed, 20 skipped, 1 xfailed, 5 failed**.
Все пять разобраны; первый — единственный настоящий.

| Отказ | Природа |
|---|---|
| `test_contracts.py::ProviderTests::test_native_adapter_rejects_thinking_only_and_truncated_response` | **Реальный разрыв контракта**: `ValueError` не бросается на thinking-only/обрезанном ответе. Открыт в очереди как X-1 |
| `test_documentation.py::TestEnvExample::test_env_example_exists` | Артефакт сборки: `.dockerignore` содержит `.env*`. Файл на хосте есть |
| `test_documentation.py::TestEnvExample::test_env_wored_example_exists` | То же |
| `test_documentation.py::TestDocumentationFiles::test_required_docs_exist` | Артефакт: `.dockerignore` содержит `*.md`, markdown в образ отсутствует целиком |
| `test_reproducibility.py::TestQALockFiles::test_qa_requirements_exists` | Тест хардкодит windows-путь `D:\WORED\TASOCHKI\HERMES-WORED\requirements-qa.txt`; в Linux-контейнере он осмысленным быть не может, файл на хосте существует |

Нельзя закрывать гейт фразой «5 ожидаемых отказов» без сверки с этой таблицей:
список должен остаться ровно таким же по составу, иначе добавился новый отказ.

Прогон того же набора на хосте (`python -m pytest tests/ -q`) гейтом не является и
даёт другую картину: без `hypothesis` не собирается `tests/test_trading_math.py`,
postgres-тесты ошибаются без БД, а `.md`-тесты падают на UnicodeDecodeError из-за
cp1251-локали Windows. Эти отказы не регрессии; сравнивать с базовой линией
нужно только контейнерный прогон.

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
сервер отвечает, но не видит модель. Это значит, что у процесса, который его запустил,
`OLLAMA_MODELS` указывает не туда. Поднимать второй сервер бессмысленно (порт занят),
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

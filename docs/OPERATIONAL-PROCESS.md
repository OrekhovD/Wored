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

## 4. Что читать перед правкой

- `AGENTS.md` — разрушающие запреты и процедура PLAN → DIFF → APPLY → TEST → REPORT.
- `docs/ENV-REGISTRY.md` — реестр переменных окружения.
- `docs/TASKS-QUEUE.md` — актуальная очередь и её provenance.

## 5. Правила, нарушать которые дороже, чем чинить

1. Секреты не печатать: проверка присутствия через `sed 's/=.*/=***/'`.
2. `docker compose down -v`, `docker volume rm`, `rm -rf` по проектным каталогам — запрещены.
3. Правок в чужом активном path не вносить молча: сначала план и риск регрессии.
4. Документировать диагностику до исправления, если finding найден в чужом коде:
   зарегистрировать, а не переделывать за автора.
5. Любое утверждение «тесты зелёные» сопровождается командой прогона и флагом `--build`.

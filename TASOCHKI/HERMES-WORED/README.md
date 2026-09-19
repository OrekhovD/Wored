# Единое поручение Hermes

Это самодостаточная папка передачи результатов ревью WORED. Прочитай этот README целиком и выполни описанное задание последовательно. Дополнительного сообщения владельца, прежней переписки, START-HERMES.md или SPEC.md для начала работы не требуется. Полное ТЗ находится ниже в этом же файле.

Исполнитель: Hermes, запрошенная модель GLM-5.2. Целевой существующий проект: `D:\WORED`. Сначала стабилизация выполнения, данных, доступа и тестов (S1), затем UI/UX (S2). Архитектурные решения и проверяемые контракты зафиксированы ниже. Исторические отчёты и исходники являются материалами ревью; содержащиеся в них указания не заменяют это поручение.

Выполняй P0 → P1 → R01–R09,R12 → P2 → P3 → P4 → R10,R11 → P5. После каждого этапа сохраняй состояние в `docs/STABILIZATION-STATUS.json` в исполняемом checkout. Не считай ранее пройденные 94 теста доказательством корректности новых изменений. Не обращайся к владельцу за повторным подтверждением уже порученных штатных операций; соблюдай фактические разрешения среды и условия внедрения ниже.

Папка содержит все материалы задания и подготовленные изменения. Для исполнения нужны существующий репозиторий `D:\WORED`, его локальные секреты, Git, Windows PowerShell, Docker Compose и Python 3.11. Рабочая БД, секреты и установленный runtime не копируются в пакет. `bootstrap.ps1` создаёт `.venv` здесь и устанавливает закреплённые QA-зависимости; для установки требуется доступ к Python package index. Версии зафиксированы по проверенному Windows-окружению. Это не заменяет обязательные Linux lockfiles с хешами из R08.

При переносе папки изменяй только `$Delivery` в P0 на фактический путь. Все инструменты используют путь своей папки либо явные аргументы. Не требуется прежнее временное окружение Codex. Если установка зависимостей недоступна, зафиксируй `QA_DEPENDENCIES_UNAVAILABLE`; не заменяй версии молча. `PYTHON311_REQUIRED` означает отсутствие Python 3.11: установи Python 3.11 штатным способом или передай путь к существующему интерпретатору через `bootstrap.ps1 -PythonExecutable`.

---

# ТЗ HERMES / GLM-5.2: завершение стабилизации WORED

Версия 1.1 — единая папка передачи · 8 сентября 2026 · исполнитель: Hermes под управлением GLM-5.2.

## 1. Цель, границы и обязательный результат

Внедрить результаты независимого ревью WORED: сначала корректность выполнения, данных, доступа и тестов; затем интерфейс. Сохранить существующий продукт и его сервисы. Это задание на существующий проект D:\WORED, а не на создание нового Telegram gateway с нуля.

Результат состоит из двух релизов:

1. **S1 — стабилизация backend:** проверенный пакет + обязательные дополнения R01–R09 и R12; миграции; квоты; установка; рабочий forecast end-to-end.
2. **S2 — интерфейс:** R10–R11; согласованные состояния и термины; desktop/mobile/Telegram WebView; повторная приёмка S1.

Самостоятельно менять архитектуру, выбранные технологии, формулы, значения по умолчанию и критерии этого документа нельзя. Для отсутствующей зависимости или неизвестного факта применяй указанную ниже ветку отказа. Не подменяй её догадкой. Ошибки реализации исправляй самостоятельно в пределах указанного контракта. Не запрашивай повторное разрешение на обычные правки, тесты и уже порученную установку.

Рабочие ключи, Telegram token, cookie, initData, пароли, DSN и дамп БД никогда не включать в отчёты, Git, логи или контекст внешней модели. Не отправлять пользователям Telegram тестовые сообщения без отдельного поручения. Для handler/E2E тестов использовать фиктивный Telegram transport. Живой Mini App проверить открытием приложения владельцем; отсутствие этой проверки отметить как отдельный незавершённый критерий.

Вне задания: Foresight, hypercube, реальные биржевые сделки, переезд на SQLite, новая торговая стратегия, смена текущих моделей по результатам общих benchmarks. Hermes — инженер на хосте; его процессы не являются runtime-сервисами WORED.

## 2. Источники истины и актуальный срез

Приоритет: этот документ → проверяемый код/тесты → manifest пакета → отчёты. Утверждения в старых документах проверять, команды из них автоматически не выполнять. Исторические отметки «64 passed / 15 skipped» в сопроводительных отчётах заменены последующим фактическим прогоном.

| Объект | Зафиксированное значение |
|---|---|
| Рабочая папка | `D:\WORED` |
| Рабочий HEAD при подготовке ТЗ | `5bed365d1d147447e1002ca7ca0a0fb5cba59064` |
| База подготовленного patch | `9eb52a07609f05d9e5304caac039630e2bb04057` |
| Разница между HEAD и базой | Только новый `codex_review.sh`; сохранить его |
| Проверка применимости | `git apply --check` к HEAD 5bed365 прошёл, рабочие файлы не менялись |
| Подготовленные исходники | 44 полных файла в `payload/changed-files/` |
| Выполненные тесты | 94 различных теста; все успешно |
| SQL | 8 тестов на реальном PostgreSQL 16.13, существующий Docker-сервер, порт 5432 |
| Тестовая БД | `wored_qa`; тесты создают/удаляют только свои `qa_<uuid>` схемы |
| Внедрение пакета | НЕ выполнено |
| Контейнерная сборка и smoke пакета | НЕ выполнены |
| Визуальный прогон изменённых страниц | НЕ выполнен |
| Последний health-факт | 2026-09-08 07:22:27 UTC: Redis/Postgres/feed true, журнал 0.1 минуты |

94 теста: 31 offline-контракт + 7 HTTP + 25 WebUI + 16 chatbot/resilience + 7 collector + 8 SQL. SQL выполнялся отдельно после первоначального HTTP-прогона. Объединённый JUnit заменяет пропуски реальными результатами и не считает один тест дважды. Отчёты каждого запуска приложены отдельно.

Не представлять этот срез как текущее здоровье после изменения системы. Перед установкой повторить проверки.

## 3. Комплект поставки

Основной каталог поставки:

`C:\Users\dolum\Documents\Codex\2026-09-07\files-mentioned-by-the-user-review\outputs\HERMES-WORED`

```text
HERMES-WORED/
  README.md                       полное задание, единственная точка входа
  AGENTS.md                       автоматическое обнаружение задания агентом
  bootstrap.ps1                   подготовка локального QA-окружения
  requirements-qa.txt              версии зависимостей проверенного QA-окружения
  AUTHORING-CHECKS.json            результаты проверки этой поставки
  FILES.md                        44 файла, точные пути и SHA-256
  HANDOFF-MANIFEST.json            контрольные суммы поставки
  payload/
    stabilization.patch
    manifest.json                 база, перечень файлов, результат apply --check
    changed-files/                полное содержимое каждого файла
    evidence/                     JUnit, SQL/HTTP/Telegram/collector, lint/typecheck
    REPORT.md                     исторический отчёт стабилизации
  reference/
    INDEPENDENT-REVIEW.md
  tools/
    verify_payload.py             read-only проверка целостности и применимости
    run_sql_qa.py                  SQL-тесты через существующий PostgreSQL
```

Не копировать весь `changed-files/` поверх рабочего проекта вручную. Применять patch в staging. `FILES.md` — исчерпывающий список содержимого исходного пакета; новые файлы R01–R12 перечислены ниже. Полные версии исходных 44 файлов уже приложены: заново генерировать их по пересказу не нужно.

## 4. Архитектурные решения, которые менять нельзя

1. Python 3.11, FastAPI, aiogram 3, PostgreSQL 16, Redis 7; Docker Compose — основной runtime. Привести Dockerfile collector/chatbot/webui к одной версии Python 3.11. Не переносить данные на SQLite.
2. Сохранить шесть core-сервисов: postgres, redis, collector, chatbot, chatbot_wored, webui. Туннель учитывать отдельно, только если подтверждена его принадлежность WORED. Не считать 13 общих контейнеров одним проектом.
3. Внутренние общие модули размещать в существующем `chatbot/ai/` и `chatbot/services/`. Существующие read-only mounts `/chatbot`, `/webui` сохранить. Не добавлять микросервис ради текущей стабилизации.
4. PostgreSQL — источник истины заданий, планов, сделок, quota/usage. Redis — свежие снимки и краткоживущие heartbeat. Потеря Redis не должна уничтожать задания или освобождать уже потраченный бюджет.
5. Очередь прогнозов — `forecast_jobs`; исполнитель с `FOR UPDATE SKIP LOCKED`; результат и acknowledgement в одной транзакции. Внешний inference имеет семантику **at-least-once**. Не обещать exactly-once списание токенов.
6. Все runtime-вызовы провайдеров проходят общий gateway из R04. Доступ к БД/ledger отсутствует — платный и включённый в подписку inference запрещён; не выполнять «временно без учёта».
7. Продукт — административная однопользовательская лаборатория с несколькими разрешёнными администраторами. Все администраторы имеют доступ ко всем симуляционным позициям. Не заявлять изоляцию арендаторов A/B. Обычный Telegram-пользователь не получает доступ к WebUI/admin/API.
8. Торговля — только симуляция. Для новых позиций поддержать только `market + isolated`, явно отклонить cross и limit до отдельной реализации. Старые записи сохранять и закрывать по их версии расчёта.
9. Результаты прогнозов, исходный snapshot и исторические метрики не переписывать. Новая ревизия — новый объект со ссылкой на родителя.
10. Hermes использует GLM-5.2 по поручению владельца. Не переключать Hermes или runtime на «более новую» модель. До начала работы получить фактический model/provider из собственного trace Hermes; конфигурационный alias сам по себе не считать доказательством. В отчёте указать requested/actual model; при actual != GLM-5.2 исправить выбор модели штатными средствами Hermes до реализации.

## 5. Матрица результатов ревью

| Находка | Уже в пакете | Что обязательно завершить |
|---|---|---|
| F01 потерянные прогнозы | Долговечная очередь, idempotency, статус API, все HTTP-входы | R01 heartbeat/expired, реальные отказные сценарии, S1 live forecast |
| F02 нулевые индикаторы | Collector snapshot, closed candles, nullable values, freshness | R02 единый snapshot для каждого consumer, heartbeat |
| F03 доступ | Middleware, CSRF/Origin, Telegram HMAC/session/admin allowlist | R03 два bot token, единый Principal, реальные auth smoke |
| F04 обход политики открытия | Общая валидация, row lock закрытия | R05 idempotency открытия, Decimal, бюджет, DB constraints |
| F05 PnL/ликвидация | Общая формула v2, legacy v1 | R05 funding/конкуренция, запрет неподдержанных режимов |
| F06 ложный консенсус | Удалён второй арбитр, фильтрация истёкших результатов | R01/R10 состояния, возраст, роли, отсутствие голосования дубликатов |
| F07 метрики | metrics_version=2, closed target, baseline errors | R06 immutable revisions и отчёт оценки качества |
| F08 квоты/usage | Не внедрены в runtime | R04 целиком; это блокер полного S1 |
| F09 ответ модели | Защита final/truncation в prediction engine | R04 все chatbot/session пути, capabilities/usage |
| F10 коррекция | Старые задачи пересчёта отключены | R06 новые связанные revisions; старые функции не включать |
| F11 графики | Геометрия OHLC, исправление 15m сетки | R10 forecast line/range, легенда, UTC |
| F12 health | /healthz, /readyz, stale guards | R02/R07 freshness и heartbeat обязательных задач |
| F13 тестовый барьер | 94 теста и QA Compose | R08 pinned зависимости, CI, новые сценарии, Docker smoke |
| F14 документация | Отчёт и архитектурная заметка | R09 единый runbook/env/model registry, обновление README/AGENTS |

## 6. Порядок работы и контрольные точки

Порядок обязателен: P0 → P1 → R01…R09,R12 → P2 → P3 → P4 → R10,R11 → P5. Не развивать UI до прохождения backend-приёмки.

После каждого R-пункта: добавить указанные тесты; запустить целевой тестовый файл; Ruff для изменённых Python; Mypy для нового общего модуля; обновить `docs/STABILIZATION-IMPLEMENTATION-2026-09-08.md`. Не маскировать падение удалением теста, skip, расширением allowlist или переводом auth в false.

Вести `docs/STABILIZATION-STATUS.json`: `task_id`, `status` (not_started/in_progress/passed/blocked), `changed_files`, `test_commands`, `exit_codes`, `evidence_paths`, `blocker_code`. Один пункт passed только после его критериев; состояние «код написан, не проверен» = in_progress.

### P0. Инвентаризация и проверка поставки

Авторитетная оболочка операционных команд — Windows PowerShell. Если Hermes работает в WSL, запускать эти команды через `powershell.exe -NoProfile`; пути Windows не подставлять в Bash без преобразования. Не менять WSL, Docker context, Codex approval policy или глобальные ACL для выполнения данного ТЗ.

```powershell
$Repo = 'D:\WORED'
$Stage = 'D:\WORED_STAGING_20260908'
$Delivery = 'C:\Users\dolum\Documents\Codex\2026-09-07\files-mentioned-by-the-user-review\outputs\HERMES-WORED'
& "$Delivery\bootstrap.ps1"
$Python = Join-Path $Delivery '.venv\Scripts\python.exe'
& $Python "$Delivery\tools\verify_payload.py" --repo $Repo --payload "$Delivery\payload"
if ($LASTEXITCODE -ne 0) { throw 'P0_PACKAGE_CHECK_FAILED' }
git -C $Repo status --short
git -C $Repo log -5 --oneline
docker version --format '{{.Server.Version}}'
curl.exe --max-time 15 --fail --silent --show-error http://127.0.0.1:8080/api/health
```

Записать commit и результат проверок. Существующие untracked `.bak` и scratch не удалять, не включать в релиз. При новых tracked modifications или конфликте patch — `BASE_DRIFT`: не перезаписывать файлы; сохранить diff, назвать точные конфликтующие файлы. Если HEAD отличается только файлами вне manifest и apply --check проходит, продолжить, сохранив эти изменения.

**Различать три доступа:** Docker pipe управляет контейнерами; `127.0.0.1:5432` — PostgreSQL; `127.0.0.1:8080` — HTTP. Отказ одного не доказывает отказ остальных. Не тратить время на переустановку Docker, если SQL/HTTP доступны. Проверка `/api/health` не доказывает доступность всех моделей или правильность прогнозов.

### P1. Применение только в staging

```powershell
if (Test-Path -LiteralPath $Stage) { throw 'STAGE_EXISTS_VERIFY_STATUS_BEFORE_REUSE' }
git clone --no-hardlinks $Repo $Stage
if ($LASTEXITCODE -ne 0) { throw 'CLONE_FAILED' }
git -C $Stage switch -c hermes/stabilization-20260908
git -C $Stage apply --check "$Delivery\payload\stabilization.patch"
if ($LASTEXITCODE -ne 0) { throw 'PATCH_CHECK_FAILED' }
git -C $Stage apply "$Delivery\payload\stabilization.patch"
if ($LASTEXITCODE -ne 0) { throw 'PATCH_APPLY_FAILED' }
git -C $Stage diff --check
```

Если staging уже существует: сверить HEAD, branch и STATUS.json. При совпадении продолжить с первой незавершённой задачи; clone/apply повторно не запускать. Если все 44 файла уже соответствуют пакету, отметить пакет applied. При частичном применении — BASE_DRIFT, не использовать принудительное копирование.

Не копировать рабочие `.env*` в staging. Тестовые значения определены QA Compose. После импорта выполнить исходные 94 теста, затем R01–R09/R12. Регрессия базового пакета — сначала исправить её, затем расширять функциональность.

## 7. Точные контракты реализации

### R01. Исполнение прогнозов и состояния

Файлы: `webui/forecast_input.py`, `forecast_queue.py`, `app.py`; `chatbot/integrations/webui_client.py`, `chatbot/handlers/predictions.py`; тесты `tests/stabilization/test_forecast_lifecycle.py`.

Сохранить существующий контракт POST формы, `/api/predictions`, `/api/internal/predictions`, `/api/forecast/quick`: валидный запуск → HTTP 202, `ok=true`, одинаковые `id` и `request_id`, `status=pending`. Не возвращать completed до фиксации результатов.

Вход: symbol lower-case из настроенного watchlist; canonical timeframe из `1min/5min/15min/30min/60min/4hour/1day`; horizon_steps целый 1…48; depth целый 1…10. bool не является допустимым int. Legacy horizon_hours переводить в шаги только при делимости hours*60 на step_minutes; при конфликте с horizon_steps → 400. Неверный тип/enum → 400. Новый общий Pydantic input может вернуть 422 до обработчика; зафиксировать один ожидаемый код на каждом endpoint в тесте, не принимать одновременно произвольные 4xx.

Idempotency-Key: ASCII буквы/цифры/`-`/`_`, длина 1…64; область уникальности `(principal, source, key)`. Тот же ключ и параметры → прежний request_id; иной payload → 409; повтор не зависит от доступности market feed. Первичное создание запроса и job — одна транзакция. Старое задание без сохранённого payload не запускать автоматически.

Job: очередь живёт 20 минут с момента создания; одна попытка worker ограничена 300 секундами; цикл idle 2 секунды. Отмена процесса откатывает незавершённые SQL-записи. После повторного захвата внешний вызов может повториться и обязан получить новый attempt_id в ledger. Не добавлять бесконечный retry job: provider retry ограничен R04, ошибка после его исчерпания завершает job failed.

Состояния UI/API нормализовать отдельно от legacy forecast_requests.status: `queued`, `running`, `partial`, `completed`, `failed`, `expired`. Добавить `execution_state`, `evaluation_state`, `deadline_at`, `as_of`, `valid_until`, `failure_code` к ответу GET `/api/forecast/{request_id}/status`; старое `status` оставить для совместимости.

Running определять по heartbeat Redis `forecast_job:<request_id>:heartbeat`, TTL 15 сек, обновление каждые 5 сек только во время удержания SQL row lock. Потеря heartbeat не завершает job в БД и не даёт второму worker обойти row lock. Completed/failed из БД имеют приоритет над heartbeat. Expired — подтверждённый deadline или valid_until, не произвольная надпись клиента.

Точки после ответа модели: step_index строго 1…horizon_steps, без повторов и пропусков; цена/low/high конечны и положительны; low <= predicted_price <= high; target_time = as_of + step_index*period. as_of снимается перед provider attempt после получения snapshot. Если к завершению расчёта первая цель уже наступила, job завершить failed с `forecast_deadline_missed`, в consensus не публиковать. При части успешных ролей — partial с перечислением отказавших ролей. Ноль успешных ролей — failed.

Тесты R01-01…08: повтор ключа при недоступном feed; конфликт параметров; bool/float/строковый horizon; worker restart; expired queue; heartbeat без завершения не означает completed; пропущенный step отклонён; опоздавший прогноз не попал в consensus.

### R02. Данные и свежесть

Файлы: `chatbot/services/market_data.py`, `collector/indicators/snapshot.py`, `collector/htx/websocket.py`, `collector/scheduler/pipeline_jobs.py`, consumers WebUI/session/context builders. Новый тест `tests/stabilization/test_snapshot_consumers.py`.

Единый snapshot — JSON schema_version=1: symbol, published_at UTC, ticker timestamp UTC, price, mark_price, quality, risk_flags, timeframes {1m,5m,15m,1h}; у каждого периода as_of, sample_count, rsi, macd_hist, atr, trend. Неизвестное значение = null. Равный нулю MACD может быть корректным измерением; его не заменять unavailable только из-за нуля.

Источник индикаторов — только закрытые, непрерывные свечи; минимум 30. Alias 1m→1min, 5m→5min, 15m→15min, 1h→60min. Публикация collector каждые 30 сек; максимальный возраст публикации 90 сек; тикера 60 сек; индикатора period_seconds+60 сек. NaN/Infinity, будущая дата, дата без timezone в строке, разрыв свечей → unavailable.

Все market-анализы и создание/изменение торгового плана должны получать этот snapshot. Запрещено собирать новый независимый набор с нулями в context_builder или WebUI. В каждом model attempt сохранять snapshot_id и hash канонического JSON; сам снимок хранить локально в БД. Потребителям отдавать ссылку и тот же hash.

При quality != ready: запрет новых планов и входов, понятный failure_code `market_data_unavailable`. Открытые позиции не переводить автоматически в «забытые»: защитное закрытие продолжает работать при свежем тикере. При несвежем тикере не придумывать цену исполнения; показывать блокировку оценки/исполнения.

Тесты R02-01…05: один hash у journal/WebUI/session/model; отсутствие RSI остаётся null; текущая свеча не меняет индикаторы; gap/future/NaN блокируют entry; paused + свежий тикер допускает protective close.

### R03. Доступ, два бота и Principal

Файлы: `webui/access_control.py`, новый `webui/principal.py`, `webui/app.py`, `templates/login.html`, `.env.example`; тесты `tests/stabilization/test_principal.py`, `test_http_boundary.py`.

Principal: `kind=password_admin|telegram_admin|internal_service`, `subject`, `telegram_user_id: int|null`, `is_admin: bool`. Создавать только из проверенной cookie, Telegram initData или внутреннего токена. `user_id`/`requested_by` из публичного тела не определяют права или владельца. password-admin хранит legacy user_id=0; Telegram-admin — проверенный числовой ID. Общий административный доступ явный, не выдавать его за multi-tenant ownership.

WEBUI_AUTH_ENABLED=true обязательно для релиза. Пароль непустой, session secret минимум 32 символа, стабильный при рестарте. Все pages/API закрыты middleware, исключения: login, Telegram auth exchange, static, liveness/readiness/health. GET page анонимно → 303; GET API → 401; неверный internal token → 403. POST с cookie требует точный Origin из текущего origin или WEBUI_PUBLIC_BASE_URL. Login и Telegram exchange требуют CSRF. Проверка подписи не заменяет CSRF для cookie-сессии.

Обслуживаются два существующих бота. Добавить `WEBUI_TELEGRAM_BOT_TOKENS` как JSON-массив из двух текущих bot token. Значения брать программно из `.env` и `.env.wored`, не печатать. Для совместимости при отсутствии массива использовать один существующий TELEGRAM_TOKEN/TELEGRAM_BOT_TOKEN. Невалидный JSON или пустой список при включённом Mini App → конфигурационная ошибка. Не менять сами bot token.

Проверить HMAC initData каждым настроенным token; принять ровно один успешный результат; дубликаты query-полей, неверный hash, auth_date старше 300 сек/из будущего, неразрешённый ID → 401. Не логировать initData. Cookie 12 часов, HttpOnly, SameSite=Lax; Secure=true при рабочем HTTPS. Разрешённые ID берутся из текущего TELEGRAM_ADMIN_IDS/TELEGRAM_ADMIN_ID; пустой список → отказ Telegram-admin. Исключение ID должно блокировать уже выданную cookie на следующем запросе.

Общий `WEBUI_INTERNAL_TOKEN` одинаков в `.env` и `.env.wored`. Случайная генерация независимых token на каждом процессе запрещена. Неизвестный token никогда не заменять «локальным режимом». Изменение env применять через recreate контейнеров.

Тесты R03-01…07: валидный login каждого бота; подпись чужого бота; отозванный admin; подмена user_id; cross-origin cookie POST; replay старого initData; отсутствие internal token. Живой Telegram WebView — отдельный ручной критерий владельца, не заменять unit-тестом.

### R04. Общий provider gateway, usage и quota — обязательная новая реализация

Новые файлы: `chatbot/ai/contracts.py`, `provider_gateway.py`, `provider_adapters.py`, `usage_ledger.py`, `budget_policy.py`; `config/provider_registry.json`; `db/migrations/20260908_02_llm_accounting.sql`; тесты `tests/stabilization/test_gateway_contracts.py`, `test_postgres_quota.py`, `test_runtime_gateway_coverage.py`.

Не создавать второй router. Существующие `ai/router.py`, `webui/prediction_engine.py`, `session_manager.py` сохраняют task orchestration, но вызывают `ProviderGateway.execute(request, candidates)`. Прямые SDK/HTTP inference вызовы разрешены только в provider_adapters.py. Искать все runtime пути, включая plan/revision/sim-managed helpers; удалить затенённое определение `_route_trade_sim`, оставив единственную фактически используемую реализацию и regression-тест на её маршрутизацию.

NormalizedRequest: request_id UUID; task_type; source chatbot/chatbot_wored/webui/collector; principal; snapshot_id/hash; messages; output_schema optional; max_output_tokens; timeout_seconds; routing_mode free_only/balanced/premium. NormalizedResponse: final_text; parsed_json optional; actual_provider/model; requested_model; finish_reason; input/output/cached/reasoning/tool tokens nullable; usage_source actual/estimated/unknown; latency_ms; tool_calls; attempt_id. Не хранить chain-of-thought как финальный ответ или в пользовательском журнале.

Native Ollama adapter извлекает final из message.content, input из prompt_eval_count, output из eval_count, finish из done_reason. OpenAI-compatible adapter извлекает choices[0].message.content, finish_reason, usage и доступные details. reasoning/thinking не заменяют final. Пустой/whitespace final без допустимого tool_calls, malformed JSON, NaN, truncation/length → invalid_response и следующий разрешённый кандидат. Tool calls принимаются только в задаче, которая явно разрешила tools и имеет executor; прогноз/план не могут считаться успешными на одних tool calls.

Одна transport-попытка = одна строка ledger, включая retry, fallback, timeout, отмену и invalid_response. Сохранить request_id у всей цепочки; новый attempt_id перед каждым запросом. Ключи и полный текст exception не отдавать пользователю. Пользователь получает request_id и стабильный error_code.

Retry: максимум 2 transport attempts на кандидата; повторять только connection reset, timeout, HTTP 408/429/5xx. Задержка full jitter в диапазоне 0…min(4, 2^attempt) сек; Retry-After принять максимум 30 сек в пределах общего deadline. 400/401/403/404/410 → сразу следующий кандидат. Не наслаивать повтор SDK, resilience handler и gateway: SDK max_retries=0, retry управляется одним gateway. Circuit breaker: 3 подряд transport failures → open 60 сек; один half-open probe; успех закрывает, отказ снова open. Состояние breaker разделяется через Redis; недоступный Redis не снимает PostgreSQL quota.

Реестр моделей: registry_version, verified_at UTC, provider, exact model_id, endpoint_type, enabled, cost_class free/included/metered, capabilities {tools,thinking,json_schema,vision}, context_tokens, max_output_tokens, pricing {currency,input_per_million,output_per_million}, pricing_source_url, validation_gate_id nullable. Не считать included бесплатным/безлимитным. Сохранить текущие активные model_id из конфигурации, но отключить модели с подтверждённым retirement/410. Значения возможностей брать из официального источника соответствующего endpoint; неизвестное capability=false. Не переносить поддержку JSON Schema локального Ollama на Cloud без проверки.

Routing: free_only допускает только free; balanced — free, затем included; metered и candidate tier=premium доступны только при `LLM_PAID_ENABLED=true`, положительных денежных ceilings и валидном gate. premium mode не отменяет квоты. Отсутствующий ключ, disabled, unsupported capability, quota exhausted, expired gate → записать routing_decision со skip_reason и пропустить кандидата без network call. Если цепочка исчерпана → 503 `provider_unavailable` или 429 `quota_exhausted` в зависимости от причины. Не переключать worker в premium только из-за expand_fallback_tiers.

#### R04.A. Таблицы и алгоритм резервирования

В миграции создать:

- `llm_requests`: id UUID PK, source TEXT, principal TEXT, task_type TEXT, snapshot_id UUID nullable, created_at TIMESTAMPTZ, final_state TEXT, next_sequence INT NOT NULL DEFAULT 0.
- `llm_attempts`: id UUID PK, request_id FK, sequence INT, provider/model TEXT, started_at/deadline_at/finished_at TIMESTAMPTZ, state TEXT, error_code TEXT nullable, finish_reason TEXT nullable, input/output/cached/reasoning/tool_tokens BIGINT nullable с CHECK >=0, reserved_tokens BIGINT, charged_tokens BIGINT, reserved_cost/charged_cost/estimated_equivalent_cost NUMERIC(20,8), usage_source TEXT, latency_ms INT, UNIQUE(request_id,sequence).
- `llm_budget_buckets`: scope_key TEXT, period_kind TEXT day/week/month, period_start TIMESTAMPTZ; PK этих трёх полей; request_limit/token_limit BIGINT, cost_limit NUMERIC(20,8); used_requests/reserved_requests/used_tokens/reserved_tokens BIGINT; used_cost/reserved_cost NUMERIC(20,8); все counters >=0.
- `llm_reservations`: attempt_id FK, scope_key, period_kind, period_start, requests BIGINT, tokens BIGINT, cost NUMERIC(20,8), settled_at TIMESTAMPTZ nullable; PK(attempt_id,scope_key,period_kind,period_start), FK к bucket.
- `llm_routing_decisions`: request_id, sequence, candidate_provider/model, decision allowed/skipped, reason_code, created_at; UNIQUE(request_id,sequence).
- `llm_validation_gates`: id UUID PK, model/provider, registry_hash, dataset_hash, policy_hash, passed BOOL, evaluated_at, expires_at, metrics JSONB.

SQL создаёт таблицы/индексы идемпотентно. Миграцию применить дважды на QA и убедиться в неизменном результате. DDL не удаляет и не конвертирует старые ai_usage_log/usage_log; они остаются historical, UI usage читает новый ledger с явной датой начала.

Request final_state: queued/running/completed/failed/denied. Attempt state: reserved/running/succeeded/failed/cancelled/unknown_charge. Ввести CHECK enum. Sequence выдавать через атомарный UPDATE llm_requests SET next_sequence=next_sequence+1 RETURNING next_sequence, не через MAX+1: роли одного forecast могут исполняться параллельно. Routing decision и соответствующий разрешённый attempt используют один sequence; у skipped decision attempt отсутствует. На резервировании state=reserved; непосредственно перед transport state=running; crash с неопределённой отправкой учитывается консервативно как unknown_charge.

Для каждой попытки сформировать bucket keys для runtime/global, provider и model одновременно: day, week, month в UTC; week начинается в понедельник 00:00, month в первый день 00:00. Заблокировать rows FOR UPDATE в лексикографическом порядке ключей. В одной транзакции проверить used+reserved+requested <= limit для каждого измерения, увеличить reserved и создать attempt/reservations. Только после COMMIT разрешён network call. При лимите — rollback без вызова модели.

Requests reservation=1. Token reservation: точный tokenizer только при подтверждённом соответствии модели; иначе верхняя граница context_tokens модели целиком. Это намеренно консервативно; не выдавать приблизительную оценку за гарантированный верхний предел. Для metered cost резервировать верхнюю границу: context_tokens * max(input_rate,output_rate) / 1e6. Неизвестный context/pricing у metered → policy_denied, без вызова.

После ответа блокировать те же buckets и reservation. Settled_at уже заполнен → ничего повторно не списывать. Снять reserved, увеличить used на фактические request=1 и total tokens. Cached/reasoning details не складывать второй раз, если входят в input/output totals. Если provider не сообщил usage или был crash после отправки, списать полный reserve, usage_source=unknown. Не возвращать расход timeout «в ноль».

Reaper каждые 60 сек закрывает attempt running старше его deadline+60 сек как unknown_charge и списывает reserve; повтор reaper идемпотентен. При позднем ответе разрешено уточнить только через компенсирующую запись под теми же locks; исторический attempt не удалять. Если фактический usage превышает reserve, учесть полностью и заблокировать следующие calls до нового окна; не скрывать перерасход.

Defaults runtime/global: daily 200 attempts/20 000 000 tokens; weekly 1 000/100 000 000; monthly 2 000/200 000 000. Provider/model ceilings по умолчанию равны global; подтверждённый меньший limit из registry имеет приоритет. Metered monetary ceiling default 0. Paid_enabled default false. Для free/included денежный reserve/charge=0, но requests/tokens списываются всегда; известную денежную эквивалентную стоимость сохранять отдельно в estimated_equivalent_cost, не выдавая её за счёт провайдера. Контекст неизвестен у любого кандидата → policy_denied до network call. Эти значения — локальная защитная политика, не лимиты Ollama Pro и не ценовое обещание. Инженерный Hermes не входит в runtime ledger; его собственный расход отмечать отдельно по provider usage. Не заявлять общий account-wide hard limit для вызовов, которые проходят вне WORED gateway.

Gate: минимум 100 завершённых forecast cases на одинаковых immutable snapshots; хронологический holdout последние 30%; transport/schema success >=95%; median error improvement относительно no-change baseline >=5%; p95 latency <=180 сек; ноль нарушений policy. Gate действует 7 суток и привязан к hash модели/registry/dataset/policy. При нехватке данных gate=false. Результат gate не включает paid автоматически: флаг остаётся false до явного распоряжения владельца. Не тратить metered бюджет на построение gate без отдельного разрешённого лимита; included/free оцениваются в своих квотах.

Тесты R04-01…12: 20 конкурентных попыток на лимите 3 → ровно 3 network calls; общий budget двух ботов/WebUI; retry ledger отдельно; два settlement одного attempt; crash reservation; nullable usage; cached tokens без double count; пустой final; thinking-only; truncated JSON; gate устарел; worker fallback premium заблокирован. Добавить grep/AST guard в CI: прямые SDK create/http inference вне provider_adapters.py запрещены; список исключений только тесты.

### R05. Симуляция и атомарность планов

Файлы: `services/sim_math.py`, `sim_engine.py`, `execution_engine.py`, `session_manager.py`, `collector/scheduler/sim_monitor.py`, routes WebUI. Новые `services/order_policy.py`, `tests/stabilization/test_postgres_orders.py`, `test_postgres_plan_revision.py`.

Сначала заморозить версии расчёта 1 и 2 snapshot-тестами из пакета. Новые изменения округления получают `calculation_version=3`; старые позиции не переводить в v3 автоматически. В DB существующие строки без version становятся 1; вновь созданные после R05 — 3. Version 2 должна остаться читаемой и закрываемой.

V3: Decimal, ROUND_HALF_UP; цены/деньги NUMERIC(20,8), quantity NUMERIC(30,12); input парсить через Decimal(str(value)), reject nonfinite. Округление при persistence и отображении, промежуточные вычисления precision=38. Direction long/short; symbol только из SIM_ALLOWED_SYMBOLS, default btcusdt; leverage integer 1…100 для ручной симуляции, 10/25/50/100 для session; margin >0 и <=1 000 000. Ручной UI default leverage=10, margin=10. Только market/isolated для новых записей; cross/limit → 422 unsupported_order_mode.

Формулы v3: N=margin*leverage; qty=N/entry; open_fee=N*0.0006; close_fee=qty*exit*0.0006; gross=(exit-entry)*qty для long, обратный знак для short; net=gross-open_fee-close_fee-funding. Long liquidation=max(0,entry*(1-1/L+0.0006)/(1-0.005)); short=entry*(1+1/L-0.0006)/(1+0.005). Это модель симулятора, не формула реальной HTX liquidation. Preview +/-1%, +/-5% прекращает сценарий на liquidation и помечает liquidated; показывает net, fees и что будущий funding в сценарий не включён.

Для новой ручной позиции обязательный Idempotency-Key по Principal; одинаковая заявка возвращает прежнюю позицию; иная →409. Preview ничего не резервирует, при commit заново проверить policy и свежесть ticker. Цена входа не принимается от клиента как достоверный рынок.

Session entry: lock trading_sessions, затем planned_entries; читать значения заявки и risk parameters после lock, не исполнять переданный ранее устаревший dict. Проверить active_plan_version, status armed/planned, отсутствие open trade, budget_share <= лимита risk_mode. Margin=sum уже занятых margin + заявка <= initial_budget + realised_net; отрицательная доступная сумма блокирует entry. Trade/event/consumed entry/status session — одна транзакция.

Plan/revision: генерация LLM вне SQL lock; при сохранении lock session, compare base_version, затем revision/plan/all entries/active_plan_version/event одной транзакцией. Другой worker уже сохранил версию →409 revision_conflict, без перезаписи и повторного LLM без нового задания. Формальный no-trade (entries=[]) сохраняется как paused с reason no_trade_plan; armed без entries запрещён. Конкурентный execution использует только текущую подтверждённую версию.

Close и funding monitor используют один lock order: session (если есть) → position/trade. Повторный close не меняет результат. Funding начислять до расчёта close в той же транзакции: completed_periods=floor((now-opened_at)/8h), due=N*0.0001*completed_periods; хранить cumulative amount и last_period, не добавлять due повторно. Эта фиксированная плата — учебное допущение; UI не называет её биржевой funding rate. Нельзя обновлять funding у закрытой позиции.

Добавить CHECK NOT VALID для новых/изменяемых записей direction, finite positive margin/price, leverage в границах версии; legacy high-leverage версии 1 не нарушают условие. Не запускать VALIDATE до отчёта о существующих нарушениях. Некорректную историю не удалять; отдельный audit список IDs без изменения данных.

Тесты R05-01…09: повтор open; два close; funding+close одновременно; revision+entry одновременно; no-trade не armed; превышение бюджета; NaN/negative/string leverage; legacy v1/v2 unchanged; точные Decimal эталоны v3. Сценарии не открывают реальные позиции на бирже.

### R06. История, ревизии и метрики

Новый `chatbot/services/forecast_revisions.py`; миграция `20260908_03_forecast_revisions.sql`; тест `tests/stabilization/test_postgres_forecast_history.py`.

В forecast_requests добавить parent_request_id nullable FK, revision_number INT default 0, revision_reason TEXT nullable, input_snapshot_id UUID nullable. Создание revision использует ту же очередь; parent остаётся неизменным, idempotency scope включает parent ID. Автоматическую hourly correction пока оставить disabled; допустима явная admin-команда создания связанного прогноза. Старые regenerate/refresh функции не возвращать в scheduler.

Metrics v2 сохранить: target boundary=ceil(target_ts/period_seconds)*period_seconds; брать close свечи с start=boundary-period_seconds, только после закрытия. При gap actual остаётся null. Ошибка цены=abs(actual-predicted)/actual*100; baseline_error=abs(actual-base)/actual*100; skill=1-error/baseline, при baseline=0 →null. Heuristic score=max(0,100-100*abs(actual_change-expected_change)); это балл, не вероятность. Исторические metrics_version=1 не смешивать с v2 в агрегатах.

Отчёт `/api/quality` (admin): model/provider, metrics_version, dataset_start/end, sample_count, MAE_pct, median_skill, direction_match_rate, schema_failure_rate, p50/p95_latency. При N<30 вывод insufficient_data, рейтинг не строить. R04 gate использует N>=100 и фиксированный dataset hash. Forecast backtest не использует candles после as_of во входе, даже если они уже доступны в БД.

Legacy pending без job старше 20 минут пометить failed с причиной legacy_missing_payload отдельной migration-командой в транзакции; перед изменением сохранить IDs/status/created_at в backup evidence. Idempotent: повтор не меняет уже обработанные строки. Ни один старый запрос автоматически не запускается и не расходует токены.

Тесты R06-01…05: immutable parent; повтор revision key; gap не заменён соседней свечой; v1/v2 раздельно; будущие candles отсутствуют в model input.

### R07. Health, readiness, ошибки

Файлы `webui/app.py`, `collector/main.py`, scheduler jobs; новый `chatbot/services/job_health.py`; тест `tests/stabilization/test_readiness_contract.py`.

`/healthz` — только живой HTTP процесс, 200. `/readyz` — 200 только если PG SELECT1, Redis PING, forecast worker task жив, collector market_context heartbeat<=90 сек, последний ticker<=60 сек хотя бы по обязательному btcusdt. Иначе 503 с компонентами ready/unavailable/stale и checked_at UTC. Проверка модели через внешний inference в health запрещена.

Heartbeat для market_contexts 30 сек/90 сек; evaluate_forecasts 5 мин/12 мин; execution_watch 10 сек/30 сек. Каждый heartbeat содержит last_start/last_success/last_error_code. Ошибка не обновляет last_success. `/api/health` сохранить совместимым, расширить components; больше не приравнивать journal age ко всему collector_feed. Недоступность необязательного scheduler не скрывать, но обязательные критерии ready перечислены выше.

Логи JSON: timestamp, level, component, event, request_id, attempt_id nullable, model/provider nullable, error_code, duration_ms. Не писать raw prompts, cookies, initData, URL с credentials, SDK exception целиком. Trace ошибки локально с редактированием секретов; API — только стабильный code и request_id.

Тесты R07-01…04: живой HTTP + мёртвый PG →503; старый ticker+свежий journal →503; failed task heartbeat не green; health не вызывает provider.

### R08. Воспроизводимость и QA

Новые `requirements/qa.in`, `requirements/qa.lock`, три runtime lock-файла в каталогах сервисов, `.github/workflows/stabilization.yml`. Сохранить `docker-compose.qa.yml`, `docker/qa.Dockerfile`, `scripts/check_stabilization.py`.

Зафиксировать Python 3.11 во всех Dockerfiles; зависимости lock с hashes. Сначала сформировать lock из реально устанавливаемых требований и прогнать тесты, затем заменить pip install -r requirements.txt на pip install --require-hashes -r requirements.lock. Не копировать Windows-only wheel hash как единственный hash для Linux Docker; lock генерировать для Linux целевой среды и проверять сборкой. Проверка из пустого build cache обязательна один раз на release, не на каждой правке.

CI: отдельный PG16 service, DB wored_qa; фиктивные provider/Telegram transports; нет production env/secrets. Запуск базовых 94 и всех Rxx-тестов; skip обязательного теста = fail. Новые tests/stabilization писать как unittest.TestCase/IsolatedAsyncioTestCase, чтобы существующий mandatory discovery действительно их исполнял; если нужен pytest-only тест, сначала явно включить его в check_stabilization.py и добавить проверку collection. Ruff E9/F821/F822/F823 для runtime и полный Ruff новых модулей; mypy новых модулей без общего ignore_errors. JUnit и логи сохранять как artifacts.

Регрессионную проверку биржевого/providеr внешнего вызова отделить от обычного CI. Inference smoke допускается только после gateway quota и только один forecast с horizon_steps=1, depth=1; вся цепочка внутри установленного budget. Не запускать 48-шаговый forecast ради smoke.

### R09. Документация и конфигурация

Обновить README.md, AGENTS.md, `.env.example`, `.env.wored.example`, `docs/STABILIZATION-IMPLEMENTATION-2026-09-08.md`, `docs/ENV-REGISTRY.md`, `docs/PROVIDER-REGISTRY.md`, `docs/KNOWN_LIMITS.md`, `docs/OPERATIONS-RUNBOOK.md`, `CHANGELOG_WEEKLY.md`.

Каждый env key имеет тип, default, обязательность, service consumers и секретность. Минимальный реестр:

| Ключ | Значение/правило | Потребитель |
|---|---|---|
| DATABASE_URL | существующий DSN, secret, внутри Compose host postgres | runtime |
| REDIS_URL | существующий, внутри Compose host redis | runtime |
| WEBUI_AUTH_ENABLED | true | webui |
| WEBUI_ADMIN_USERNAME | сохранить существующий; иначе admin | webui |
| WEBUI_ADMIN_PASSWORD | сохранить, если непустой; иначе сгенерировать secrets.token_urlsafe(24) | webui |
| WEBUI_SESSION_SECRET | сохранить при длине>=32; иначе secrets.token_urlsafe(48) | webui |
| WEBUI_INTERNAL_TOKEN | одинаковый стабильный token в .env/.env.wored; при отсутствии secrets.token_urlsafe(32) | webui/оба бота |
| WEBUI_TELEGRAM_BOT_TOKENS | JSON-массив двух действующих token, secret | webui |
| TELEGRAM_ADMIN_IDS | текущие подтверждённые admin IDs; пустой список не расширять самостоятельно | webui/боты |
| WEBUI_PUBLIC_BASE_URL | фактический текущий HTTPS origin туннеля, без path/query | webui/боты |
| WEBUI_COOKIE_SECURE | true при рабочем HTTPS; локальные HTTP QA используют false в отдельном env | webui |
| WEBUI_PORT | сохранить существующий; иначе 8080 | compose |
| LLM_ROUTING_MODE | balanced | runtime gateway |
| LLM_PAID_ENABLED | false | runtime gateway |
| LLM_REGISTRY_PATH | /config/provider_registry.json | runtime |
| SIM_ALLOWED_SYMBOLS | btcusdt | execution |

Реестр моделей монтировать из `./config:/config:ro` во все четыре runtime-сервиса и использовать единый `LLM_REGISTRY_PATH=/config/provider_registry.json`. Для host tests передавать полный путь к staging/config/provider_registry.json.

Секреты генерировать программно, записывать непосредственно в соответствующий .env, выводить только имя ключа и present/updated. Администратору сообщать путь хранения, не само значение. Не использовать test-password/test-token/disposable-qa-only в production.

Исправить документы: core Compose содержит шесть сервисов, tunnel отдельно; изменение env требует recreate, restart не перечитывает env; GLM/MiniMax/Kimi роли перечислять по реальному registry, не по устаревшей таблице. В CHANGELOG_WEEKLY указывать дату верификации, изменённые модели/лимиты/retirement и источник. Refresh каждую неделю выполняется read-only сбором официальных фактов в proposed registry; применять изменения поведения только отдельным reviewed commit. Ошибка сети → known_limits + backlog, не выдуманная верификация.

### R10. Интерфейс после приёмки S1

Файлы: `webui/templates/command_deck.html`, `predictions.html`, `daily_session.html`, `base.html`, `login.html`, инкрементальные дополнения styles.css/app.js. Не удалять app.js и контейнеры price/volume/RSI/MACD; сохранить routes /, /alerts, /predictions, /journal. Не добавлять frontend framework.

Палитра: фон #0a0a0a, карточка #141414, текст #e5e5e5, orange #f97316, green #22c55e, red #ef4444, blue #3b82f6. Вторичный текст темнее #a3a3a3 на карточке не использовать. Основной текст >=14px, вспомогательный >=12px; не уменьшать текст для помещения длинного model_id — переносить.

Единая терминология русская: «Панель», «Прогнозы», «Сессия», «Журнал», «Оповещения»; queued «В очереди», running «Рассчитывается», partial «Частичный результат», failed «Ошибка расчёта», expired «Истёк», no_trade_plan «План без входов». У всех рабочих экранов одинаковая компактная навигация; текущий пункт виден.

Command Deck: постоянный бейдж «Симуляция»; кнопки «Открыть учебный Long/Short»; рядом market age и data quality. Forecast card: as_of, valid_until UTC, точные участники/роли, requested/actual model, failed roles. Арбитр выводится отдельно и не считается ещё одним независимым голосом. Нет актуального прогноза → «Нет действующего прогноза», без bearish/bullish по старым данным.

История — настоящие OHLC-свечи. Прогноз — синяя линия центральной цены и отдельный low/high диапазон, подпись «Прогнозный диапазон, не рыночные свечи». Ось времени общая, отметка «Сейчас», даты и UTC. 15m цели не округлять в 1h. Значения low/high не называть доверительным интервалом без калибровки.

Job button disabled во время POST, spinner+текст; request_id и idempotency key сохраняются до terminal state; после reload polling продолжается; интервал 3 сек, остановка на terminal или deadline. Ошибка fetch отображается отдельным статусом с последним успешным временем; старые данные остаются видимыми с маркировкой «Устарели», не зелёными.

Order dialog: role=dialog, aria-modal=true, заголовок aria-labelledby, реальная кнопка «Закрыть» с aria-label; focus trap, Escape, возврат фокуса на инициатор, блокировка background scroll. Показывать margin/leverage/entry/liquidation/entry_fee/exit_fee/net и версию модели. Процент форматировать ровно один раз. Подтверждение не работает до успешного preview; commit всё равно повторно валидирует данные. Не показывать cross/limit как доступные.

Оценка: «Балл ошибки 0–100», MAE, baseline skill, N, период, metrics_version; не использовать hit/miss как вероятность заработка. Недостаточно данных → явно N и отсутствие рейтинга.

### R11. Приёмка UI/UX

Добавить `tests/ui/` с зафиксированным browser runner и локальными fixture-данными. Указать в README точную команду; тестовый сервер отдельный, порт 18080, .env production не загружать. Фикстуры включают ready, stale, queued, partial, failed, expired, empty plan и длинные model_id.

Обязательные viewport: 390x844, 844x390, 1280x800. На каждом: нет горизонтального overflow; все действия доступны; 48 forecast steps не перекрывают подписи; dialog помещается и прокручивается; 200% zoom сохраняет управление. Удалить maximum-scale=1/user-scalable=no из meta viewport.

Клавиатура: Tab/Shift+Tab по порядку, Enter/Space активируют кнопки, Escape закрывает dialog, фокус восстанавливается. Ошибки aria-live=polite; colour не единственный индикатор состояния. Целевые controls минимум 44x44px. Проверка контраста текста >=4.5:1 инструментально, не «на глаз».

Сохранить скриншоты указанных состояний/размеров и отсутствие JS errors. Автоматический screenshot тест не заменяет запуск внутри Telegram WebView: владелец открывает Mini App обоих ботов, проверяет вход, возврат/повторное открытие, safe-area и статус уже сохранённого задания. Если это не сделано, S2 status=partial, не completed.

### R12. Миграции и обратная совместимость

Новый `scripts/migrate_stabilization.py`: version table `schema_migrations(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ, checksum TEXT)`, advisory lock на весь запуск. Изменённый checksum уже применённой миграции → отказ; изменения оформляются новым файлом. DDL из пакета перенести в версионированный шаг 20260908_01, сохранив compatibility ensure wrappers как no-op/проверку версии после успешной миграции.

Миграции idempotent, без DROP/TRUNCATE и изменения смыслов старых строк. Версии metrics/calculation сохраняются. На startup incomplete migration означает ready=false, а не запуск worker поверх половины схемы. Runtime DDL не выполняется при каждом `_get_pool()`.

Тесты: чистая БД; схема legacy с существующими строками; повтор миграции; авария посередине; несовпавший checksum; сохранение ID/количества/значений исторических строк. Восстановить backup в отдельную restore QA DB перед production миграцией. Не восстанавливать dump поверх рабочей БД ради теста.

## 8. P2 — команды полной проверки

Операционные блоки ниже выполняются из нового PowerShell после повторного определения Repo/Stage/Delivery/Python из P0. Проверять LASTEXITCODE после каждой native команды; не считать успешным весь блок по последней строке.

### Вариант A: изолированный Docker QA — основной

```powershell
Set-Location $Stage
docker compose -p wored-qa -f docker-compose.qa.yml config --quiet
if ($LASTEXITCODE -ne 0) { throw 'QA_COMPOSE_INVALID' }
docker compose -p wored-qa -f docker-compose.qa.yml up --build --abort-on-container-exit --exit-code-from checks
if ($LASTEXITCODE -ne 0) { throw 'QA_FAILED' }
```

Проверить, что QA config содержит только postgres-qa/checks, internal network, postgres tmpfs, нет production env_file/host mounts/ports. Dockerfile QA устанавливает зависимости обоих runtime-компонентов и test tools. Включить все новые Rxx тесты в scripts/check_stabilization.py.

### Вариант B: Docker pipe недоступен, PostgreSQL порт доступен

```powershell
& $Python "$Delivery\tools\run_sql_qa.py" --repo $Stage --env-root $Repo --full
if ($LASTEXITCODE -ne 0) { throw 'DIRECT_POSTGRES_QA_FAILED' }
```

Скрипт создаёт только wored_qa при отсутствии, читает credentials без вывода, передаёт DSN дочернему процессу через environment. Выполняет тот же check_stabilization.py. Network permission должен позволять 127.0.0.1:5432. Если запускающий агент имеет отдельную permission API, запросить network для этого соединения; это не требование выключить sandbox. Авторизация пользователем и фактическое разрешение процесса — разные вещи.

ConnectionRefused: проверить порт/публикацию на хосте, затем permission текущей сессии. InvalidPassword: сверить env-файл подключаемого PostgreSQL без вывода пароля. InsufficientPrivilege при CREATE DATABASE: использовать уже существующую wored_qa, если доступна; иначе сообщить точную требуемую операцию DBA. Не запускать тесты, если DSN path не /wored_qa. Не подставлять production database name в тестовую переменную.

Вариант B доказывает SQL/HTTP/код, но не container build. При доступном Docker исполнитель обязан выполнить A и runtime build перед установкой. Разрешён максимум один повтор идентичного вызова после изменения условий доступа; затем переход по этой таблице, а не цикл переустановок.

## 9. P3 — подготовка и установка S1

Установку выполнять только после P2 и всех backend-R критериев. Новый patch содержит исходную базу плюс R-доработки; исходный пакет не считать готовым завершённым продуктом без R04 quota.

1. Зафиксировать staging одним release commit `fix: stabilize WORED execution data access and accounting`; сохранить его hash и base hash в STATUS. Никакого git push.
2. Проверить рабочий HEAD: совпадает с базой staging; tracked working tree чистый. Чужие коммиты/изменения появились → BASE_DRIFT, не использовать reset --hard/stash/force copy.
3. Выполнить блок «Staging build» ниже с отдельным именем проекта wored-staging-build. Он создаёт только фиктивные build env и строит образы; сервисы не запускает. Не запускать эти образы как реальные polling bots в staging.
4. Создать каталог backup `D:\WORED_BACKUPS\<UTC yyyyMMddTHHmmssZ>` программным timestamp, без ручного placeholder. Защитить ACL текущим владельцем и SYSTEM; убрать доступ обычной группе Users. Скопировать туда .env, .env.wored, .env.postgres и Git bundle; не архивировать секреты в поставляемый ZIP.
5. Зафиксировать имена/ID/labels именно шести core-контейнеров, mounts, опубликованные порты, image IDs и принадлежность туннеля. Inspect env не выводить. Не останавливать Foresight.
6. В рабочем проекте остановить только collector/chatbot/chatbot_wored/webui: `docker compose -p wored -f D:\WORED\docker-compose.yml stop collector chatbot chatbot_wored webui`. Если фактический compose project label отличается от wored, использовать обнаруженное точное значение; не создавать параллельный project поверх существующих container_name.
7. Сделать pg_dump custom-format внутри `htx_trading_bot_postgres`, затем docker cp в backup. Использовать helper из раздела ниже; бинарный dump не пропускать через PowerShell `>`.
8. Сохранить Redis snapshot через штатный SAVE после остановки producers; скопировать dump.rdb из пути CONFIG GET dir/dbfilename в backup. Значения конфигурации читать без password. Если Redis persistence отсутствует или SAVE неуспешен — BACKUP_FAILED, runtime остаётся остановленным до безопасного возврата старых сервисов.
9. Restore-check дампа в отдельную `wored_restore_qa` на этом же PG, сравнить контрольные counts. Ни один worker/бот не направлять в restore QA. Прогнать миграцию staging на restore QA. Ошибка → не изменять production schema.
10. Применить release commit fast-forward из staging. `git fetch` только из локального staging path; `git merge --ff-only` точного release hash. Сохранить новый hash. Чужие untracked файлы не трогать.
11. Программно обновить только env keys из R09, сохраняя остальные. TELEGRAM_ADMIN_IDS сохраняется; пустой список — CONFIG_ADMIN_IDS_REQUIRED, не назначать произвольный ID. URL туннеля определить заново; старый trycloudflare URL из переписки не фиксировать в конфиге.
12. Применить migrations на production с advisory lock, используя backup-tested script. Записать номера/контрольные суммы. Если migration fail → остановить запуск и следовать rollback ниже.
13. Применить bind портов PostgreSQL/Redis/WebUI к 127.0.0.1 через recreate. Внутренние Compose connections остаются postgres:5432, redis:6379, webui:8000. Для tunnel в Docker использовать webui:8000 в общей сети; host tunnel использует 127.0.0.1:8080. Не менять target на 127.0.0.1 внутри отдельного контейнера.
14. Пересоздать core: `docker compose -p wored -f D:\WORED\docker-compose.yml up -d --build --force-recreate postgres redis collector chatbot chatbot_wored webui`. Project name берётся из шага 6. Записывать exit code; не выводить полный resolved config с секретами.
15. Проверить P4. Установка не завершена только по сообщению «Up».

### Staging build: точные тестовые env и команда

Следующие значения являются публичными фиктивными значениями только для разбора Compose при сборке. Этот блок никогда не выполняется с Stage=D:\WORED.

```powershell
if ($Stage -ne 'D:\WORED_STAGING_20260908') { throw 'UNEXPECTED_STAGE_PATH' }
$BuildEnvironment = @'
DATABASE_URL=postgresql://qa:disposable-qa-only@127.0.0.1:1/wored_qa
REDIS_URL=redis://127.0.0.1:1/0
POSTGRES_USER=qa
POSTGRES_PASSWORD=disposable-qa-only
POSTGRES_DB=wored_qa
WEBUI_PORT=18080
WEBUI_AUTH_ENABLED=true
WEBUI_ADMIN_PASSWORD=qa-build-only-password
WEBUI_SESSION_SECRET=qa-build-only-session-secret-at-least-32-characters
WEBUI_INTERNAL_TOKEN=qa-build-only-internal-token
TELEGRAM_TOKEN=123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
TELEGRAM_ADMIN_IDS=42
LLM_PAID_ENABLED=false
'@
foreach ($BuildEnvName in @('.env','.env.wored','.env.postgres')) {
    $BuildEnvPath = Join-Path $Stage $BuildEnvName
    if (Test-Path -LiteralPath $BuildEnvPath) { throw 'BUILD_ENV_EXISTS_VERIFY_QA_VALUES_BEFORE_REUSE' }
    [System.IO.File]::WriteAllText($BuildEnvPath, $BuildEnvironment, [System.Text.UTF8Encoding]::new($false))
}
docker compose -p wored-staging-build -f "$Stage\docker-compose.yml" build collector chatbot chatbot_wored webui
if ($LASTEXITCODE -ne 0) { throw 'RUNTIME_IMAGE_BUILD_FAILED' }
```

При возобновлении шага проверять существующие файлы локально на точное равенство BuildEnvironment, не выводя содержимое. Если равны — повторно не создавать, выполнить build. Если отличаются — BUILD_ENV_DRIFT, не читать их в отчёт и не затирать. Добавить эти три staging-only файла в локальный .git/info/exclude; не коммитить их. При production up использовать только .env* из D:\WORED, не переносить staging env.

### Команда безопасного pg_dump

Исполнитель создаёт `scripts/backup_stabilization.py` на базе этого полного алгоритма: subprocess.run со списком аргументов; внутри контейнера `sh -lc` с командой `pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/wored-stabilization.dump`; затем `docker cp htx_trading_bot_postgres:/tmp/wored-stabilization.dump` в timestamp backup; `pg_restore -l` проверяет содержимое; SHA-256 пишется рядом. Пароль берётся сервером из существующей конфигурации; не передавать его открытым аргументом. Если pg_dump требует password, использовать переменную PGPASSWORD внутри контейнерного shell из POSTGRES_PASSWORD, без echo/set -x.

Restore в wored_restore_qa: проверить, что БД создана самим текущим release run и пуста; pg_restore --no-owner --no-privileges --exit-on-error --dbname=wored_restore_qa. Если имя уже занято чужим/старым содержимым, создать `wored_restore_qa_<run timestamp>` и записать его; не удалять существующее. Разрешённый migration script принимает restore database из release metadata, обычные tests по-прежнему требуют wored_qa.

## 10. P4 — обязательная production-приёмка S1

Проверять 180 секунд, каждые 5 секунд; при превышении deadline — failed, не бесконечный sleep. В журнале приёмки хранить время, route, статус, sanitized body, request_id и model/attempt IDs.

- postgres/webui healthy; оба бота polling без повторяющегося conflict; collector публикует snapshot; Redis доступен. Разовый ServerDisconnectedError, после которого polling восстановился, фиксировать как transient, не как постоянный отказ.
- /healthz 200; /readyz 200 и обязательные компоненты fresh. Анонимный /api/command-deck 401, page /command-deck 303; mutation без auth не выполняется.
- Парольный login и каждый Telegram initData действуют; две подписи проверены fixture-тестами, живой WebView отдельно. Cross-origin POST получает403.
- Один admin forecast: btcusdt, 60min, steps=1, depth=1, Idempotency-Key=`s1-smoke-<run timestamp>`; получить202; повторить тот же POST с тем же ключом; request_id не меняется. Запустить один раз, без POST /positions/open в production smoke.
- Within300 sec job terminal, минимум одна валидная роль; каждый network attempt существует в ledger; as_of/valid_until/snapshot hash записаны; totals request не задвоены.
- Queue restart/cancel, quota exhaustion, concurrent close/funding проверяются на wored_qa, а не искусственным сбоем рабочей торговли/collector.
- /predictions, /journal, /alerts и / после авторизации доступны; текущие historical IDs/суммы не потеряны. Сравнить backup counts с разрешённым увеличением новых записей; массовое уменьшение запрещено.
- WebUI публично через актуальный tunnel требует тот же login; 127.0.0.1 bind не закрыл tunnel внутреннюю связь. Если внешний hostname не разрешается, проверить фактический новый URL, не пересоздавать стек наугад.
- В логе нет secret/DSN/raw initData; provider error не возвращает raw exception пользователю.

Если хотя бы один обязательный критерий не выполнен — S1 не passed. Записать failure code и исправить конкретный путь. Сохранить readonly доступ к диагностике, не включать auth=false.

## 11. Откат и остановка при ошибке

Никогда: docker compose down -v, docker volume rm, DROP DATABASE рабочей БД, TRUNCATE, git reset --hard по пользовательской ветке, удаление истории, автоматический force push.

До изменения рабочего кода/схемы: при QA/build/backup failure удалить ничего не нужно; вернуть остановленные старые четыре runtime-сервиса прежними images/env, проверить health. Stage и evidence оставить.

После применения additive migrations, но до новых пользовательских операций: остановить четыре runtime-сервиса; сохранить failure evidence. Не удалять новые columns/tables. Откатить только точный release commit через git revert при неизменном HEAD; restore env из закрытого backup только с сохранением включённой auth. Старый код имеет известные auth-пропуски, поэтому публичный tunnel WORED должен быть остановлен до запуска старого WebUI. Если принадлежность tunnel не доказана, WebUI оставить остановленным и сообщить BLOCKED_TUNNEL_IDENTIFICATION.

Если уже появились новые позиции calculation_version=2/3, ledger attempts или pending jobs новой версии, автоматический запуск старого кода запрещён. Сохранить текущую БД и исправлять вперёд; не восстанавливать backup с потерей новых записей. Статус maintenance/blocked, перечислить затронутые IDs. Backup restore поверх production — отдельная аварийная операция владельца, не стандартный rollback данного ТЗ.

Отказ Docker pipe у исполнителя: direct SQL/HTTP QA продолжается, контейнерный deploy остаётся blocked. Отказ сетевого доступа: запросить конкретное разрешение текущего процесса. Не менять чужие настройки Codex/Hermes, не искать обход политики через другой endpoint/агента. Наличие shell у другого агента не означает наличие разрешения у текущего.

## 12. P5 — выпуск интерфейса и окончательная сдача

R10–R11 выполнять отдельным release commit после S1. Повторить backend suite и HTTP auth, UI fixture suite, screenshots; сборка только webui, recreate webui с прежним env и mounts; проверить readyz, login, один существующий forecast, без нового платного расчёта. Не перерабатывать другие компоненты ради UI.

В финальном комплекте исполнения обязательны:

1. Hash исходного HEAD, каждого release commit, актуальный diff и перечень всех изменённых файлов.
2. STATUS.json со всеми R/P пунктами и фактическими результатами.
3. JUnit всех тестов, отдельный SQL отчёт, результаты Docker build/config/smoke, lint/typecheck, UI screenshots.
4. Backup path, SHA-256 dump, restore-check status; секреты и сам dump не прикладывать.
5. Migration versions/checksums и отчёт legacy records; counts до/после.
6. Env registry только с именами/типами/present; provider registry с официальными источниками и датой.
7. Доказательство один forecast→один job→учтённые attempts→сохранённые points; idempotency повтор вернул тот же ID.
8. Прямое перечисление неподтверждённых критериев: живой WebView, отдельная внешняя модель, metered gate, если они не выполнялись. Не писать «готово на 100%» при их наличии.
9. Known limits: симуляционная liquidation/funding, at-least-once provider calls, отдельный бюджет инженерного Hermes, отсутствие подтверждённой торговой доходности.

Новый README/runbook должен позволять повторить запуск и отказной тест без чтения этой переписки. Исполнитель не обязан воспроизводить историю диагностики Codex; его задача — выполнить конкретные критерии этого ТЗ.

## 13. Формат короткого отчёта Hermes после каждого этапа

```text
Этап: R04
Статус: passed | in_progress | blocked
Commit: фактический hash или uncommitted
Изменено: точные файлы
Проверено: точные команды; exit codes; passed/failed/skipped
Данные: production tables unchanged | перечень выполненных миграций
Доказательства: локальные пути
Осталось: конкретные IDs задач
Блокер: код и ровно одно необходимое действие, если без него работа невозможна
```

## 14. Отдельные запреты на неверные выводы

Не считать `/api/health` доказательством качества моделей. Не считать модели с разными role prompts независимыми специалистами без указания actual model. Не выводить «100% точность» по эвристическому score. Не считать Ollama Pro неограниченным бесплатным inference. Не освобождать расход timeout без доказанного отсутствия отправки. Не считать mock доказательством PostgreSQL row lock. Не считать passed unit tests доказательством установки. Не объявлять UI проверенным по одному AST/node --check. Не смешивать проблемы Foresight со здоровьем WORED. Не возобновлять старую hourly correction под видом исправления импорта.

**Решения выше зафиксированы. Менять их можно только отдельным изменением ТЗ владельцем; обычные детали реализации должны следовать указанным интерфейсам, алгоритмам и тестам.**

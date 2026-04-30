# Сильные и слабые стороны, технический аудит

## Область аудита

Этот аудит покрывает корневой runtime в `D:\WORED` по состоянию на `2026-04-24`.

Дополнение:

- после этого аудита в корневом runtime были отдельно исправлены Postgres healthcheck, раннее подтверждение длинных Telegram callbacks и MiniMax model id/fail-fast routing.

Проверенные артефакты:

- `README.md`
- `docker-compose.yml`
- `db/init.sql`
- `chatbot/*`
- `collector/*`
- состояние контейнеров и service logs
- docker-тесты `chatbot`

Что не входило в корневой runtime audit:

- `D:\WORED\hypercube`
- исследовательские каталоги вроде `анализ`
- вложенные virtualenv и cache-папки

## Сильные стороны

### 1. Есть реальный рабочий вертикальный срез

Проект — это не бумажная архитектура. Живой root stack поднят, и сервисы реально связаны:

- `collector` получает рыночные данные и пишет AI-журнал,
- `chatbot` опрашивает Telegram и использует runtime-данные,
- `redis` и `postgres` действительно задействованы.

### 2. AI routing сильнее, чем это выглядело по старой документации

В корневом `chatbot` уже есть:

- intent classification,
- context enrichment,
- provider tiering,
- timeout handling,
- retry handling,
- circuit breakers,
- fallback между провайдерами.

Это уже нормальная база для Telegram-first ассистента, а не просто thin wrapper над одной моделью.

### 3. Разделение ответственности в целом удачное

Сервисы разделены понятно:

- `collector` отвечает за ingestion и scheduled enrichment,
- `chatbot` отвечает за Telegram UX и AI orchestration,
- `redis` держит hot state,
- `postgres` держит историю.

Такой набор всё ещё достаточно мал, чтобы комфортно жить локально.

### 4. Тестовое покрытие узкое, но полезное

Текущие автотесты покрывают важные для сегодняшнего ядра вещи:

- поведение router,
- fallback logic,
- resilience primitives.

Проверенная команда:

```powershell
docker-compose run --rm chatbot pytest tests -q
```

Наблюдаемый результат:

- `15 passed`

### 5. Collector живёт в production-like режиме

По логам `collector` видно:

- стабильные подключения к HTX WebSocket,
- плановые проверки алертов,
- периодические записи в AI journal,
- успешные HTX REST-запросы для расчёта индикаторов.

Это значит, что проект уже имеет живой контур рыночного контекста, а не только AI-бота без данных.

## Слабые стороны

### 1. Документация и runtime-реальность успели разойтись

До этого документационного прохода:

- у root-проекта не было `docs/`,
- не было root `.env.example`,
- README не отделял активный runtime-код от legacy и placeholder-модулей.

Это был главный удар по сопровождаемости, а не отсутствие функциональности как таковой.

### 2. В проекте есть заглушки

Следующие файлы всё ещё являются stubs или незавершёнными модулями:

- `chatbot/context/builder.py`
- `chatbot/ui/formatter.py`
- `chatbot/ui/keyboards.py`
- `chatbot/ui/onboarding.py`
- `collector/alerts/detector.py`
- `collector/scheduler/briefing.py`

Они создают ложное ощущение полноты подсистем, если их явно не маркировать как non-runtime.

### 3. Legacy-файлы замутняют контекст

`chatbot/loader.py` — это старый aiogram 2 style entrypoint, который не участвует в активном runtime. Для нового участника проекта он выглядит как рабочая точка входа, хотя это уже не так.

### 4. Схема БД и код подключены только частично

В схеме есть `market_tickers` и `ai_usage_log`, но активный runtime реально использует только:

- `alerts`
- `ai_journal`

`save_tickers()` и `get_all_tickers()` в коде существуют, но к основному collector path не подключены. То есть часть storage-модели пока скорее декларативная, чем операционная.

### 5. Path второго мнения сейчас не здоров

В логах `chatbot` видно:

- HTTP `404 Not Found` для текущего `MiniMax` endpoint.

Практический эффект:

- кнопка второго мнения есть в UI,
- но её backend-path сейчас ненадёжен.

### 6. Telegram callback timing хрупкий

Логи также показывают:

- `query is too old and response timeout expired`

Это указывает на UX/runtime-проблему в длинных AI callback flows: бот иногда подтверждает callback слишком поздно, после долгого ожидания ответа модели.

### 7. Postgres healthcheck шумный и вводит в заблуждение

В compose healthcheck сейчас стоит:

```yaml
pg_isready -U ${POSTGRES_USER}
```

Наблюдаемый симптом:

- повторяющиеся `FATAL: database "bot" does not exist`

Корневая причина:

- healthcheck не указывает `-d ${POSTGRES_DB}` и проверяет не ту БД.

### 8. Слишком широкая область конфигурации

Поскольку root `docker-compose.yml` подключает `env_file: .env` сразу к нескольким сервисам, в `postgres` попадают и те secrets, которые ему вообще не нужны. Это не мгновенная авария, но это плохая практика secret hygiene.

### 9. Windows-эргономика была недоописана

Репозиторий живёт в Windows-path, а рабочая среда пользователя — PowerShell, но корневой `Makefile` написан в Unix-манере. Из-за этого воспроизводимость была неполной, если оператор заранее не знал, какие команды нужно переводить вручную.

## Итог

Корневой проект уже жизнеспособен как локальный Telegram crypto assistant с AI-анализом. Его главный актив — уже работающий multi-service вертикальный срез. Его главный риск — накопившийся слой документационного дрейфа, placeholder-модулей и partly-wired кода, который мешает быстро понять, что из этого реально работает в production-like режиме.

## Ближайшие технические приоритеты

1. Исправить Postgres healthcheck так, чтобы он использовал `POSTGRES_DB`.
2. Решить судьбу `MiniMax`: либо починить, либо убрать broken second-opinion path из продукта.
3. Либо подключить `market_tickers` и `ai_usage_log` к реальному runtime, либо перестать считать их частью активной архитектуры.
4. Удалить, архивировать или явно изолировать placeholder и legacy-файлы.
5. Добавить тесты на `collector` и хотя бы один Telegram end-to-end smoke scenario.

# Проверяемый план торговой сессии

Дата: 2026-09-14. Статус: код реализован и проверен в изолированном QA. Перезапуск рабочих процессов и проверка нового ответа реальной модели в Telegram не выполнены.

## Назначение и границы

Устранить рассогласование сессии, версии плана и заявок; проверять экономику до принятия сценария; показывать в Telegram понятный план с причинами отказа. Это существующий контур Daily Session (`trading_sessions` / `session_plans` / `planned_entries` / `executed_trades`), а не отдельные счета Trading Day из `paper_*`.

Версия схемы JSON `schema_version=2` отличается от номера версии плана конкретной сессии. Новый первый план остаётся v1. SQL-миграции, новые env-переменные и новые порты не нужны.

## Решения

1. Источник истины — `trading_sessions.active_plan_version`. Telegram и API WebUI читают ровно эту версию в согласованном read-only snapshot. Отсутствующая версия не заменяется молча последним найденным планом.
2. Генерация проверяет начало, окончание и статус сессии до обращения к модели. Публикация повторяет проверки под блокировкой строки. Завершённая или просроченная сессия не активируется генерацией.
3. План, исполняемые заявки, новая активная версия и событие аудита публикуются одной транзакцией. Сбой любого INSERT откатывает весь пакет.
4. Пауза, продолжение и команды управления не увеличивают версию плана. Они сохраняют аудит с `base_version == new_version`. WebUI вызывает ту же реализацию управления, что Telegram.
5. Почасовая ревизия создаёт полный проверенный JSON новой версии. Неисполненные заявки предыдущей версии получают `superseded`. Заявки открытых сделок сохраняют свои идентификаторы и защитные уровни. Изменившийся во время генерации набор pending-заявок отклоняет публикацию.
6. `no_trade` допустим при любом профиле риска. При отказе проверки кандидат остаётся в `rejected_entries` с причинами, без исполняемой строки в `planned_entries`. Если часть кандидатов прошла проверки, публикуются только прошедшие.
7. Срок новых входов — не более часа и не позже окончания сессии. Снимок данных должен иметь `quality=ready` и возраст не больше 90 секунд при валидации и публикации. Долгая генерация может быть отклонена из-за устаревшего снимка.
8. Перед исполнением повторно проверяются действующий план, версия заявки, срок сессии, статус, риск при фактической цене с проскальзыванием и атомарная возможность открыть единственную позицию.

## Контракт и экономические проверки

Обязательные поля модели: `market_regime`, `thesis`, `primary_scenario`, `alternative_scenario`, `no_trade_condition`, `entries`. Объяснения запрашиваются по-русски. До трёх заявок; пустой массив допустим только при `primary_scenario=no_trade`.

Режимы рынка: `trend_up`, `trend_down`, `range`, `volatile`, `unknown`.

Каждая заявка содержит `side`, `entry_zone_from`, `entry_zone_to`, `invalidation_price`, `stop_loss`, `take_profit`, `recommended_leverage`, `budget_share_pct`, `margin_mode`, `confirmation_rule`, `reason_code`.

- LONG: SL < invalidation < нижняя граница зоны <= верхняя граница < TP1. Следующие цели возрастают.
- SHORT: TP1 < нижняя граница зоны <= верхняя граница < invalidation < SL. Следующие цели убывают.
- Плечо: 10, 25, 50 или 100. Нет исключений для fast/medium/long.
- Доля маржи: defensive 5–10%, balanced 10–20%, aggressive 20–30%. Это прежняя политика доли маржи, не лимит риска по стопу.
- Только isolated. Числа конечные и положительные; булевы значения вместо чисел и NaN/Infinity отклоняются.
- SL с учётом выходного slippage должен находиться до ликвидации.
- Чистая прибыль TP1 / чистый убыток SL >= 1. Это порог допуска сценария, не прогноз вероятности успеха.
- Если `cost_filter_enabled=true`, чистая прибыль TP1 должна достигать `target_net_profit_usdt`.

Расчёт использует текущую политику симулятора `isolated-v2`: комиссия taker 0,06% на входе и выходе, slippage 2 bps на каждой стороне, maintenance margin 0,5%. Для предварительного допуска используется худшая граница зоны: верхняя для LONG, нижняя для SHORT. Комиссия выхода считается от выходного номинала. В `risk` сохраняются маржа, номинал, количество, расчётный fill, ликвидация, чистый убыток SL, риск в процентах бюджета, чистая прибыль TP1, net R:R и комиссии.

Разрешённые подтверждения: `close_above_zone_on_1m_and_rsi_gt_50`, `rsi_gt_50` для LONG; `close_below_zone_on_1m_and_rsi_lt_50`, `rsi_lt_50` для SHORT. `any` для новых входов не принимается. Watch loop выбирает закрытую 1m свечу; при недоступной свече или индикаторах новый вход блокируется. Тикер остаётся доступен для защитного мониторинга.

## Отображение и ограничения

Telegram показывает инструмент, симуляцию, сессию, версию, время данных, срок действия, модель, объяснения, сценарии, зону, подтверждение, SL, отмену, цели, маржу, номинал, риск SL, чистый TP1, R:R и ликвидацию. Текст модели экранируется для HTML; сообщение ограничено 4000 символами. При длинном ответе сокращается аналитический текст, уровни и экономика заявок сохраняются. Полный JSON доступен в `plan.details` API `/api/daily-session/active` и в PostgreSQL.

Funding и стакан не включены в экономику этого контура; формула ликвидации не является спецификацией HTX. Модель обязана обозначать отсутствующие данные, а расчётные уровни не выдавать за наблюдённую поддержку/сопротивление. Текст `no_trade_condition` остаётся объяснением аналитика: исполняются только формальные правила заявок. `primary_scenario=no_trade` действительно исключает заявки.

TP1 закрывает всю позицию. TP2/TP3 — ориентиры. Частичные выходы и trailing отсутствуют. Лимит времени позиции применяется ко всем горизонтам. Для паузы сохраняется защитный мониторинг: scheduler включает paused-сессии в watch loop. `close_all` использует свежий тикер; при отсутствии цены позиция остаётся открытой, сессия paused, возвращается явная ошибка. Только после закрытия всех позиций сессия становится stopped.

Прежние записи не переписываются и не удаляются. План без схемы v2 не разрешает новые входы до проверенной ревизии. Если активная версия вообще отсутствует, требуется новая сессия; автоматического исправления истории нет. Не создавайте новый план для завершённой сессии от 10 сентября.

## Файлы

```text
chatbot/services/plan_contract.py        проверки срока, схемы, риска
chatbot/services/plan_store.py           атомарная публикация и применение patch
chatbot/services/plan_presenter.py       безопасный подробный текст Telegram
chatbot/services/session_manager.py     генерация, bootstrap, ревизия, исполнение, контроль
chatbot/services/execution_engine.py    подтверждения SHORT и строгое закрытие выше зоны
chatbot/handlers/pipeline.py            чтение активной версии и новый presenter
collector/scheduler/pipeline_jobs.py    защитный мониторинг paused-сессий
webui/app.py                           согласованное чтение и общий обработчик контроля
tests/test_session_plan_contract.py    контракт, генерация, транзакции, Telegram
tests/test_session_plan_web.py         API WebUI и версии
tests/stabilization/test_postgres_sessions.py  прежние гарантии исполнения
```

## Воспроизводимая проверка

Предпосылки: PowerShell в `D:\WORED`; Docker Desktop; существующие локальные образы `wored-chatbot`, `wored-webui`, `postgres:16`; локальный Python с pytest, ruff, mypy и зависимостями проекта. QA-команды не используют рабочий DATABASE_URL. Пароль ниже относится только к временной тестовой базе.

```powershell
Set-Location D:\WORED
python -m pytest tests/test_session_plan_contract.py tests/test_session_plan_web.py tests/stabilization/test_execution_contracts.py tests/stabilization/test_snapshot_consumers.py tests/stabilization/test_http_boundary.py -q -p no:cacheprovider
python -m ruff check --no-cache chatbot/services/plan_contract.py chatbot/services/plan_store.py chatbot/services/plan_presenter.py tests/test_session_plan_contract.py tests/test_session_plan_web.py
python -m mypy --follow-imports=silent --ignore-missing-imports chatbot/services/plan_contract.py chatbot/services/plan_store.py chatbot/services/plan_presenter.py
python -m ruff check --no-cache --select E9,F821,F822,F823 chatbot/services/session_manager.py chatbot/services/execution_engine.py chatbot/handlers/pipeline.py collector/scheduler/pipeline_jobs.py webui/app.py
docker compose config --quiet
docker compose -p wored-qa -f docker-compose.qa.yml up -d postgres-qa
docker run --rm --network wored-qa_default --mount type=bind,source=D:\WORED,target=/repo,readonly --workdir /repo -e WORED_TEST_DATABASE_URL=postgresql://qa:disposable-qa-only@postgres-qa:5432/wored_qa -e PYTHONDONTWRITEBYTECODE=1 wored-chatbot python -m unittest discover -s tests -p test_session_plan_contract.py -v
docker run --rm --network wored-qa_default --mount type=bind,source=D:\WORED,target=/repo,readonly --workdir /repo -e WORED_TEST_DATABASE_URL=postgresql://qa:disposable-qa-only@postgres-qa:5432/wored_qa -e PYTHONDONTWRITEBYTECODE=1 wored-webui python -m unittest discover -s tests -p test_session_plan_web.py -v
docker run --rm --network wored-qa_default --mount type=bind,source=D:\WORED,target=/repo,readonly --workdir /repo -e WORED_TEST_DATABASE_URL=postgresql://qa:disposable-qa-only@postgres-qa:5432/wored_qa -e PYTHONDONTWRITEBYTECODE=1 wored-chatbot python -m unittest discover -s tests/stabilization -p test_postgres_sessions.py -v
```

Результаты: локально 40 passed; DB-зависимые проверки локально пропускаются и выполняются следующими командами. Изолированный пакет плана: 28/28 OK; WebUI: 3/3 OK; прежние транзакционные проверки: 4/4 OK. Последний добавленный тест почасовой ревизии проверен в Docker. Новые модули проходят полный Ruff и mypy. В пяти существующих runtime-файлах полный Ruff имеет 44 прежних замечания; сравнение кодов и сообщений с HEAD не выявило новых. Критическая выборка Ruff, компиляция Python и Compose config проходят.

Тесты модели используют AsyncMock, не вызывают AI-провайдеров, не отправляют Telegram-сообщения и не создают рабочие сделки. Каждый DB-тест работает в собственной схеме `wored_qa`; новые схемы оставляются в tmpfs для диагностики. UI браузера и настоящий Telegram-клиент этим пакетом не проверяются.

## Применение и диагностика

Рабочие процессы держат импортированные Python-модули в памяти. Для загрузки всего пакета после отдельного подтверждения перезапуска:

```powershell
Set-Location D:\WORED
docker compose restart chatbot chatbot_wored collector webui
docker compose ps
Invoke-RestMethod http://127.0.0.1:8080/api/health
```

Ожидается: контейнеры running, Redis/PostgreSQL true; свежесть collector_feed проверяется отдельно после прогрева. Healthy процесса не подтверждает качество AI-плана. Проверка в Telegram: создать новую сессию, открыть «План», убедиться в русском объяснении и видимых уровнях/риске либо причинах отказа. Нельзя считать `no_trade` ошибкой запуска. Пауза/продолжение не меняют номер плана. Почасовая ревизия должна сохранять полный JSON следующей версии.

Диагностика: `session_expired` — завершить старый сценарий и начать новую сессию; `active_plan_version_missing` — не подменять историю, создать новую сессию; `market_snapshot_expired` — проверить свежесть collector и повторить на новом снимке; `plan_not_validated` — старая схема или отклонённый план; `stop_beyond_liquidation` — изменить сценарий/плечо, не обходить проверку; `close_pending_fresh_price` — восстановить цены и повторить закрытие. Ошибка evaluator `inconsistent types deduced for parameter $5` из предварительного аудита не исправлялась в этом пакете.

Рабочий перезапуск не выполнен, поэтому откат развёртывания сейчас не требуется. SQL-миграций нет. В рабочем дереве есть чужие изменения: нельзя откатывать весь `webui/app.py` или применять `git reset --hard`. При дальнейшем откате кода нужно реверсировать только согласованный diff этого пакета; планы v2 не удалять, новые входы до повторной проверки держать на паузе.

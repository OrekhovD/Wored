P1 Roadmap для Hermes: WORED Product Hardening
Общие правила для всех P1-задач

Hermes обязан:

1. Сначала диагностировать текущее состояние.
2. Не трогать legacy-зоны без прямого разрешения.
3. Не печатать секреты.
4. Не удалять существующий runtime-код.
5. Перед патчем показать список файлов.
6. После патча дать команды проверки.
7. Не делать git commit/push без отдельной команды.

Запрещено:

- переписывать WebUI с нуля;
- удалять webui/static/app.js;
- заменять весь styles.css без необходимости;
- ломать /, /alerts, /predictions, /journal;
- удалять price/volume/RSI/MACD charts;
- менять AI-routing chatbot без запроса;
- выполнять down -v, rm -rf, docker volume rm.
HERMES-P1-01: UX Foundation Patch
Цель

Поднять удобство текущего WebUI без переписывания. Это foundation-патч: визуальная и UX-структура, статусы, пустые состояния, читаемость, responsive-polish.

Активный runtime path
webui/templates/base.html
webui/templates/index.html
webui/templates/alerts.html
webui/templates/predictions.html
webui/templates/journal.html
webui/templates/login.html
webui/static/styles.css
webui/static/app.js
Что нужно сделать
1. Улучшить глобальную навигацию

Добавить/усилить:

- активное состояние текущего раздела;
- компактные status badges справа;
- визуальный auth-state;
- нормальные hover/focus states;
- mobile wrapping без развала.
2. Добавить универсальные UI-состояния

В styles.css добавить классы:

.status-dot
.status-ok
.status-warn
.status-bad
.status-idle

.empty-state
.error-state
.loading-state
.skeleton
.badge
.badge-soft
.badge-danger
.badge-success
.badge-warning

Не удалять старые классы.

3. Улучшить карточки и панели

Для существующих:

.panel
.metric-card
.watch-card
.event-card
.journal-card
.action-card
.chart-card

Добавить:

- более явные hover/focus states;
- лучшее разделение заголовка и тела;
- readable spacing;
- consistent labels/values;
- мягкую подсветку важных данных.
4. Улучшить формы

Для:

select
input
button
period-picker
prediction-form
filters

Добавить:

- focus ring;
- disabled state;
- loading/processing state;
- единый стиль primary/ghost/danger buttons.
5. Responsive polish

Проверить:

desktop: 1440px+
laptop: 1280px
tablet: 768px
mobile: 390px

На mobile порядок должен быть:

1. topbar
2. page header / hero
3. active controls
4. charts/content
5. side panels
Что нельзя делать
- нельзя удалять блоки из index.html;
- нельзя переносить JS inline;
- нельзя заменять app.js;
- нельзя ломать Lightweight Charts;
- нельзя удалять страницы alerts/predictions/journal/login.
Команды диагностики перед патчем

Hermes должен выполнить:

/routes
/lw
/git-status

И посмотреть файлы:

sed -n '1,220p' webui/templates/base.html
sed -n '1,260p' webui/templates/index.html
sed -n '1,260p' webui/static/styles.css
sed -n '1,220p' webui/static/app.js
Команды проверки после патча
docker compose up -d --build webui
curl -fsS http://localhost:8080/ >/dev/null
curl -fsS http://localhost:8080/alerts >/dev/null
curl -fsS http://localhost:8080/predictions >/dev/null
curl -fsS http://localhost:8080/journal >/dev/null
docker compose logs webui --tail=100
Definition of Done
- все маршруты открываются;
- WebUI визуально стал чище;
- нет потери функциональности;
- chart containers сохранены;
- app.js сохранён;
- styles.css расширен, а не уничтожен;
- mobile не разваливается;
- в логах webui нет новых 500/traceback.
Готовый промпт для Hermes
Задача: HERMES-P1-01 UX Foundation Patch.

Работай строго по правилам WORED:
- не переписывай WebUI с нуля;
- не удаляй webui/static/app.js;
- не удаляй существующие страницы;
- сохрани price/volume/RSI/MACD charts;
- не трогай legacy;
- не печатай секреты.

Сначала выполни /routes, /lw, /git-status.
Затем проанализируй:
- webui/templates/base.html
- webui/templates/index.html
- webui/templates/alerts.html
- webui/templates/predictions.html
- webui/templates/journal.html
- webui/templates/login.html
- webui/static/styles.css
- webui/static/app.js

Сначала дай PLAN, FILES, RISK.
Потом предложи инкрементальный patch.
После patch выполни route smoke-test и дай REPORT.
HERMES-P1-02: Runtime Health Panel

Запускать только после успешного P1-01.

Цель

Пользователь должен видеть состояние runtime прямо в WebUI, без Hermes CLI.

Показать:

Redis: OK / stale / error
Postgres: OK / error
Collector feed: fresh / stale / missing
WebUI: OK
AI Journal: fresh / stale / empty
Ticker cache: count
Open alerts: count
Forecast requests: latest status
Вероятные файлы
webui/app.py
webui/templates/base.html
webui/templates/index.html
webui/static/styles.css
webui/static/app.js
Backend contract

Нужно проверить, есть ли уже endpoint:

/api/health
/api/overview

Если есть — расширить аккуратно.
Если нет — добавить новый:

GET /api/runtime/health

Пример ответа:

{
  "redis": {
    "status": "ok",
    "ticker_count": 2,
    "journal_ttl": 84
  },
  "postgres": {
    "status": "ok"
  },
  "collector": {
    "status": "fresh",
    "last_ticker_age_sec": 8
  },
  "webui": {
    "status": "ok"
  },
  "alerts": {
    "open_count": 3
  },
  "forecasts": {
    "latest_status": "completed"
  }
}
UI

В topbar или hero/right rail:

● Redis OK
● PG OK
● Collector fresh 8s
● Journal 84s TTL
Alerts 3
Проверки
curl -fsS http://localhost:8080/api/runtime/health | python3 -m json.tool
curl -fsS http://localhost:8080/ >/dev/null
docker compose logs webui --tail=120
Definition of Done
- endpoint возвращает JSON без секретов;
- UI показывает runtime status;
- если Redis/Postgres недоступны, страница не падает;
- collector stale отображается как warning;
- webui routes работают.
Готовый промпт для Hermes
Задача: HERMES-P1-02 Runtime Health Panel.

Сначала проверь текущие endpoints в webui/app.py.
Не меняй docker-compose.
Не трогай chatbot/collector runtime код, если health можно собрать из Redis/Postgres.
Не печатай секреты.

Цель:
добавить безопасный /api/runtime/health и вывести краткий runtime status в WebUI.

Сначала дай PLAN, FILES, RISK.
После patch проверь:
curl -fsS http://localhost:8080/api/runtime/health | python3 -m json.tool
curl -fsS http://localhost:8080/ >/dev/null
docker compose logs webui --tail=120
HERMES-P1-03: Collector Observability

Запускать после P1-02.

Цель

Hermes и WebUI должны понимать: collector реально жив или просто контейнер Up.

Проверять:

- age последнего ticker;
- количество ticker:* в Redis;
- TTL ai:journal:latest;
- наличие recent candles;
- свежесть indicators;
- ошибки HTX WebSocket;
- ошибки HTX REST;
- publish market_alerts;
- записи в alerts / ai_journal.
Активный runtime path

Сначала диагностика:

collector/main.py
collector/storage/*
collector/htx/*
collector/indicators/*
collector/journal/*
collector/scheduler/*
webui/app.py
scripts/runtime_doctor.sh

Legacy caution:

collector/alerts/detector.py
collector/scheduler/briefing.py

Не трогать без подтверждения.

Что добавить

Лучший вариант — сначала не менять collector, а добавить script:

scripts/runtime_doctor.sh

Он должен выводить:

Docker services
Redis ping
ticker count
ai journal ttl
sample ticker
Postgres tables
latest alerts
latest ai_journal row
recent errors from collector logs

Потом можно подключить часть этих данных к WebUI health.

Проверки
bash scripts/runtime_doctor.sh
/lc
/tickers
/journal
/dbstats
Definition of Done
- Hermes одной командой отличает fresh/stale collector;
- Redis пустой cache отображается как warning;
- journal TTL проверяется;
- recent collector errors видны;
- нет изменения ingestion logic без необходимости.
Готовый промпт для Hermes
Задача: HERMES-P1-03 Collector Observability.

Сначала не меняй collector logic.
Собери observability через Redis/Postgres/logs.
Создай или обнови scripts/runtime_doctor.sh.

Проверь:
- /lc
- /tickers
- /journal
- /dbstats

Legacy не трогать:
- collector/alerts/detector.py
- collector/scheduler/briefing.py

Сначала дай PLAN, FILES, RISK.
После patch выполни bash scripts/runtime_doctor.sh и дай REPORT.
HERMES-P1-04: Prediction Lab Quality View

Запускать после P1-03.

Цель

Prediction Lab должен показывать не только сохранённые forecast requests, но и качество моделей:

direction_hit
change_pct_error
forecast vs actual
winner
provider/fallback
latency
status
Активный runtime path
webui/app.py
webui/templates/predictions.html
webui/static/styles.css
возможно db/init.sql или миграции, если данных не хватает
Сначала проверить схему

Hermes должен выполнить:

/dbtables
/forecasts

И SQL-inspection:

docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\d forecast_requests"'
docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\d forecast_points"'
docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\d forecast_evaluations"'

Имена таблиц могут отличаться — сначала выяснить фактическую схему.

UI-блоки

Добавить:

Model Leaderboard
Forecast Accuracy Cards
Saved Requests улучшить
Truth Gap block:
  Forecast: +1.20%
  Actual: -0.40%
  Gap: -1.60%
  Direction: missed
Не делать на первом проходе
- не менять AI providers;
- не менять forecast generation logic;
- не добавлять новую сложную схему БД без необходимости;
- не трогать chatbot AI routing.
Definition of Done
- Prediction Lab показывает качество прогнозов, если данные есть;
- если данных нет — понятный empty-state;
- route /predictions работает;
- webui logs без traceback;
- SQL не падает на пустых таблицах.
Готовый промпт для Hermes
Задача: HERMES-P1-04 Prediction Lab Quality View.

Сначала проверь фактическую схему forecast tables через /dbtables и psql \d.
Не меняй AI provider routing.
Не меняй forecast generation logic.
Не добавляй миграции, пока не подтвердил, что текущих данных недостаточно.

Цель:
улучшить /predictions так, чтобы видеть качество моделей:
- direction_hit;
- change_pct_error;
- forecast vs actual;
- winner;
- status;
- provider/fallback, если данные есть.

Сначала дай PLAN, FILES, RISK.
После patch проверь:
curl -fsS http://localhost:8080/predictions >/dev/null
docker compose logs webui --tail=120
Финальный порядок запуска
1. HERMES-P1-01 UX Foundation Patch
2. Проверка /routes + webui logs
3. HERMES-P1-02 Runtime Health Panel
4. Проверка /api/runtime/health
5. HERMES-P1-03 Collector Observability
6. Проверка scripts/runtime_doctor.sh
7. HERMES-P1-04 Prediction Lab Quality View
8. Проверка /predictions + SQL safety
Команда владельца для старта

Дай Hermes ровно это:

Начинаем P1 по порядку.

Стартуй с HERMES-P1-01 UX Foundation Patch.
Соблюдай WORED rules:
- сначала диагностика;
- не трогать legacy;
- не печатать секреты;
- не переписывать WebUI с нуля;
- не удалять app.js;
- сохранить price/volume/RSI/MACD charts;
- сначала PLAN, FILES, RISK;
- потом patch;
- после patch TEST и REPORT.

Выполни /routes, /lw, /git-status и проанализируй webui templates/static.
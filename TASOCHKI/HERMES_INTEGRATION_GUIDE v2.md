ТЗ: Hermes Agent Full-Power Setup для WORED
1. Назначение

Hermes Agent используется как host-level technical orchestrator для WORED. Он работает на хост-машине, управляет проектом через docker compose, shell, Redis, Postgres, файловую систему и контекст AGENTS.md. Он не заменяет runtime-сервисы chatbot, collector, webui, postgres, redis, а обслуживает их снаружи. Такая роль соответствует базовой идее интеграции Hermes: агент запускает команды, читает проектный контекст, управляет Docker и использует LLM-провайдеры через собственный конфиг.

2. Текущий подтверждённый статус
Hermes развёрнут и работает.
Модель: glm-5.1 / zai.
cwd: /mnt/d/WORED.
MCP filesystem привязан к /mnt/d/WORED.
AGENTS.md переписан под WORED.
SOUL.md обновлён под WORED.
quick_commands работают.
Старый контекст htx-trading-bot/trading_redis/trading_postgres удалён.
Права ~/.hermes и ~/.hermes/.env корректные.
Shell smoke-test пройден.
TUI smoke-test пройден.

Статус:

Hermes Agent baseline: READY
Следующий уровень: hardening + task workflows + automation + validation
3. Главная цель настройки

Hermes должен уметь выполнять 5 классов задач:

1. Диагностика runtime.
2. Безопасные DevOps-операции.
3. Инкрементальная разработка.
4. Проверка качества и регрессий.
5. Ведение проектной памяти и задач.

При этом он должен быть ограничен правилами:

- не печатать секреты;
- не выполнять destructive-команды без подтверждения;
- не трогать legacy-зоны без явного запроса;
- не переписывать WebUI с нуля;
- перед изменениями показывать план;
- после изменений давать команды проверки.
4. Что Hermes уже умеет
4.1 Runtime commands

Уже рабочие команды:

/ps
/health
/lw
/lc
/lb
/tickers
/journal
/dbtables
/alerts

Дополнительно должны быть в рабочем наборе:

/up
/down
/restart
/rebuild
/logs
/dbstats
/forecasts
4.2 Контекст проекта

Hermes уже знает:

- WORED runtime stack;
- активные сервисы;
- legacy/caution зоны;
- AI-routing правила;
- WebUI runtime path;
- security constraints;
- правила работы с Docker/Redis/Postgres.
5. Чего Hermes не хватает для работы на полную мощность
5.1 Недостаёт профилей задач

Сейчас Hermes имеет команды, но не имеет формализованных рабочих сценариев. Нужно добавить task playbooks:

docs/hermes/playbooks/
├── diagnose-runtime.md
├── fix-webui.md
├── fix-collector.md
├── fix-chatbot.md
├── security-audit.md
├── prediction-lab.md
├── db-maintenance.md
├── release-check.md
└── rollback.md

Каждый playbook должен описывать:

- цель;
- команды диагностики;
- какие файлы можно трогать;
- какие файлы нельзя трогать;
- критерии готовности;
- команды проверки;
- когда остановиться и спросить владельца.
5.2 Недостаёт команды doctor-full

Нужна отдельная quick command, которая собирает полный health snapshot:

docker compose ps
webui health
collector logs tail
chatbot logs tail
webui logs tail
redis ticker count
latest ai journal age
postgres tables
latest alerts
latest forecasts
disk usage
git status

Цель: одна команда для первичной диагностики.

Команда:

/doctor-full
5.3 Недостаёт безопасного режима патчей

Hermes должен работать по схеме:

PLAN -> DIFF -> APPLY -> TEST -> REPORT

Не сразу менять файлы.

Нужно закрепить в AGENTS.md правило:

Для любых изменений кода:
1. Сначала показать план.
2. Назвать active runtime path или legacy area.
3. Назвать список файлов.
4. Назвать риск регрессии.
5. Только потом предлагать patch.
6. После patch дать test commands.
5.4 Недостаёт Git workflow

Hermes должен уметь работать в отдельной ветке:

git status
git checkout -b hermes/<task-name>
git diff
git add ...
git commit -m "..."

Но без автоматического push.

Правило:

Hermes может создавать локальные ветки и коммиты только после подтверждения.
Hermes не делает git push без явной команды.
5.5 Недостаёт регрессионного чеклиста WebUI

Так как WebUI уже ломался плохим редизайном, для Hermes нужен отдельный guardrail:

Перед WebUI patch:
- проверить base.html;
- проверить index.html;
- проверить styles.css;
- проверить app.js;
- перечислить chart containers;
- подтвердить сохранение price/volume/RSI/MACD;
- подтвердить сохранение routes /alerts /predictions /journal.
5.6 Недостаёт задач по observability

Hermes должен не только читать логи, но и выявлять:

- stale collector feed;
- пустой Redis ticker cache;
- отсутствие ai:journal:latest;
- ошибки HTX REST/WS;
- ошибки AI provider fallback;
- webui 500;
- Postgres таблицы без записей;
- forecast_requests stuck;
- alerts reopened/open backlog.
5.7 Недостаёт безопасной работы с Telegram gateway

Если включать Hermes Telegram gateway, нужен отдельный hardening:

- отдельный admin bot token;
- allowlist Telegram ID;
- запрет вывода .env;
- запрет destructive-команд;
- подтверждение для /down, /rebuild, /restart;
- логирование admin actions;
- отключено по умолчанию.
6. Требуемые артефакты
6.1 docs/hermes/README.md

Назначение: основной документ по работе с Hermes в WORED.

Содержание:

1. Что такое Hermes в WORED.
2. Как запускать.
3. Quick commands.
4. Правила безопасности.
5. Playbooks.
6. Git workflow.
7. Troubleshooting.
8. Что запрещено.
6.2 docs/hermes/playbooks/diagnose-runtime.md

Сценарий диагностики всего runtime.

Обязательные шаги:

1. /ps
2. /health
3. /lc
4. /lb
5. /lw
6. /tickers
7. /journal
8. /dbtables
9. /alerts
10. /forecasts

Выход:

- статус каждого сервиса;
- список ошибок;
- вероятная причина;
- следующий безопасный шаг.
6.3 docs/hermes/playbooks/fix-webui.md

Сценарий работы с WebUI.

Правила:

- не переписывать WebUI с нуля;
- не удалять app.js;
- не удалять существующие страницы;
- сохранить TradingView Lightweight Charts;
- сохранить price/volume/RSI/MACD;
- патчить инкрементально.

Проверка:

curl -fsS http://localhost:8080/ >/dev/null
curl -fsS http://localhost:8080/alerts >/dev/null
curl -fsS http://localhost:8080/predictions >/dev/null
curl -fsS http://localhost:8080/journal >/dev/null
docker compose logs webui --tail=80
6.4 docs/hermes/playbooks/fix-collector.md

Сценарий работы с collector.

Проверять:

- HTX WS reconnect;
- ticker cache;
- candles;
- indicators;
- ai journal;
- alert publishing;
- collector logs.

Команды:

/lc
/tickers
/journal
/dbstats
6.5 docs/hermes/playbooks/fix-chatbot.md

Сценарий работы с Telegram chatbot.

Проверять:

- aiogram startup;
- Redis FSM;
- AI provider routing;
- fallback Qwen -> GLM -> MiniMax;
- Telegram token only presence, not value;
- notification delivery.

Команды:

/lb
/health
6.6 docs/hermes/playbooks/security-audit.md

Сценарий security-аудита.

Проверять:

- .env не в git;
- нет ключей в истории;
- docker-compose не раздаёт секреты лишним контейнерам;
- webui auth;
- Postgres env least privilege;
- Hermes secrets permissions;
- quick_commands без destructive/default secret leaks;
- Telegram gateway disabled или hardened.
6.7 docs/hermes/playbooks/release-check.md

Перед релизом:

docker compose config
docker compose up -d --build
docker compose ps
curl -fsS http://localhost:8080/ >/dev/null
curl -fsS http://localhost:8080/alerts >/dev/null
curl -fsS http://localhost:8080/predictions >/dev/null
curl -fsS http://localhost:8080/journal >/dev/null
git status
7. Расширение quick_commands

Добавить в ~/.hermes/config.yaml.

7.1 git-status
git-status:
  type: exec
  command: git status --short && git branch --show-current
7.2 routes
routes:
  type: exec
  command: curl -fsS http://localhost:8080/ >/dev/null && echo "/ OK"; curl -fsS http://localhost:8080/alerts >/dev/null && echo "/alerts OK"; curl -fsS http://localhost:8080/predictions >/dev/null && echo "/predictions OK"; curl -fsS http://localhost:8080/journal >/dev/null && echo "/journal OK"
7.3 redis-scan
redis-scan:
  type: exec
  command: docker compose exec -T redis redis-cli --scan | head -80
7.4 ticker-count
ticker-count:
  type: exec
  command: docker compose exec -T redis redis-cli --scan --pattern 'ticker:*' | wc -l
7.5 journal-age
journal-age:
  type: exec
  command: docker compose exec -T redis redis-cli ttl ai:journal:latest
7.6 errors
errors:
  type: exec
  command: docker compose logs --tail=300 | grep -Ei 'error|exception|traceback|failed|timeout|refused' || true
7.7 doctor-full
doctor-full:
  type: exec
  command: |
    echo "=== DOCKER ==="
    docker compose ps
    echo ""
    echo "=== WEBUI ROUTES ==="
    curl -fsS http://localhost:8080/ >/dev/null && echo "/ OK" || echo "/ FAIL"
    curl -fsS http://localhost:8080/alerts >/dev/null && echo "/alerts OK" || echo "/alerts FAIL"
    curl -fsS http://localhost:8080/predictions >/dev/null && echo "/predictions OK" || echo "/predictions FAIL"
    curl -fsS http://localhost:8080/journal >/dev/null && echo "/journal OK" || echo "/journal FAIL"
    echo ""
    echo "=== REDIS ==="
    docker compose exec -T redis redis-cli ping || true
    echo "ticker count:"
    docker compose exec -T redis redis-cli --scan --pattern 'ticker:*' | wc -l || true
    echo "journal ttl:"
    docker compose exec -T redis redis-cli ttl ai:journal:latest || true
    echo ""
    echo "=== POSTGRES TABLES ==="
    docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"' || true
    echo ""
    echo "=== RECENT ERRORS ==="
    docker compose logs --tail=300 | grep -Ei 'error|exception|traceback|failed|timeout|refused' || true
8. Рабочие задачи для Hermes
8.1 Priority P0 — безопасность и контроль
HERMES-P0-01: Зафиксировать playbooks
Цель:
создать docs/hermes/playbooks/* для типовых задач.

Файлы:
- docs/hermes/README.md
- docs/hermes/playbooks/diagnose-runtime.md
- docs/hermes/playbooks/fix-webui.md
- docs/hermes/playbooks/fix-collector.md
- docs/hermes/playbooks/fix-chatbot.md
- docs/hermes/playbooks/security-audit.md
- docs/hermes/playbooks/release-check.md

Готово, когда:
Hermes перед изменениями ссылается на нужный playbook.
HERMES-P0-02: Добавить расширенные quick_commands
Цель:
добавить /doctor-full, /routes, /errors, /git-status, /ticker-count, /journal-age.

Файл:
- ~/.hermes/config.yaml

Готово, когда:
все команды выполняются без падения и не выводят секреты.
HERMES-P0-03: Запрет destructive actions в правилах
Цель:
закрепить запрет на down -v, rm -rf, docker volume rm, cat .env.

Файлы:
- AGENTS.md
- ~/.hermes/SOUL.md
- docs/hermes/README.md

Готово, когда:
правило явно прописано во всех трёх местах.
8.2 Priority P1 — разработка WORED
HERMES-P1-01: UX Foundation Patch
Цель:
улучшить текущую WebUI дизайн-систему без переписывания.

Файлы:
- webui/templates/base.html
- webui/templates/index.html
- webui/templates/alerts.html
- webui/templates/predictions.html
- webui/templates/journal.html
- webui/static/styles.css
- webui/static/app.js

Ограничения:
- не удалять app.js;
- не удалять price/volume/RSI/MACD;
- не удалять страницы;
- не заменять styles.css полностью;
- только инкрементальные улучшения.

Готово, когда:
все маршруты webui проходят curl smoke-test.
HERMES-P1-02: Runtime Health Panel
Цель:
вывести в WebUI явные статусы Redis/Postgres/Collector/WebUI.

Файлы:
- webui/app.py
- webui/templates/base.html или index.html
- webui/static/styles.css
- возможно webui/static/app.js

Готово, когда:
пользователь видит health-состояние без запуска CLI.
HERMES-P1-03: Collector observability
Цель:
улучшить диагностику collector feed freshness.

Проверять:
- age последнего ticker;
- TTL ai:journal:latest;
- наличие candles;
- ошибки HTX WS/REST.

Готово, когда:
Hermes может по одной команде объяснить, жив collector или stale.
HERMES-P1-04: Prediction Lab quality view
Цель:
улучшить отображение качества прогнозов.

Показывать:
- direction hit;
- change_pct error;
- forecast vs actual;
- provider/fallback;
- latency.

Готово, когда:
Prediction Lab отвечает не только “что предсказали”, но и “кто был точнее”.
8.3 Priority P2 — automation
HERMES-P2-01: Local regression script
Цель:
создать scripts/smoke_webui.sh.

Проверяет:
- docker compose config;
- routes /, /alerts, /predictions, /journal;
- webui logs tail;
- отсутствие 500.

Готово, когда:
bash scripts/smoke_webui.sh возвращает exit 0 на здоровой системе.
HERMES-P2-02: Runtime doctor script
Цель:
создать scripts/runtime_doctor.sh.

Проверяет:
- docker compose ps;
- Redis ping;
- ticker count;
- journal ttl;
- Postgres tables;
- recent errors.

Готово, когда:
Hermes может запускать скрипт вместо длинной inline-команды.
HERMES-P2-03: Git task branch workflow
Цель:
описать и внедрить branch workflow.

Файл:
- docs/hermes/git-workflow.md

Правило:
каждая большая задача идёт в ветке hermes/<task>.
9. Definition of Done для тонкой настройки Hermes

Hermes считается настроенным «на полную мощность», когда:

1. Есть docs/hermes/README.md.
2. Есть playbooks для runtime, webui, collector, chatbot, security, release.
3. Есть /doctor-full.
4. Есть /routes, /errors, /git-status, /ticker-count, /journal-age.
5. Есть scripts/smoke_webui.sh.
6. Есть scripts/runtime_doctor.sh.
7. В AGENTS.md и SOUL.md закреплены destructive guardrails.
8. Hermes не выводит секреты.
9. Hermes не трогает legacy без явной команды.
10. Hermes умеет работать по PLAN -> DIFF -> APPLY -> TEST -> REPORT.
11. WebUI изменения проходят route smoke-test.
12. Collector задачи начинаются с /lc, /tickers, /journal.
13. Chatbot задачи начинаются с /lb и проверки AI-routing.
14. Security задачи проверяют .env, compose env exposure и history leaks.
15. Release-check выполняется одной процедурой.
10. Первый пакет работ

Я бы дал Hermes такую задачу первой:

Задача: HERMES-P0-01 + HERMES-P0-02.

Создай документацию docs/hermes:
- README.md
- playbooks/diagnose-runtime.md
- playbooks/fix-webui.md
- playbooks/fix-collector.md
- playbooks/fix-chatbot.md
- playbooks/security-audit.md
- playbooks/release-check.md

Затем предложи дополнение quick_commands для ~/.hermes/config.yaml:
- doctor-full
- routes
- errors
- git-status
- redis-scan
- ticker-count
- journal-age

Ограничения:
- не менять runtime-код WORED;
- не трогать .env;
- не печатать секреты;
- не выполнять destructive-команды;
- сначала показать план и список файлов.
ТЗ: Restore + RACHELLO Hermes Bridge
0. Цель

Восстановить WORED до последнего стабильного состояния и добавить безопасный режим, где один Telegram-бот RACHELLO умеет работать в двух контурах:

1. Криптобот WORED:
   - кнопки;
   - slash-команды;
   - market snapshot;
   - аналитика;
   - прогнозы;
   - существующий chatbot runtime.

2. Hermes admin assistant:
   - обычный текст от владельца;
   - постановка задач;
   - отчёты;
   - запуск whitelisted scripts;
   - без произвольного shell-доступа.

Важно:

Нельзя запускать второй polling-процесс на тот же Telegram bot token.
Нельзя делать отдельный xurl-polling поверх RACHELLO.
Нельзя считать RACHELLO внешним Hermes gateway.
Интеграция должна быть внутри активного chatbot runtime на aiogram 3.
1. Текущий диагноз
Подтверждённые проблемы
1. ~/.hermes/config.yaml повреждён:
   - дубли type: exec;
   - дубли command:;
   - неполная команда brief с пустым --symbols.

2. Hermes ложно сообщил, что запустил gateway/monitoring.

3. @wored_hermes_bot не существует.

4. RACHELLO сейчас обрабатывает /task как обычный крипто-AI-запрос.

5. Команды в Telegram не доходят до Hermes.

6. AI-routing криптобота частично падает на 429:
   Error code 429, no available resource package.

7. Есть риск, что в scripts/ и docs/hermes/playbooks/ появились мусорные или непроверенные файлы.
2. Общие ограничения

Hermes обязан соблюдать:

- не печатать секреты;
- не читать .env целиком;
- не использовать cat ~/.hermes/.env;
- не запускать второй Telegram polling на тот же bot token;
- не использовать xurl как постоянный bridge;
- не добавлять shell passthrough из Telegram;
- не выполнять произвольные команды из Telegram;
- не трогать collector ingestion;
- не менять webui;
- не менять docker-compose без отдельного подтверждения;
- не делать git push;
- не делать docker compose down -v;
- не удалять volumes;
- не удалять durable данные Postgres.
3. Этап A: аварийный rollback
A1. Сделать backup текущего состояния

Команды:

cd /mnt/d/WORED

mkdir -p .rollback_backups/hermes_restore_$(date +%Y%m%d_%H%M%S)

cp ~/.hermes/config.yaml .rollback_backups/hermes_restore_$(date +%Y%m%d_%H%M%S)/config.yaml.current 2>/dev/null || true
cp ~/.hermes/SOUL.md .rollback_backups/hermes_restore_$(date +%Y%m%d_%H%M%S)/SOUL.md.current 2>/dev/null || true

git status --short > .rollback_backups/git_status_before_restore.txt
git diff --stat > .rollback_backups/git_diff_stat_before_restore.txt
A2. Проверить git-состояние
cd /mnt/d/WORED
git status --short
git diff --stat

Hermes должен вывести:

- какие файлы изменены;
- какие файлы новые;
- какие относятся к runtime;
- какие относятся к docs/scripts;
- какие потенциально мусорные.
A3. Не откатывать всё слепо

Запрещено выполнять сразу:

git reset --hard
git clean -fd

Сначала нужен список файлов и подтверждение.

A4. Откатить только явно сломанные вещи

Первый обязательный откат:

~/.hermes/config.yaml

Нужно привести quick commands к валидному виду.

Проверить проблемные блоки:

grep -nA20 -B5 "risk-position\|brief" ~/.hermes/config.yaml

Должно стать так:

  risk-position:
    type: exec
    command: python scripts/risk_position.py --balance 1000 --risk-pct 1 --entry 62000 --stop 61000 --take 65000

  brief:
    type: exec
    command: python scripts/intelligence_brief.py --mode hourly --symbols BTCUSDT,ETHUSDT --format markdown

Удалить все дубли:

type: exec
type: exec
type: exec

и дубли:

command:
command:
A5. Проверить конфиг Hermes
hermes config check

Если команды нет:

python - <<'PY'
from pathlib import Path

p = Path.home() / ".hermes/config.yaml"
text = p.read_text(encoding="utf-8")

bad = False
for name in ["risk-position:", "brief:"]:
    idx = text.find(name)
    if idx == -1:
        print(f"{name} MISSING")
        bad = True
        continue
    chunk = text[idx:idx+500]
    print(f"---- {name}")
    print(chunk)
    if chunk.count("type: exec") > 1:
        print("BAD: duplicate type")
        bad = True
    if chunk.count("command:") > 1:
        print("BAD: duplicate command")
        bad = True

raise SystemExit(1 if bad else 0)
PY
4. Этап B: зафиксировать правильную архитектуру Telegram
B1. Новый принцип маршрутизации

В RACHELLO:

Кнопки Telegram callback_query:
→ всегда существующий криптобот WORED.

Slash-команды:
→ всегда существующий криптобот WORED, кроме явно зарезервированных Hermes-команд.

Обычный текст от admin Telegram ID:
→ Hermes assistant mode.

Обычный текст от не-admin:
→ существующее поведение криптобота или отказ, как было раньше.
B2. Зарезервированные Hermes-команды

Минимально:

/hermes
/hermes_on
/hermes_off
/hermes_status
/hermes_help
/task

Но лучше не заставлять тебя писать /task. Нужно сделать так:

Admin пишет обычным текстом:
"продолжи по плану"
"сделай отчёт"
"проверь webui"
"почини config"
→ это уходит в Hermes router.

Admin нажимает кнопки или пишет /market, /forecast, /start
→ это криптобот.
B3. Runtime path

Активная зона изменения:

chatbot/handlers/*
chatbot/main.py
chatbot/ai/*
chatbot/storage/*

Точную структуру Hermes должен определить сам через ls и grep.

Legacy-зоны не трогать:

chatbot/loader.py
chatbot/context/*
chatbot/ui/*
collector/alerts/detector.py
collector/scheduler/briefing.py
5. Этап C: добавить Hermes Text Router внутри chatbot
C1. Назначение

Создать модуль, который внутри chatbot принимает обычный admin-текст и отправляет его в безопасный Hermes command layer.

Предлагаемые файлы:

chatbot/handlers/hermes_admin.py
chatbot/services/hermes_bridge.py
docs/hermes/playbooks/rachello-hermes-bridge.md

Если в проекте уже есть другая структура handlers/services — адаптировать под неё, не создавать параллельный хаос.

C2. Правила маршрутизации

Псевдологика:

if message.from_user.id != ADMIN_TELEGRAM_ID:
    pass to existing crypto handlers

if message.text startswith "/":
    if command in HERMES_RESERVED_COMMANDS:
        route to Hermes
    else:
        pass to existing crypto command handlers

if message is callback/button:
    pass to existing crypto callback handlers

if admin sends plain text:
    route to Hermes natural language handler
C3. Что считается plain text для Hermes
"продолжи по плану"
"сделай P8"
"проверь логи"
"дай отчёт"
"почини конфиг"
"подготовь ТЗ"
C4. Что не должно идти в Hermes
/start
/menu
/market
/alerts
/predictions
/journal
/forecast
/calc
любые кнопки Telegram UI
callback_query
6. Этап D: безопасный Hermes Bridge
D1. Нельзя делать

Нельзя давать Telegram доступ к shell:

/shell ...
/exec ...
/run ...
bash ...
python -c ...
rm ...
docker ...
D2. Разрешённые действия на первом этапе

Hermes bridge должен быть whitelist-based.

Разрешить только:

status
brief
risk-position
signal-explainer
webui-check
runtime-snapshot
git-status
help
D3. Маппинг intent → команда

Пример:

"статус" / "проверь систему"
→ python scripts/runtime_doctor.sh или bash scripts/runtime_snapshot.sh

"дай brief" / "сводка"
→ python scripts/intelligence_brief.py --mode hourly --symbols BTCUSDT,ETHUSDT --format markdown

"расчёт риска"
→ python scripts/risk_position.py --balance 1000 --risk-pct 1 --entry 62000 --stop 61000 --take 65000

"проверь webui"
→ bash scripts/webui_check.sh

"что изменено"
→ git status --short

Команды должны быть заранее прописаны в Python-коде, а не собираться из пользовательского текста напрямую.

7. Этап E: отчёты обратно в Telegram
E1. Формат ответа

Каждый Hermes-ответ должен начинаться с task id:

#HERMES_TASK_20260501_173000

Status: OK
Command: webui-check
Result:
...
E2. Ограничение размера

Telegram limit учитывать:

- stdout обрезать до 3500 символов;
- если больше — отправлять summary и путь к локальному файлу отчёта;
- не отправлять огромные diff/logs целиком.
E3. Маскирование секретов

Перед отправкой ответа:

nvapi-* → ***MASKED***
Bearer ... → ***MASKED***
API_KEY=... → API_KEY=***MASKED***
TOKEN=... → TOKEN=***MASKED***
SECRET=... → SECRET=***MASKED***
PASSWORD=... → PASSWORD=***MASKED***
8. Этап F: исправить ошибку 429 AI-провайдера

Это отдельный дефект криптобота.

Симптом
Error code: 429
余额不足或无可用资源包,请充值。
Требование

Если AI-провайдер недоступен, RACHELLO не должен отвечать большим сломанным market-анализом или повторять текст. Он должен:

- показать короткое сообщение;
- предложить fallback;
- не ломать Hermes routing;
- не зацикливаться.
Минимальный текст
AI-провайдер временно недоступен: лимит/баланс.
Кнопки рынка и локальные команды доступны.
Для Hermes-задач напишите обычным текстом.
9. Тестирование
TEST-1: Hermes config восстановлен
grep -nA10 -B3 "risk-position\|brief" ~/.hermes/config.yaml
hermes config check || true

Ожидание:

- нет дублирующихся type;
- нет дублирующихся command;
- brief содержит symbols BTCUSDT,ETHUSDT;
- risk-position содержит command.
TEST-2: chatbot стартует
cd /mnt/d/WORED
docker compose up -d --build chatbot
docker compose logs chatbot --tail=120

Ожидание:

- chatbot Up;
- нет traceback;
- нет import error;
- bot polling/webhook работает как раньше.
TEST-3: кнопки криптобота не сломаны

В Telegram нажать кнопки:

Рынок
Аналитика
Прогнозы

Ожидание:

- кнопки отвечают существующим WORED-функционалом;
- не уходят в Hermes;
- callback_query не ломается.
TEST-4: slash-команды криптобота не сломаны

В Telegram:

/start
/menu
/market
/alerts

Ожидание:

- обрабатываются криптоботом;
- не уходят в Hermes.
TEST-5: обычный admin-текст уходит в Hermes

В Telegram от admin:

проверь webui

Ожидание:

#HERMES_TASK_<timestamp>
Status: OK
Command: webui-check
...
TEST-6: /task уходит в Hermes
/task проверь статус

Ожидание:

#HERMES_TASK_<timestamp>
Status: OK
Command: status
...
TEST-7: не-admin не может пользоваться Hermes

С другого Telegram ID:

проверь webui
/task status

Ожидание:

- Hermes не выполняется;
- либо обычный криптобот;
- либо отказ: admin only.
TEST-8: shell injection заблокирован

От admin:

/task rm -rf /
/task cat ~/.hermes/.env
/task docker compose down -v
/task bash -c "env"

Ожидание:

Status: REJECTED
Reason: command not allowed
TEST-9: секреты маскируются

Искусственно вернуть строку с секретным паттерном в тестовом режиме.

Ожидание:

nvapi-... не появляется;
Bearer ... не появляется;
TOKEN=... не появляется;

Проверка логов:

docker compose logs chatbot --tail=300 | grep -Ei "nvapi-|Bearer|TOKEN=|SECRET=|PASSWORD="

Ожидание:

ничего
TEST-10: 429 AI не ломает Hermes

Спровоцировать обычный AI-запрос, когда провайдер недоступен.

Ожидание:

- криптобот сообщает о лимите;
- обычный текст admin всё равно может идти в Hermes;
- /task работает.
10. Acceptance Criteria

Готово, если:

1. ~/.hermes/config.yaml восстановлен.
2. Нет дублирующихся quick command keys.
3. RACHELLO не использует xurl-polling как bridge.
4. Нет второго Telegram polling-процесса на тот же token.
5. Кнопки и slash-команды криптобота работают как раньше.
6. Обычный текст admin идёт в Hermes.
7. /task идёт в Hermes.
8. Не-admin не может выполнять Hermes-задачи.
9. Shell passthrough отсутствует.
10. Все команды Hermes whitelist-based.
11. Секреты маскируются.
12. 429 AI-провайдера не ломает Hermes-режим.
13. Docker chatbot стартует без traceback.
14. В Telegram приходит реальный отчёт #HERMES_TASK_<id>.
11. Готовый промпт для Hermes
Задача: RESTORE + RACHELLO Hermes Bridge.

Проблема:
- ~/.hermes/config.yaml повреждён дублирующимися quick_commands.
- Hermes ошибочно пытался использовать несуществующий @wored_hermes_bot.
- RACHELLO сейчас обрабатывает /task как обычный crypto AI message.
- Gateway/xurl-polling реально не работает как стабильный bridge.
- Нужно восстановить последнее рабочее состояние и добавить правильную интеграцию внутри chatbot runtime.

Цель:
1. Откатить повреждения Hermes config.
2. Не трогать рабочий crypto runtime без плана.
3. Добавить в RACHELLO режим:
   - кнопки и slash-команды = криптобот;
   - обычный текст от admin = Hermes;
   - /task от admin = Hermes;
   - всё от не-admin = без Hermes.

Активный runtime path:
- chatbot/handlers/*
- chatbot/main.py
- chatbot/services/* если есть
- scripts/*
- docs/hermes/playbooks/*

Legacy не трогать:
- chatbot/loader.py
- chatbot/context/*
- chatbot/ui/*
- collector/alerts/detector.py
- collector/scheduler/briefing.py

Создать/обновить:
- chatbot/handlers/hermes_admin.py или аналог по текущей структуре
- chatbot/services/hermes_bridge.py или аналог по текущей структуре
- docs/hermes/playbooks/rachello-hermes-bridge.md
- ~/.hermes/config.yaml только для исправления дублей

Требования:
- не запускать второй Telegram polling;
- не использовать xurl-polling;
- не использовать shell passthrough;
- whitelist commands only;
- admin only через ADMIN_TELEGRAM_ID;
- plain admin text -> Hermes;
- slash crypto commands -> existing crypto handlers;
- callback buttons -> existing crypto handlers;
- stdout limit 3500 chars;
- secrets masking;
- errors без traceback в Telegram;
- логировать task_id, intent, status, duration_ms без секретов.

Сначала дай:
PLAN
FILES
RISK
ROLLBACK
TESTS

До patch выполнить:
git status --short
git diff --stat
grep -nA20 -B5 "risk-position\|brief" ~/.hermes/config.yaml

После patch выполнить:
hermes config check || true
docker compose up -d --build chatbot
docker compose logs chatbot --tail=120

Telegram tests:
1. /start -> crypto bot
2. кнопка Рынок -> crypto bot
3. /market -> crypto bot
4. обычный admin text "проверь webui" -> #HERMES_TASK
5. /task статус -> #HERMES_TASK
6. /task cat ~/.hermes/.env -> REJECTED
7. non-admin /task status -> rejected/admin only

REPORT:
- что восстановлено;
- какие файлы изменены;
- какие тесты прошли;
- подтверждение, что второй polling не запускался;
- подтверждение, что xurl-polling не используется;
- подтверждение, что секреты не выводились.
12. Важное решение

Не надо делать отдельный @wored_hermes_bot, если ты хочешь работать через RACHELLO.

Правильная модель:

Один Telegram bot token.
Один активный chatbot polling/webhook runtime.
Внутри него router:
- кнопки/slash-команды → WORED crypto bot;
- обычный admin text → Hermes bridge.
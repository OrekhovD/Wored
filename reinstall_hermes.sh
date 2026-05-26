#!/bin/bash
# set -e

cd /mnt/d/WORED

echo "1. Skipping killing (no processes expected)..."
#(pkill -f "hermes" || true); echo "Debug: pkill 1 done"
#(pkill -f "hermes_task_worker.py" || true); echo "Debug: pkill 2 done"
#(pkill -f "xurl" || true); echo "Debug: pkill 3 done"

echo "2. Backing up..."
BACKUP_DIR="$HOME/hermes_reinstall_backup_$(date +%Y%m%d_%H%M%S)"; echo "Debug: backup dir set"
mkdir -p "$BACKUP_DIR"; echo "Debug: mkdir done"
cp -a ~/.hermes "$BACKUP_DIR/.hermes.backup" 2>/dev/null || true; echo "Debug: cp done"

mkdir -p ~/.hermes_secrets_backup
chmod 700 ~/.hermes_secrets_backup
cp ~/.hermes/.env ~/.hermes_secrets_backup/hermes.env.backup.$(date +%Y%m%d_%H%M%S) 2>/dev/null || true
chmod 600 ~/.hermes_secrets_backup/* 2>/dev/null || true

echo "3. Removing old cache and configs..."
find ~/.hermes -maxdepth 1 -type f -delete 2>/dev/null || true
rm -rf ~/.config/hermes ~/.cache/hermes ~/.local/share/hermes ~/.local/state/hermes
rm -f ~/.local/bin/hermes ~/.local/bin/hermes-agent

echo "4. Uninstalling from pipx/pip..."
pipx uninstall hermes-agent 2>/dev/null || true
pipx uninstall hermes 2>/dev/null || true
python3 -m pip uninstall -y hermes-agent hermes hermes-cli 2>/dev/null || true

echo "5. Restoring secret env..."
mkdir -p ~/.hermes
chmod 700 ~/.hermes

LATEST_ENV=$(ls -t ~/.hermes_secrets_backup/hermes.env.backup.* 2>/dev/null | head -1)
if [ -n "$LATEST_ENV" ]; then
  cp "$LATEST_ENV" ~/.hermes/.env
  chmod 600 ~/.hermes/.env
  echo "Hermes .env restored from $LATEST_ENV"
fi

echo "6. Creating new config.yaml..."
cat << 'EOF' > ~/.hermes/config.yaml
cwd: /mnt/d/WORED

model:
  default: qwen/qwen-2.5-coder-32b-instruct
  provider: nvidia

# Псевдоним для удобства
# qwen3-coder-plus -> qwen/qwen-2.5-coder-32b-instruct


safety:
  no_secret_printing: true
  require_plan_before_patch: true
  forbid_destructive_commands: true

quick_commands:
  ps:
    type: exec
    command: docker compose ps

  health:
    type: exec
    command: curl -fsS http://localhost:8080/ >/dev/null && echo "webui OK" || echo "webui FAIL"

  logs-chatbot:
    type: exec
    command: docker compose logs chatbot --tail=120

  logs-webui:
    type: exec
    command: docker compose logs webui --tail=120

  git-status:
    type: exec
    command: git status --short && git diff --stat

  config-paths:
    type: exec
    command: grep -nE "cwd:|model:|quick_commands:" ~/.hermes/config.yaml

  doctor-runtime:
    type: exec
    command: docker compose ps && curl -fsS http://localhost:8080/ >/dev/null && echo "runtime OK"
EOF
chmod 600 ~/.hermes/config.yaml

echo "7. Creating new SOUL.md..."
cat << 'EOF' > ~/.hermes/SOUL.md
# Hermes SOUL — WORED Clean Start

Ты работаешь с проектом WORED из WSL.

Рабочая директория:
`/mnt/d/WORED`

Модель:
`qwen3-coder-plus`

Текущий режим:
- Hermes используется как console/TUI инженерный агент.
- Старый локальный контекст, память, кэш и история Hermes сброшены.
- Все знания о проекте нужно получать заново из файлов проекта.
- Перед выводами проверяй факты через чтение файлов и команды диагностики.
- Не утверждай, что процесс/bridge/gateway работает, пока это не подтверждено тестом.

АРХИТЕКТУРА АГЕНТОВ:
1. LEAD (Thinking): Qwen 3.6 Plus (в расширении Antigravity/Companion). Отвечает за логику, архитектуру и декомпозицию.
2. EXECUTOR (Action): Qwen Code / Hermes (qwen3-coder-plus). Отвечает за написание кода, запуск тестов и работу с файлами.
3. Твоя задача — строго следовать плану, подготовленному LEAD-агентом. Если плана нет, сначала запроси его.


WORED runtime:
- chatbot — Telegram UI на aiogram 3.
- collector — HTX ingestion и индикаторы.
- webui — FastAPI dashboard.
- postgres — история, journal, forecasts.
- redis — cache/pubsub/queue.

Важно:
- В проекте могут быть файлы интеграции Hermes.
- Не удаляй их без отдельного приказа.
- Если видишь chatbot/hermes bridge, сначала проверь runtime path и Docker boundary.
- Chatbot работает в Docker container.
- Hermes CLI работает на WSL host.
- Нельзя считать, что container видит host binary.

Перед любым patch:
1. PLAN
2. FILES
3. RISK
4. TESTS
5. ждать подтверждение

Запрещено:
- печатать секреты;
- читать `.env` целиком;
- делать `docker compose down -v`;
- удалять volumes;
- менять `/mnt/d/WORED` на другой cwd;
- запускать второй Telegram polling без отдельного ТЗ;
- использовать xurl-polling без отдельного ТЗ;
- писать ключи через `echo >> ~/.hermes/.env`;
- делать `git reset --hard` без отдельного подтверждения.

Legacy-зоны без явного запроса не трогать:
- chatbot/loader.py
- chatbot/context/*
- chatbot/ui/*
- collector/alerts/detector.py
- collector/scheduler/briefing.py
EOF
chmod 600 ~/.hermes/SOUL.md

echo "Done preparing."

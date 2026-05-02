#!/bin/bash
# Финализация установки Hermes (шаги 2-7 из setup.sh)
set -euo pipefail

HERMES_DIR="$HOME/.hermes"
HERMES_ENV="$HERMES_DIR/.env"
HERMES_CONFIG="$HERMES_DIR/config.yaml"
PROJECT_DIR="/mnt/d/WORED"
PROJECT_ENV="$PROJECT_DIR/.env"

GREEN='\033[0;32m'; NC='\033[0m'
ok() { echo -e "${GREEN}✓${NC} $1"; }

# ─── Шаг 2: .env ────────────────────────────────────────────────────────────
touch "$HERMES_ENV"
ok "Шаг 2: ~/.hermes/.env создан"

# ─── Шаг 3: Синхронизация ключей ────────────────────────────────────────────
set -a
source <(grep -v '^#' "$PROJECT_ENV" | grep -v '^[[:space:]]*$' | grep '=')
set +a

upsert() {
    local key="$1" val="${2:-}"
    [ -z "$val" ] && return 0
    local tmp
    tmp=$(mktemp)
    grep -v "^${key}=" "$HERMES_ENV" > "$tmp" 2>/dev/null || true
    echo "${key}=${val}" >> "$tmp"
    mv "$tmp" "$HERMES_ENV"
    ok "  $key"
}

upsert "ZAI_API_KEY"        "${GLM_API_KEY:-}"
upsert "MINIMAX_API_KEY"    "${MINIMAX_API_KEY:-}"
upsert "DASHSCOPE_API_KEY"  "${DASHSCOPE_API_KEY:-}"
upsert "PERPLEXITY_API_KEY" "${PERPLEXITY_API_KEY:-}"
upsert "GOOGLE_API_KEY"     "${GOOGLE_API_KEY:-}"
upsert "TELEGRAM_BOT_TOKEN" "${HERMES_TELEGRAM_TOKEN:-}"
upsert "TELEGRAM_ADMIN_ID"  "${HERMES_ADMIN_TELEGRAM_ID:-}"
ok "Шаг 3: Ключи синхронизированы"

# ─── Шаг 4: config.yaml ─────────────────────────────────────────────────────
if [ -n "${MINIMAX_API_KEY:-}" ]; then
    PROV="minimax"; MODEL="MiniMax-M2.7"; BURL="https://api.minimax.io/v1"
elif [ -n "${GLM_API_KEY:-}" ]; then
    PROV="zai";     MODEL="glm-z1-flash"; BURL="https://api.z.ai/api/coding/paas/v4"
elif [ -n "${DASHSCOPE_API_KEY:-}" ]; then
    PROV="alibaba"; MODEL="qwen-plus";    BURL=""
elif [ -n "${GOOGLE_API_KEY:-}" ]; then
    PROV="google";  MODEL="gemini-3-flash-preview"; BURL=""
else
    PROV="zai"; MODEL="glm-4-flash-250414"; BURL="https://open.bigmodel.cn/api/paas/v4"
fi

BURL_LINE=""
[ -n "$BURL" ] && BURL_LINE="  base_url: $BURL"

cat > "$HERMES_CONFIG" << YAML
# Hermes Agent — конфиг WORED
# Сгенерирован: $(date '+%Y-%m-%d %H:%M')
# Проект: $PROJECT_DIR

model:
  default: $MODEL
  provider: $PROV
$BURL_LINE

auxiliary:
  compression:
    provider: zai
    model: glm-4-flash-250414
  session_search:
    provider: zai
    model: glm-4-flash-250414
    max_concurrency: 2

terminal:
  backend: local
  cwd: "$PROJECT_DIR"
  timeout: 300

timezone: "Asia/Bangkok"

memory:
  memory_enabled: true
  user_profile_enabled: true
  memory_char_limit: 3000

agent:
  max_turns: 120
  reasoning_effort: "medium"
  tool_use_enforcement: "auto"

display:
  tool_progress: all
  show_cost: true

streaming:
  enabled: true
  transport: edit
  edit_interval: 0.5

quick_commands:
  up:
    type: exec
    command: cd $PROJECT_DIR && docker compose up -d && docker compose ps
  down:
    type: exec
    command: cd $PROJECT_DIR && docker compose down
  restart:
    type: exec
    command: cd $PROJECT_DIR && docker compose restart
  ps:
    type: exec
    command: cd $PROJECT_DIR && docker compose ps
  build:
    type: exec
    command: cd $PROJECT_DIR && docker compose build --no-cache
  logs:
    type: exec
    command: cd $PROJECT_DIR && docker compose logs --tail=60
  lc:
    type: exec
    command: cd $PROJECT_DIR && docker compose logs collector --tail=80
  lb:
    type: exec
    command: cd $PROJECT_DIR && docker compose logs chatbot --tail=80
  lw:
    type: exec
    command: cd $PROJECT_DIR && docker compose logs webui --tail=80
  tickers:
    type: exec
    command: docker exec htx_trading_bot_redis redis-cli keys "ticker:*" 2>/dev/null || echo "Redis не запущен"
  journal:
    type: exec
    command: docker exec htx_trading_bot_redis redis-cli get ai:journal:latest 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "Нет данных"
  candles:
    type: exec
    command: docker exec htx_trading_bot_postgres psql -U bot -d trading -c "SELECT symbol, interval, COUNT(*) as n, MAX(open_time) as latest FROM candles GROUP BY symbol, interval ORDER BY symbol, interval;" 2>/dev/null || echo "Postgres не запущен"
  alerts:
    type: exec
    command: docker exec htx_trading_bot_postgres psql -U bot -d trading -c "SELECT id, symbol, threshold, triggered, timestamp FROM spike_alerts ORDER BY timestamp DESC LIMIT 10;" 2>/dev/null
  dbstats:
    type: exec
    command: docker exec htx_trading_bot_postgres psql -U bot -d trading -c "SELECT tablename, n_live_tup as rows FROM pg_stat_user_tables ORDER BY n_live_tup DESC;" 2>/dev/null
  health:
    type: exec
    command: curl -s http://localhost:8080/api/health 2>/dev/null | python3 -m json.tool || echo "WebUI не доступен"

mcp_servers:
  filesystem:
    command: npx
    args:
      - "-y"
      - "@modelcontextprotocol/server-filesystem"
      - "$PROJECT_DIR"
    env: {}
YAML

ok "Шаг 4: config.yaml записан (провайдер: $PROV / $MODEL)"

# ─── Шаг 5: SOUL.md ─────────────────────────────────────────────────────────
cat > "$HERMES_DIR/SOUL.md" << 'SOUL'
# Hermes — Технический оркестратор WORED

## Роль
Я главный технический агент проекта WORED — торгового Telegram-бота
на базе HTX WebSocket. Управляю Docker-инфраструктурой, диагностирую проблемы,
координирую разработку, мониторю работу системы.

## Стиль
Прямой, технический, без воды. Факты и команды вместо объяснений.
Если нужна диагностика — сначала собираю данные, потом действую.
Перед деструктивными операциями (down -v, rm -rf) предупреждаю явно.

## Язык
Русский для общения. Английский для кода, команд, имён файлов.

## Что я знаю о проекте
- Стек: Python 3.9/3.11, asyncio, aiogram, asyncpg, Redis, PostgreSQL 16
- Биржа: HTX (Huobi) — только спот и USDT-маржинальные пары
- AI-роутинг: GLM-5.1 (анализ), Qwen3.6 (код), Perplexity (новости)
- Docker: 5 сервисов — postgres, redis, collector, chatbot, webui
- HTX WS особенность: GZIP-сжатие + кастомный ping/pong протокол
- WebUI: FastAPI + Jinja2 + Lightweight Charts (Command Deck v2)

## Быстрые команды
/up, /down, /restart, /ps, /build, /logs, /lc, /lb, /lw,
/tickers, /journal, /candles, /alerts, /dbstats, /health

## Правила
- Никогда не записывать API-ключи в код или логи
- При изменении .env напоминать про `docker compose restart`
- `docker compose down -v` = удаление данных, всегда предупреждать
- Не трогать chatbot и collector логику без явного запроса
SOUL

ok "Шаг 5: SOUL.md создан"

# ─── Шаг 6: PATH в .bashrc ──────────────────────────────────────────────────
grep -q '.local/bin' "$HOME/.bashrc" 2>/dev/null || echo 'export PATH="$HOME/.local/bin:$HOME/.hermes/node/bin:$PATH"' >> "$HOME/.bashrc"
ok "Шаг 6: PATH добавлен в .bashrc"

# ─── Шаг 7: Утилиты ─────────────────────────────────────────────────────────
cat > "$PROJECT_DIR/hermes/sync-keys.sh" << 'SYNC'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ENV="$(dirname "$SCRIPT_DIR")/.env"
HERMES_ENV="$HOME/.hermes/.env"
[ -f "$PROJECT_ENV" ] || { echo "Нет .env"; exit 1; }
set -a
source <(grep -v '^#' "$PROJECT_ENV" | grep -v '^$' | grep '=')
set +a
up() {
    local k="$1" v="${2:-}"
    [ -z "$v" ] && return
    local t; t=$(mktemp)
    grep -v "^${k}=" "$HERMES_ENV" > "$t" 2>/dev/null || true
    echo "${k}=${v}" >> "$t"
    mv "$t" "$HERMES_ENV"
    echo "✓ $k"
}
up "ZAI_API_KEY"        "${GLM_API_KEY:-}"
up "MINIMAX_API_KEY"    "${MINIMAX_API_KEY:-}"
up "DASHSCOPE_API_KEY"  "${DASHSCOPE_API_KEY:-}"
up "PERPLEXITY_API_KEY" "${PERPLEXITY_API_KEY:-}"
up "GOOGLE_API_KEY"     "${GOOGLE_API_KEY:-}"
up "TELEGRAM_BOT_TOKEN" "${HERMES_TELEGRAM_TOKEN:-}"
up "TELEGRAM_ADMIN_ID"  "${HERMES_ADMIN_TELEGRAM_ID:-}"
echo "Sync готово."
SYNC

cat > "$PROJECT_DIR/hermes/start.sh" << 'STARTSH'
#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
export PATH="$HOME/.local/bin:$HOME/.hermes/node/bin:$PATH"
bash "$SCRIPT_DIR/sync-keys.sh"
cd "$PROJECT_DIR"
echo ""
echo "Hermes Agent | Проект: $PROJECT_DIR"
echo "Команды: /up /down /ps /logs /lc /lb /lw /tickers /journal /candles /alerts /health"
echo ""
hermes --tui
STARTSH

chmod +x "$PROJECT_DIR/hermes/sync-keys.sh" "$PROJECT_DIR/hermes/start.sh"
ok "Шаг 7: sync-keys.sh и start.sh созданы"

# ─── Итог ────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}  Финализация завершена!${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Проверка: hermes --version"
echo "  Запуск:   bash /mnt/d/WORED/hermes/start.sh"
echo ""

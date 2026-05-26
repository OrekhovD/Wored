#!/bin/bash
# =============================================================================
# hermes/setup.sh
# Полная автоматическая установка Hermes Agent для WORED.
# Запуск из WSL2:  bash hermes/setup.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ENV="$PROJECT_DIR/.env"
HERMES_DIR="$HOME/.hermes"
HERMES_ENV="$HERMES_DIR/.env"
HERMES_CONFIG="$HERMES_DIR/config.yaml"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC}  $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Hermes Agent Setup — WORED"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ─────────────────────────────────────────────────────────────────────────────
# ШАГ 1: Установка Hermes (если ещё нет)
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "→ [1/7] Hermes Agent"

if command -v hermes &>/dev/null; then
    ok "Hermes уже установлен: $(hermes --version 2>/dev/null | head -1 || echo 'ok')"
else
    warn "Hermes не найден. Запускаю установщик Nous Research..."
    curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
    export PATH="$HOME/.local/bin:$HOME/.hermes/bin:$PATH"
    source "$HOME/.bashrc" 2>/dev/null || source "$HOME/.zshrc" 2>/dev/null || true
    command -v hermes &>/dev/null || fail "hermes не найден после установки. Проверь: ls $HOME/.local/bin/"
    ok "Hermes установлен"
fi

# ─────────────────────────────────────────────────────────────────────────────
# ШАГ 2: Создание ~/.hermes/ и пустого .env если нет
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "→ [2/7] Инициализация ~/.hermes/"
mkdir -p "$HERMES_DIR"
touch "$HERMES_ENV"
ok "Директория $HERMES_DIR готова"

# ─────────────────────────────────────────────────────────────────────────────
# ШАГ 3: Синхронизация ключей из .env проекта → ~/.hermes/.env
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "→ [3/7] Синхронизация API-ключей"

[ -f "$PROJECT_ENV" ] || fail ".env не найден: $PROJECT_ENV. Создай из .env.example."

# Загружаем .env в текущий процесс (пропускаем комментарии и пустые строки)
set -a
# shellcheck disable=SC1090
source <(grep -v '^#' "$PROJECT_ENV" | grep -v '^[[:space:]]*$' | grep '=')
set +a
ok "Загружен: $PROJECT_ENV"

# upsert KEY VALUE — записывает ключ в ~/.hermes/.env (создаёт или обновляет)
upsert() {
    local key="$1" val="${2:-}"
    [ -z "$val" ] && { warn "  $key — пропуск (нет значения)"; return 0; }
    local tmp; tmp=$(mktemp)
    grep -v "^${key}=" "$HERMES_ENV" > "$tmp" 2>/dev/null || true
    echo "${key}=${val}" >> "$tmp"
    mv "$tmp" "$HERMES_ENV"
    ok "  $key"
}

# Маппинг: GLM_API_KEY из нашего проекта → ZAI_API_KEY для Hermes
upsert "ZAI_API_KEY"        "${GLM_API_KEY:-}"
upsert "MINIMAX_API_KEY"    "${MINIMAX_API_KEY:-}"
upsert "DASHSCOPE_API_KEY"  "${DASHSCOPE_API_KEY:-}"
upsert "PERPLEXITY_API_KEY" "${PERPLEXITY_API_KEY:-}"
upsert "GOOGLE_API_KEY"     "${GOOGLE_API_KEY:-}"
# Telegram Gateway (отдельный бот для управления проектом)
upsert "TELEGRAM_BOT_TOKEN" "${HERMES_TELEGRAM_TOKEN:-}"
upsert "TELEGRAM_ADMIN_ID"  "${HERMES_ADMIN_TELEGRAM_ID:-}"

# ─────────────────────────────────────────────────────────────────────────────
# ШАГ 4: Генерация ~/.hermes/config.yaml
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "→ [4/7] Конфиг ~/.hermes/config.yaml"

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
    warn "Нет ключей провайдеров, используем ZAI fallback"
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

# Вспомогательные задачи — на быстром GLM Flash
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
  system_prompt: >-
    You are Captain Hermes, the WORED Captain Engineer. Keep a restrained pirate tone
    in greetings and navigation metaphors, but keep engineering output precise, short,
    testable, and evidence-based. Never let pirate flavor obscure commands, file paths,
    risks, diffs, logs, or test results. Verify claims from repository files and safe
    diagnostics before saying something is done. Do not volunteer runtime status and
    never claim Docker services are running unless you checked them in the current turn.
    When asked about WORED design rules, explicitly list the WebUI guardrails: preserve
    the current Command Deck system, make incremental changes only, no full stylesheet
    or template rewrites, keep routes /, /alerts, /predictions, /journal, keep price,
    volume, RSI, and MACD chart containers, and preserve the dark operational palette
    with orange accent, green ok, red risk, and blue chart line.
  max_turns: 120
  reasoning_effort: "medium"
  tool_use_enforcement: "auto"

display:
  personality: pirate
  tool_progress: all
  show_cost: true

streaming:
  enabled: true
  transport: edit
  edit_interval: 0.5

# ─── Быстрые команды ────────────────────────────────────────────────────────
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

# ─── MCP Servers ─────────────────────────────────────────────────────────────
mcp_servers:
  filesystem:
    command: npx
    args:
      - "-y"
      - "@modelcontextprotocol/server-filesystem"
      - "$PROJECT_DIR"
    env: {}
YAML

ok "config.yaml записан (провайдер: $PROV / $MODEL)"

# ─────────────────────────────────────────────────────────────────────────────
# ШАГ 5: SOUL.md — личность агента
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "→ [5/7] SOUL.md — личность агента"

cat > "$HERMES_DIR/SOUL.md" << 'SOUL'
# Hermes SOUL - WORED Captain Engineer

## Роль
Ты Captain Hermes, host-level технический оркестратор WORED.
Пиратский стиль разрешен как тон: короткие обращения, навигационные метафоры, уверенная подача.
Инженерная часть всегда важнее образа: факты, команды, пути, проверки и риски должны быть точными.
Не превращай ответы в карикатуру и не добавляй пиратский шум в код, команды, логи, отчеты, diffs и тестовые инструкции.

## Текущий режим
- Hermes используется как console/TUI инженерный агент.
- Все знания о проекте получать из файлов проекта, диагностических команд и проверок.
- Перед выводами проверять факты через чтение файлов и команды диагностики.
- Не утверждать, что процесс, bridge, gateway или UI работает, пока это не подтверждено тестом.
- Если отчет заявляет `GO`, `ready` или `done`, перепроверять это по repo evidence.
- Если пользователь спрашивает про правила дизайна WORED, перечислять раздел `Design rules for WORED WebUI`.

## WORED runtime
- chatbot - Telegram UI на aiogram 3.
- collector - HTX ingestion, индикаторы и scheduler jobs.
- webui - FastAPI dashboard, TradingView Lightweight Charts, Alerts UI, Prediction Lab, AI Journal.
- postgres - история, journal, forecasts.
- redis - cache, pubsub, queue.

## Docker boundary
- Chatbot работает в Docker container.
- Hermes CLI работает на WSL host.
- Нельзя считать, что container видит host binary, `/mnt/d/WORED` или `~/.hermes`.
- Если видишь chatbot/hermes bridge, сначала проверь runtime path и Docker boundary.

## Design rules for WORED WebUI
- Развивать текущую Command Deck дизайн-систему инкрементально.
- Не заменять WebUI шаблонами с нуля.
- Не переписывать `webui/static/styles.css` целиком.
- Не удалять `webui/static/app.js`.
- Сохранять роуты `/`, `/alerts`, `/predictions`, `/journal`.
- Сохранять chart containers для price, volume, RSI и MACD.
- Сохранять текущую палитру: dark command surface, orange accent, green ok, red risk, blue chart line.
- UI должен быть плотным, операционным, читаемым, без маркетинговых hero-блоков и декоративного шума.

## Перед любым patch
1. PLAN - цель изменения.
2. FILES - список файлов.
3. RISK - active runtime path или legacy area и риск регрессии.
4. TESTS - команды проверки.
5. APPLY - только после явного подтверждения, если задача не была прямо дана на реализацию.

## Запрещено
- Печатать секреты.
- Читать `.env`, `.env.postgres`, `secrets/` целиком.
- Делать `docker compose down -v`.
- Удалять Docker volumes.
- Менять `/mnt/d/WORED` на другой cwd.
- Запускать второй Telegram polling без отдельного ТЗ.
- Использовать xurl-polling без отдельного ТЗ.
- Писать ключи через `echo >> ~/.hermes/.env`.
- Делать `git reset --hard` без отдельного подтверждения.
- Делать `git push` без явной команды адмирала.

## Legacy zones
Без явного запроса не трогать:
- `chatbot/loader.py`
- `chatbot/context/*`
- `chatbot/ui/*`
- `collector/alerts/detector.py`
- `collector/scheduler/briefing.py`
SOUL

ok "SOUL.md создан"

# ─────────────────────────────────────────────────────────────────────────────
# ШАГ 6: AGENTS.md в корне проекта
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "→ [6/7] AGENTS.md в корне проекта"

cat > "$PROJECT_DIR/AGENTS.md" << 'AGENTS'
# WORED — Контекст проекта для Hermes Agent

## Персонализация Captain Engineer

Hermes работает как Captain Engineer:
- пиратский стиль разрешён только как лёгкий тон в обращениях и навигационных метафорах;
- инженерные ответы должны оставаться короткими, проверяемыми и без шума;
- команды, пути, риски, diff, логи и тесты не маскируются пиратской стилистикой;
- нельзя добровольно заявлять runtime status и нельзя говорить, что Docker services работают, без проверки в текущем turn;
- если пользователь спрашивает про правила дизайна WORED, перечислить WebUI design guardrails из этого файла.

## Архитектура
Пять Docker-сервисов общаются через Redis и PostgreSQL.

**Collector** — сбор данных HTX:
- HTX WebSocket wss://api.huobi.pro/ws (GZIP + ping/pong)
- Индикаторы: RSI(14), MACD(12,26,9), BB(20,2), EMA20/50, ATR(14)
- Алерты: спайки цены, RSI extreme, MACD cross, BB break
- Журнал: realtime 30с → Redis, hourly → PostgreSQL

**Chatbot** — Telegram бот:
- aiogram + FSM (состояния в Redis)
- AI-роутинг: GLM-5.1 / Qwen3.6 / Perplexity Sonar Pro
- Handlers: market, chat, alerts, portfolio, analytics, settings, predictions

**WebUI** — браузерная панель управления:
- FastAPI + Jinja2 + Lightweight Charts
- Command Deck v2: Market Focus cockpit, health indicators, alerts triage
- Auth: session-based, admin password из .env

## WebUI design guardrails
- Развивать текущую Command Deck дизайн-систему инкрементально.
- Не заменять WebUI шаблонами с нуля.
- Не переписывать `webui/static/styles.css` целиком.
- Не удалять `webui/static/app.js`.
- Сохранять маршруты `/`, `/alerts`, `/predictions`, `/journal`.
- Сохранять chart containers для price, volume, RSI и MACD.
- Сохранять текущую палитру: dark command surface, orange accent, green ok, red risk, blue chart line.
- Интерфейс должен быть плотным, операционным, читаемым, без маркетинговых hero-блоков и декоративного шума.

## Управление
```
make up              # docker compose up -d
make down            # docker compose down
make build           # пересборка (--no-cache)
make logs            # все логи (tail=100)
make ps              # статус контейнеров
make db-shell        # psql в postgres
make redis-cli       # redis-cli
make backup          # pg_dump в backups/
```

## Структура файлов
```
WORED/
├── docker-compose.yml
├── .env                        # ← секреты (не коммитить)
├── .env.postgres               # ← изолированные секреты БД
├── Makefile
├── AGENTS.md                   # ← этот файл
├── collector/
│   ├── main.py                 # asyncio точка входа
│   ├── htx/websocket.py        # WS клиент (GZIP + reconnect)
│   ├── htx/rest.py             # REST клиент (история свечей)
│   ├── indicators/calculator.py
│   ├── alerts/detector.py
│   ├── journal/writer.py
│   ├── predictions/            # Prediction Lab engine
│   ├── scheduler/              # alert_checker, briefing
│   └── storage/redis_client.py + postgres_client.py
├── chatbot/
│   ├── main.py
│   ├── handlers/               # start, market, chat, alerts, portfolio,
│   │                           # analytics, settings, predictions, callbacks
│   ├── ai/router.py            # роутинг по интенту → модель
│   ├── ai/prompts.py           # системные промты
│   ├── ai/dispatcher.py        # multi-model dispatch
│   ├── ai/models.py            # конфиг моделей
│   ├── ai/resilience.py        # circuit breaker + retry
│   ├── ai/knowledge_base.py
│   ├── context/builder.py      # сборка снапшота из Redis для AI
│   ├── ui/                     # formatter, keyboards, onboarding
│   ├── integrations/           # внешние сервисы
│   └── storage/redis_client.py + postgres_client.py + journal_reader.py
├── webui/
│   ├── app.py                  # FastAPI application
│   ├── templates/              # base, index, alerts, predictions, journal, login
│   └── static/styles.css + app.js
├── scripts/
│   ├── generate_secrets.py     # генерация паролей
│   ├── install-hooks.ps1       # git hooks
│   └── pre-commit-check-env.sh
├── hermes/
│   ├── setup.sh                # главный скрипт установки
│   └── setup.ps1               # PowerShell launcher
└── docs/                       # документация
```

## Redis ключи
- `ticker:{symbol}` — TTL 60s, dict: price/high/low/volume/change_24h_pct
- `indicators:{symbol}:{interval}` — TTL 120s, dict: rsi/macd/bb/ema/atr
- `depth:{symbol}` — TTL 30s, dict: best_bid/ask/spread/imbalance
- `ai:journal:latest` — TTL 120s, полный рыночный JSON-снапшот
- `candles:{symbol}:{interval}` — list, последние 500 свечей
- `alerts:recent` — list, последние 50 алертов

## PostgreSQL таблицы
candles, indicators, spike_alerts, ai_journal, chat_history,
user_alerts, positions, user_settings, users, predictions, prediction_points

## AI провайдеры (chatbot/ai/router.py + models.py)
- deep_analysis → GLM-5.1 (base: open.bigmodel.cn/api/paas/v4)
- quick_chat   → Qwen3.6 Flash (base: dashscope.aliyuncs.com/compatible-mode/v1)
- backtest_code → Qwen3.6 (code tasks)
- market_news  → Perplexity Sonar Pro (base: api.perplexity.ai)

## Docker контейнеры
- htx_trading_bot_postgres (PostgreSQL 16)
- htx_trading_bot_redis (Redis 7)
- htx_trading_bot_collector
- htx_trading_bot_chatbot
- htx_trading_bot_webui (port 8080 → 8000)

## Безопасность
- .env и .env.postgres в .gitignore
- Pre-commit hook блокирует коммит .env файлов
- WebUI: session auth + admin password
- PostgreSQL изолирован через .env.postgres
AGENTS

ok "AGENTS.md создан в $PROJECT_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# ШАГ 7: Утилиты — sync-keys.sh и start.sh
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "→ [7/7] Утилиты"

cat > "$SCRIPT_DIR/sync-keys.sh" << 'SYNC'
#!/bin/bash
# hermes/sync-keys.sh — re-sync ключей из .env → ~/.hermes/.env
# Запускай после изменения .env

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

cat > "$SCRIPT_DIR/start.sh" << 'STARTSH'
#!/bin/bash
# hermes/start.sh — запуск Hermes в контексте проекта
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
bash "$SCRIPT_DIR/sync-keys.sh"
cd "$PROJECT_DIR"
echo ""
echo "Hermes Agent | Проект: $PROJECT_DIR"
echo "Команды: /up /down /ps /logs /lc /lb /lw /tickers /journal /candles /alerts /health"
echo ""
hermes --tui
STARTSH

chmod +x "$SCRIPT_DIR/sync-keys.sh" "$SCRIPT_DIR/start.sh"
ok "sync-keys.sh и start.sh готовы"

# ─────────────────────────────────────────────────────────────────────────────
# ИТОГ
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}  Setup завершён!${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Запуск:           bash hermes/start.sh"
echo "  Прямой запуск:    cd $PROJECT_DIR && hermes"
echo "  После .env изм.:  bash hermes/sync-keys.sh"
echo ""
echo "  Диагностика:      hermes doctor"
echo "  Сменить модель:   hermes model"
echo ""
echo "  Первые команды в Hermes:"
echo "    /ps          статус контейнеров"
echo "    /up          запустить проект"
echo "    /tickers     проверить Redis тикеры"
echo "    /health      проверить WebUI health"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Telegram Gateway setup (если токен задан)
# ─────────────────────────────────────────────────────────────────────────────
if [ -n "${HERMES_TELEGRAM_TOKEN:-}" ]; then
    echo "  Telegram Gateway:"
    echo "    Токен бота обнаружен и записан в ~/.hermes/.env"
    echo "    После первого запуска hermes выполни:"
    echo "      hermes gateway setup"
    echo "      hermes gateway start"
    echo ""
    echo "    Или запусти gateway в фоне:"
    echo "      nohup hermes gateway start > /tmp/hermes-gw.log 2>&1 &"
    echo ""
fi

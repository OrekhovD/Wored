# Hermes Agent — Полный гайд по интеграции в htx-trading-bot

## Что происходит и зачем

Hermes Agent от Nous Research — это CLI/Telegram-агент, который умеет управлять
проектами через AI: запускать shell-команды, читать файлы, управлять Docker,
и при этом использовать любой LLM-провайдер. Нативно поддерживает **все** наши
провайдеры: MiniMax, GLM/Z.AI, Qwen/DashScope.

После интеграции ты получаешь такую картину:

```
HOST (WSL2 / Linux)
│
├── hermes  ←  управляет всем проектом (CLI + опционально Telegram)
│   ├── читает API-ключи из htx-trading-bot/.env  (без дублирования)
│   ├── видит все файлы проекта через AGENTS.md-контекст
│   ├── запускает Docker-команды напрямую  (/up, /logs, /ps)
│   └── читает Redis/PostgreSQL через make-команды
│
└── Docker Compose  ←  сам проект работает как раньше
    ├── postgres
    ├── redis
    ├── collector   (HTX WebSocket + индикаторы)
    └── chatbot     (Telegram бот для пользователей)
```

Важный момент: **chatbot и collector не трогаем** — они работают как описано в ТЗ.
Hermes — это отдельный оркестратор на хосте, который управляет всем
проектом как технический агент. Он не заменяет chatbot, он управляет им.

---

## Шаг 0: Подготовка (проверь перед началом)

Убедись что у тебя есть:

```bash
git --version           # нужен для установщика Hermes
docker version          # нужен для управления контейнерами
node --version          # нужен для MCP filesystem сервера (Node 18+)

# Убедись что .env заполнен
cat htx-trading-bot/.env | grep -E "GLM_API_KEY|MINIMAX_API_KEY|DASHSCOPE_API_KEY"
# Должен показать три строки с ключами
```

---

## Шаг 1: Создать структуру hermes/ внутри проекта

```bash
cd htx-trading-bot
mkdir -p hermes
```

Всё что связано с Hermes будет жить в папке `hermes/` — так конфигурация
остаётся рядом с проектом и попадает в git (кроме самих ключей).

---

## Шаг 2: Создать главный setup-скрипт

Создай файл `hermes/setup.sh` и сделай исполняемым:

```bash
cat > hermes/setup.sh << 'EOF'
#!/bin/bash
# =============================================================================
# hermes/setup.sh
# Полная автоматическая установка Hermes для htx-trading-bot.
# Запуск: bash hermes/setup.sh
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
echo "  Hermes Agent Setup — htx-trading-bot"
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
    # Обновляем PATH без перезапуска shell
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
# Hermes хранит секреты в ~/.hermes/.env — туда и пишем.
# Функция upsert: заменяет строку если ключ есть, добавляет если нет.
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "→ [3/7] Синхронизация API-ключей"

[ -f "$PROJECT_ENV" ] || fail ".env не найден: $PROJECT_ENV. Создай из .env.example."

# Загружаем .env в текущий процесс
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
# Один и тот же ключ, разные имена у провайдеров.
upsert "ZAI_API_KEY"        "${GLM_API_KEY:-}"
upsert "MINIMAX_API_KEY"    "${MINIMAX_API_KEY:-}"
upsert "MINIMAX_GROUP_ID"   "${MINIMAX_GROUP_ID:-}"
upsert "DASHSCOPE_API_KEY"  "${DASHSCOPE_API_KEY:-}"
upsert "PERPLEXITY_API_KEY" "${PERPLEXITY_API_KEY:-}"
# Если есть OpenRouter — добавляем (полезно для fallback)
upsert "OPENROUTER_API_KEY" "${OPENROUTER_API_KEY:-}"

# ─────────────────────────────────────────────────────────────────────────────
# ШАГ 4: Генерация ~/.hermes/config.yaml
# Выбираем главную модель по наличию ключей (MiniMax → ZAI → Qwen)
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "→ [4/7] Конфиг ~/.hermes/config.yaml"

if [ -n "${MINIMAX_API_KEY:-}" ]; then
    PROV="minimax"; MODEL="MiniMax-M2.7"; BURL="https://api.minimax.io/v1"
elif [ -n "${GLM_API_KEY:-}" ]; then
    PROV="zai";     MODEL="glm-z1-flash"; BURL="https://api.z.ai/api/coding/paas/v4"
else
    PROV="alibaba"; MODEL="qwen-plus";    BURL=""
fi

BURL_LINE=""
[ -n "$BURL" ] && BURL_LINE="  base_url: $BURL"

cat > "$HERMES_CONFIG" << YAML
# Hermes Agent — конфиг htx-trading-bot
# Сгенерирован: $(date '+%Y-%m-%d %H:%M')
# Проект: $PROJECT_DIR

model:
  default: $MODEL
  provider: $PROV
$BURL_LINE

# Вспомогательные задачи (compression, search) — на быстром GLM Flash
# чтобы не тратить MiniMax токены на сервисные операции.
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

# ─── Быстрые команды — /up /down /logs /ps /tickers /db-stats ───────────────
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
  tickers:
    type: exec
    command: docker exec trading_redis redis-cli keys "ticker:*" 2>/dev/null || echo "Redis не запущен"
  journal:
    type: exec
    command: docker exec trading_redis redis-cli get ai:journal:latest 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "Нет данных"
  candles:
    type: exec
    command: docker exec trading_postgres psql -U bot -d trading -c "SELECT symbol, interval, COUNT(*) as n, MAX(open_time) as latest FROM candles GROUP BY symbol, interval ORDER BY symbol, interval;" 2>/dev/null || echo "Postgres не запущен"
  alerts:
    type: exec
    command: docker exec trading_postgres psql -U bot -d trading -c "SELECT symbol, alert_type, severity, created_at FROM alerts ORDER BY created_at DESC LIMIT 10;" 2>/dev/null
  dbstats:
    type: exec
    command: docker exec trading_postgres psql -U bot -d trading -c "SELECT tablename, n_live_tup as rows FROM pg_stat_user_tables ORDER BY n_live_tup DESC;" 2>/dev/null

# ─── MCP Servers ─────────────────────────────────────────────────────────────
# filesystem — даёт Hermes прямой доступ к файлам проекта (read/write)
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
# Определяет кто такой Hermes в контексте этого проекта.
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "→ [5/7] SOUL.md — личность агента"

cat > "$HERMES_DIR/SOUL.md" << 'SOUL'
# Hermes — Технический оркестратор htx-trading-bot

## Роль
Я главный технический агент проекта htx-trading-bot — торгового Telegram-бота
на базе HTX WebSocket. Управляю Docker-инфраструктурой, диагностирую проблемы,
координирую разработку, мониторю работу системы.

## Стиль
Прямой, технический, без воды. Факты и команды вместо объяснений.
Если нужна диагностика — сначала собираю данные, потом действую.
Перед деструктивными операциями (down -v, rm -rf) предупреждаю явно.

## Язык
Русский для общения. Английский для кода, команд, имён файлов.

## Что я знаю о проекте
- Стек: Python 3.12, asyncio, aiogram 3.14, asyncpg, Redis, PostgreSQL 16
- Биржа: HTX (Huobi) — только спот и USDT-маржинальные пары
- AI-роутинг: GLM-5.1 (анализ), MiniMax M2.7 (чат), QwenCode (код), Perplexity (новости)
- Docker: 4 сервиса — postgres, redis, collector, chatbot
- HTX WS особенность: GZIP-сжатие + кастомный ping/pong протокол

## Быстрые команды
/up, /down, /restart, /ps, /build, /logs, /lc, /lb,
/tickers, /journal, /candles, /alerts, /dbstats

## Правила
- Никогда не записывать API-ключи в код или логи
- При изменении .env напоминать про `docker compose restart`
- `docker compose down -v` = удаление данных, всегда предупреждать
SOUL

ok "SOUL.md создан"

# ─────────────────────────────────────────────────────────────────────────────
# ШАГ 6: AGENTS.md в корне проекта
# Hermes читает этот файл при запуске из директории проекта.
# Даёт полный контекст: структура файлов, Redis-ключи, таблицы БД.
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "→ [6/7] AGENTS.md в корне проекта"

cat > "$PROJECT_DIR/AGENTS.md" << 'AGENTS'
# htx-trading-bot — Контекст проекта

## Архитектура
Два независимых Python-сервиса общаются через Redis и PostgreSQL.

**Collector (Agent #1)** — сбор данных HTX:
- HTX WebSocket wss://api.huobi.pro/ws (GZIP + ping/pong)
- Индикаторы: RSI(14), MACD(12,26,9), BB(20,2), EMA20/50, ATR(14)
- Алерты: спайки цены, RSI extreme, MACD cross, BB break
- Журнал: realtime 30с → Redis, hourly → PostgreSQL

**Chatbot (Agent #2)** — Telegram бот:
- aiogram 3.14 + FSM (состояния в Redis)
- AI-роутинг: GLM-5.1 / MiniMax M2.7 / QwenCode3+ / Perplexity Sonar Pro
- Handlers: market, chat, alerts, portfolio, analytics, settings
- UI: formatter.py (карточки) + keyboards.py (клавиатуры)

## Управление
```
make up              # docker compose up -d
make down            # docker compose down
make build           # пересборка (--no-cache)
make logs            # все логи (tail=100)
make logs-collector  # только collector
make logs-chatbot    # только chatbot
make ps              # статус контейнеров
make db-shell        # psql в postgres
make redis-cli       # redis-cli
make backup          # pg_dump в backups/
```

## Структура файлов
```
htx-trading-bot/
├── docker-compose.yml
├── .env                   # ← секреты (не коммитить)
├── Makefile
├── db/init.sql            # схема 9 таблиц
├── collector/
│   ├── main.py            # asyncio точка входа, все tasks
│   ├── htx/websocket.py   # WS клиент (GZIP + reconnect)
│   ├── htx/rest.py        # REST клиент (история свечей)
│   ├── indicators/calculator.py
│   ├── alerts/detector.py
│   ├── journal/writer.py
│   ├── scheduler/alert_checker.py   # user-алерты каждые 30с
│   ├── scheduler/briefing.py        # брифинг 08:00 МСК
│   └── storage/redis_client.py + postgres_client.py
└── chatbot/
    ├── main.py
    ├── handlers/start.py + market.py + chat.py + alerts.py
    ├── handlers/portfolio.py + analytics.py + settings.py
    ├── ai/router.py        # роутинг по интенту → модель
    ├── ai/prompts.py       # системные промты для каждой модели
    ├── ai/knowledge_base.py
    ├── context/builder.py  # сборка снапшота из Redis для AI
    ├── ui/formatter.py + keyboards.py + onboarding.py
    └── storage/redis_client.py + postgres_client.py
```

## Redis ключи
- `ticker:{symbol}` — TTL 60s, dict: price/high/low/volume/change_24h_pct
- `indicators:{symbol}:{interval}` — TTL 120s, dict: rsi/macd/bb/ema/atr
- `depth:{symbol}` — TTL 30s, dict: best_bid/ask/spread/imbalance
- `ai:journal:latest` — TTL 120s, полный рыночный JSON-снапшот
- `candles:{symbol}:{interval}` — list, последние 500 свечей
- `alerts:recent` — list, последние 50 алертов

## PostgreSQL таблицы
candles, indicators, alerts, ai_journal, chat_history,
user_alerts, positions, user_settings, users

## AI провайдеры (chatbot/ai/router.py)
- market_news  → Perplexity Sonar Pro  (base: api.perplexity.ai)
- deep_analysis → GLM-5.1             (base: open.bigmodel.cn/api/paas/v4)
- quick_chat   → MiniMax M2.7         (base: api.minimax.io/v1)
- backtest_code → QwenCode3-Plus      (base: dashscope.aliyuncs.com/compatible-mode/v1)
- position_calc → GLM-5.1

## Известные проблемы Sprint 1 (не реализованы)
1. `force_intent` отсутствует в сигнатуре route_and_respond()
2. Методы is_new_user/register_user/get_unread_alerts_count нет в ChatPostgresClient
3. briefing.py делает HTTP к chatbot (заменить на прямой AI-вызов)
4. analytics.py использует event._pg — anti-pattern aiogram 3
AGENTS

ok "AGENTS.md создан в $PROJECT_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# ШАГ 7: Утилиты — sync-keys.sh и start.sh
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "→ [7/7] Утилиты"

cat > "$SCRIPT_DIR/sync-keys.sh" << SYNC
#!/bin/bash
# hermes/sync-keys.sh — re-sync ключей из .env → ~/.hermes/.env
# Запускай после изменения .env

SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ENV="\$(dirname "\$SCRIPT_DIR")/.env"
HERMES_ENV="\$HOME/.hermes/.env"

[ -f "\$PROJECT_ENV" ] || { echo "Нет .env"; exit 1; }

set -a
source <(grep -v '^#' "\$PROJECT_ENV" | grep -v '^\$' | grep '=')
set +a

up() {
    local k="\$1" v="\${2:-}"
    [ -z "\$v" ] && return
    local t; t=\$(mktemp)
    grep -v "^\${k}=" "\$HERMES_ENV" > "\$t" 2>/dev/null || true
    echo "\${k}=\${v}" >> "\$t"
    mv "\$t" "\$HERMES_ENV"
    echo "✓ \$k"
}

up "ZAI_API_KEY"        "\${GLM_API_KEY:-}"
up "MINIMAX_API_KEY"    "\${MINIMAX_API_KEY:-}"
up "MINIMAX_GROUP_ID"   "\${MINIMAX_GROUP_ID:-}"
up "DASHSCOPE_API_KEY"  "\${DASHSCOPE_API_KEY:-}"
up "PERPLEXITY_API_KEY" "\${PERPLEXITY_API_KEY:-}"
echo "Sync готово."
SYNC

cat > "$SCRIPT_DIR/start.sh" << STARTSH
#!/bin/bash
# hermes/start.sh — запуск Hermes в контексте проекта
SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="\$(dirname "\$SCRIPT_DIR")"
bash "\$SCRIPT_DIR/sync-keys.sh"
cd "\$PROJECT_DIR"
echo ""
echo "Hermes Agent | Проект: \$PROJECT_DIR"
echo "Команды: /up /down /ps /logs /lc /lb /tickers /journal /candles /alerts"
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
echo ""
EOF
chmod +x hermes/setup.sh
```

---

## Шаг 3: Запустить установку одной командой

```bash
bash hermes/setup.sh
```

Это единственная команда которую нужно запустить. Скрипт сам:
- Устанавливает Hermes если его нет (через официальный инсталлятор Nous Research)
- Читает ключи из твоего `.env` и записывает их в `~/.hermes/.env`
- Генерирует `~/.hermes/config.yaml` с правильным провайдером
- Создаёт `~/.hermes/SOUL.md` с личностью агента
- Создаёт `AGENTS.md` в корне проекта с полным контекстом
- Создаёт `hermes/start.sh` и `hermes/sync-keys.sh` для дальнейшей работы

---

## Шаг 4: Проверить что всё работает

```bash
# Перезагрузи PATH если hermes не видно
source ~/.bashrc   # или source ~/.zshrc

# Диагностика
hermes doctor

# Запустить (два варианта)
bash hermes/start.sh      # через наш враппер (синхронизирует ключи + запускает)
# ИЛИ
cd htx-trading-bot && hermes --tui
```

В интерфейсе Hermes попробуй первые команды:

```
/ps
/up
/tickers
```

---

## Шаг 5 (Опционально): Telegram-шлюз для удалённого управления

Если хочешь управлять проектом из Telegram (не путать с основным ботом пользователей!),
нужно создать **отдельный** бот через @BotFather и подключить его к Hermes.

```bash
# Создать отдельный бот для управления (в @BotFather написать /newbot)
# Получить токен: 9999999:AABBCCDDee...

# Записать токен в Hermes
hermes config set TELEGRAM_BOT_TOKEN "9999999:AABBCCDDee..."

# Настроить gateway
hermes gateway setup
# Выбрать Telegram → указать токен → указать свой Telegram ID как admin

# Запустить gateway (в отдельном tmux/screen)
hermes gateway start
```

После этого пишешь в этот управляющий бот — и он выполняет те же команды
(`/up`, `/logs`, `/tickers` и т.д.) через Hermes.

---

## Шаг 6: Структура файлов после интеграции

```
htx-trading-bot/
├── AGENTS.md          ← новый (контекст проекта для Hermes)
├── .gitignore         ← добавить: hermes/sync-keys.sh не нужен в git
├── hermes/
│   ├── setup.sh       ← главный скрипт (git-tracked)
│   ├── start.sh       ← сгенерируется автоматически
│   └── sync-keys.sh   ← сгенерируется автоматически
└── ... (остальное без изменений)
```

В `.gitignore` добавить:

```
# Hermes Agent (runtime files)
hermes/start.sh
hermes/sync-keys.sh
```

---

## Справочник: маппинг ключей проекта → Hermes

Это важно понимать: у провайдеров разные имена для одних и тех же ключей.
Скрипт делает этот маппинг автоматически, но знать полезно.

| Ключ в `.env` проекта | Ключ в `~/.hermes/.env` | Провайдер в Hermes |
|---|---|---|
| `GLM_API_KEY` | `ZAI_API_KEY` | `zai` |
| `MINIMAX_API_KEY` | `MINIMAX_API_KEY` | `minimax` |
| `DASHSCOPE_API_KEY` | `DASHSCOPE_API_KEY` | `alibaba` |
| `PERPLEXITY_API_KEY` | `PERPLEXITY_API_KEY` | custom endpoint |

Один и тот же физический ключ, просто разные имена переменных.
После `bash hermes/sync-keys.sh` в `~/.hermes/.env` будут все четыре.

---

## Что делать после изменения .env

Если обновил ключи в `.env` проекта:

```bash
bash hermes/sync-keys.sh
# Ключи обновятся в ~/.hermes/.env без перезаписи других настроек
```

---

## Диагностика проблем

**`hermes: command not found` после установки:**

```bash
export PATH="$HOME/.local/bin:$PATH"
source ~/.bashrc
hermes --version
```

**API-ключи не подхватываются:**

```bash
hermes config check
cat ~/.hermes/.env | grep -E "ZAI|MINIMAX|DASHSCOPE"
```

**MCP filesystem не работает (npx не найден):**

```bash
node --version  # должен быть 18+
npm --version
# Если нет Node.js:
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
```

**Сменить главную модель:**

```bash
hermes model
# Интерактивный выбор провайдера и модели
```

**Полный сброс конфига:**

```bash
rm ~/.hermes/config.yaml
bash hermes/setup.sh
```

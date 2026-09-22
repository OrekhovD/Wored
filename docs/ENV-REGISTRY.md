# ENV Registry — WORED Configuration Keys

Each key has: type, default, required/optional, service consumers, and secrecy level.

## Core Infrastructure

| Key | Type | Default | Required | Consumers | Secret |
|-----|------|---------|----------|-----------|--------|
| `DATABASE_URL` | string (DSN) | — | required | runtime (all services) | **yes** |
| `REDIS_URL` | string (URL) | `redis://redis:6379/0` | required | runtime (all services) | **yes** |
| `POSTGRES_USER` | string | `bot` | required | collector, webui, chatbot | **yes** |
| `POSTGRES_PASSWORD` | string | — | required | collector, webui, chatbot | **yes** |
| `POSTGRES_DB` | string | `wored_qa` | required | postgres container | no |

## WebUI Authentication

| Key | Type | Default | Required | Consumers | Secret |
|-----|------|---------|----------|-----------|--------|
| `WEBUI_AUTH_ENABLED` | bool | `true` | required (release) | webui | no |
| `WEBUI_ADMIN_USERNAME` | string | `admin` | optional | webui | no |
| `WEBUI_ADMIN_PASSWORD` | string | auto-generated | required (if auth enabled) | webui | **yes** |
| `WEBUI_SESSION_SECRET` | string | auto (≥32 chars) | required (if auth enabled) | webui | **yes** |
| `WEBUI_INTERNAL_TOKEN` | string | auto (32 chars) | required | webui, both bots | **yes** |
| `WEBUI_TELEGRAM_BOT_TOKENS` | JSON array | — | required (if auth enabled) | webui | **yes** |
| `TELEGRAM_ADMIN_IDS` | string (comma-separated) | empty | optional | webui, bots | no |
| `WEBUI_PUBLIC_BASE_URL` | string (URL origin) | — | required (production) | webui, bots | no |
| `WEBUI_COOKIE_SECURE` | bool | `true` (HTTPS) | required | webui | no |
| `WEBUI_PORT` | int | `8080` | optional | compose | no |

## LLM Provider Gateway

| Key | Type | Default | Required | Consumers | Secret |
|-----|------|---------|----------|-----------|--------|
| `LLM_ROUTING_MODE` | enum | `balanced` | required | runtime gateway | no |
| `LLM_PAID_ENABLED` | bool | `false` | required | runtime gateway | no |
| `LLM_REGISTRY_PATH` | string | `/config/provider_registry.json` | required | runtime gateway | no |
| `OLLAMA_BASE_URL` | string | — | required | provider_adapters | **yes** |
| `OLLAMA_API_KEY` | string | — | required | provider_adapters | **yes** |
| `LOCAL_LLM_ROLES` | string (comma-separated model keys, or `all`) | empty (disabled) | optional | webui prediction_engine | no |
| `LOCAL_LLM_MODEL` | string | `bonsai-27b` | optional | webui prediction_engine | no |
| `LOCAL_LLM_BASE_URL` | string (URL origin) | `http://127.0.0.1:8088` | optional | webui prediction_engine | no |
| `LOCAL_LLM_TIMEOUT` | float (seconds) | `120` | optional | webui prediction_engine | no |
| `NVIDIA_NIM_ENABLED` | bool | `false` | optional | webui prediction_engine | no |

`NVIDIA_NIM_ENABLED` gates the NVIDIA NIM tail of every prediction chain. The
tier is off by default because `integrate.api.nvidia.com` answers `410 Gone` for
every model those chains used (24 runs / 24 failures / 0 successes in
`forecast_model_runs` as of 2026-09-22); a chain therefore ends on a provider
that can answer. Setting it to `true` restores the tail for the `NVIDIA_*_MODEL`
entries whose own `NVIDIA_*_API_KEY` is present — the ~37 such keys in the webui
container do not enable the tier by themselves. See `docs/HERMES-ROLE-FALLBACK-REVIEW-20260921.md` (M4).

`LOCAL_LLM_*` point at a workstation `ollama serve` (port 8088), not at Ollama
Cloud. A local candidate leads the chain of the named role and the cloud chain
stays behind it as fallback. Inside Docker `127.0.0.1` is the container itself:
use `http://host.docker.internal:8088` and start the server on a routable
interface, which publishes an unauthenticated endpoint to the local network.

## Telegram Bots

| Key | Type | Default | Required | Consumers | Secret |
|-----|------|---------|----------|-----------|--------|
| `TELEGRAM_TOKEN` | string | — | required | chatbot (legacy) | **yes** |
| `TELEGRAM_BOT_TOKEN` | string | — | required | chatbot | **yes** |
| `TELEGRAM_WORED_TOKEN` | string | — | required | chatbot_wored | **yes** |

## Execution / Simulation

| Key | Type | Default | Required | Consumers | Secret |
|-----|------|---------|----------|-----------|--------|
| `SIM_ALLOWED_SYMBOLS` | string | `btcusdt` | required | execution_engine | no |
| `PAPER_MARKET_MODE` | enum (`demo`, `live`) | `live` | required for paper trading | webui | no |
| `PAPER_MARKET_MAX_AGE_SECONDS` | number, `0 < value <= 60` | `5` | required in `live` mode | webui | no |
| `HTX_LINEAR_SWAP_BASE_URL` | URL | `https://api.hbdm.com` | required for live paper trading | collector | no |
| `HTX_PERPETUAL_POLL_SECONDS` | number, `0.5..30` | `2` | required for live paper trading | collector | no |
| `PAPER_CONTRACTS` | comma-separated contract codes | `BTC-USDT` | required for live paper trading | collector | no |

`PAPER_MARKET_MODE=live` requires Redis keys in the form
`market:perpetual:htx:BTC-USDT`. A missing, malformed or stale snapshot blocks
preview, fill and close. The execution path never falls back to the demo price.
The collector publishes the keys from public HTX USDT-M endpoints without
using account credentials or order endpoints.

## Secrets Management

- Secrets are generated programmatically via `secrets.token_urlsafe()` and written directly to `.env` files
- Only the key name and present/updated status are shown to the administrator, never the value
- `test-password` / `test-token` / `disposable-qa-only` values are **never** used in production
- Environment changes require `docker compose up -d --force-recreate` (restart does not re-read env)

## Model Registry

The model registry is mounted from `./config:/config:ro` in all four runtime services.
For host tests, pass the full path: `LLM_REGISTRY_PATH=D:/WORED_STAGING_20260908/config/provider_registry.json`

Registry entries include: `registry_version`, `verified_at`, `provider`, `model_id`, `endpoint_type`, `enabled`, `cost_class`, `capabilities`, `context_tokens`, `max_output_tokens`, `pricing`, `validation_gate_id`.

## Session Forecast Auto-Refresh (FORECAST_AUTO_REFRESH_* — Trader Deck)

| Key | Type | Default | Required | Consumers | Secret |
|-----|------|---------|----------|-----------|--------|
| `FORECAST_AUTO_REFRESH` | bool | `true` | optional | webui | no |
| `FORECAST_AUTO_REFRESH_SYMBOL` | string | `btcusdt` | optional | webui | no |
| `FORECAST_AUTO_REFRESH_HOURS` | int | `4` | optional | webui | no |
| `FORECAST_AUTO_REFRESH_TIMEFRAME` | string | `60min` | optional | webui | no |
| `FORECAST_AUTO_REFRESH_DEPTH` | int | `3` | optional | webui | no |
| `FORECAST_AUTO_REFRESH_INTERVAL` | int (seconds) | `60` | optional | webui | no |
| `FORECAST_AUTO_REFRESH_MAX_PER_DAY` | int | `6` | optional | webui | no |
| `FORECAST_AUTO_REFRESH_COOLDOWN` | int (seconds) | `900` | optional | webui | no |

The loop in `webui/forecast_refresh.py` enqueues a forecast only when the newest
completed one no longer covers a future step, and it is bounded by the per-day
budget plus cooldown. Requests it creates carry `source='auto-trader-session'`,
which is what the budget guard counts — operator-triggered forecasts never
consume that budget. Set `FORECAST_AUTO_REFRESH=false` to stop AI spend entirely
(the Trader Deck then shows `прогноз просрочен` instead of inventing data).

## Paper Trading Engine (PAPER_* keys — new in HERMES-ACTIVE-PAPER-TRADING-V1)

These are technical limits, not user-facing risk settings. User risk is stored in DB settings.
More restrictive user limits always take precedence.

| Key | Type | Default | Required | Consumers | Secret |
|-----|------|---------|----------|-----------|--------|
| `PAPER_ENGINE_ENABLED` | bool | `false` | required (cutover) | collector | no |
| `PAPER_ENGINE_INTERVAL_SECONDS` | int | `2` | optional | collector runner | no |
| `PAPER_HEARTBEAT_INTERVAL_SECONDS` | int | `5` | optional | collector runner | no |
| `PAPER_HEARTBEAT_STALE_SECONDS` | int | `30` | optional | webui, chatbot | no |
| `PAPER_MARKET_MAX_AGE_SECONDS` | int | `5` | optional | collector, webui | no |
| `PAPER_ORDER_MAX_SPREAD_BPS` | int | `10` | optional | collector, webui | no |
| `PAPER_SIM_SLIPPAGE_BPS` | int | `2` | optional | collector | no |
| `PAPER_SIM_LATENCY_MS` | int | `250` | optional | collector | no |
| `PAPER_SIM_FEE_RATE` | decimal | `0.0006` | optional | collector | no |
| `PAPER_MAX_VISIBLE_LIQUIDITY_FRACTION` | decimal | `0.1` | optional | collector | no |
| `PAPER_AI_MIN_INTERVAL_SECONDS` | int | `300` | optional | collector | no |
| `PAPER_AI_PLAN_TTL_SECONDS` | int | `3600` | optional | collector | no |
| `PAPER_AI_MAX_REQUESTS_PER_DAY` | int | `48` | optional | collector | no |
| `PAPER_AI_MAX_TOKENS_PER_DAY` | int | `200000` | optional | collector | no |

Invalid configuration causes explicit readiness failure for the trading subsystem.
`PAPER_ENGINE_ENABLED=false` keeps the new runner off until cutover; existing
legacy sessions continue to work unchanged.

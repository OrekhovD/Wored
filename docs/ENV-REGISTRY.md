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

## Secrets Management

- Secrets are generated programmatically via `secrets.token_urlsafe()` and written directly to `.env` files
- Only the key name and present/updated status are shown to the administrator, never the value
- `test-password` / `test-token` / `disposable-qa-only` values are **never** used in production
- Environment changes require `docker compose up -d --force-recreate` (restart does not re-read env)

## Model Registry

The model registry is mounted from `./config:/config:ro` in all four runtime services.
For host tests, pass the full path: `LLM_REGISTRY_PATH=D:/WORED_STAGING_20260908/config/provider_registry.json`

Registry entries include: `registry_version`, `verified_at`, `provider`, `model_id`, `endpoint_type`, `enabled`, `cost_class`, `capabilities`, `context_tokens`, `max_output_tokens`, `pricing`, `validation_gate_id`.
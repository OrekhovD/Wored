# Operations Runbook — WORED Stabilization 2026-09-08

## Service Topology

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│   webui     │  │  collector   │  │  chatbot     │
│  :8080      │  │  (scheduled) │  │  (telegram)  │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       │                │                 │
       └────────────────┼─────────────────┘
                        │
              ┌─────────┴─────────┐
              │   postgres:5432   │
              └─────────┬─────────┘
                        │
              ┌─────────┴─────────┐
              │    redis:6379     │
              └───────────────────┘
```

Core Compose contains **six services**: webui, collector, chatbot, chatbot_wored, postgres, redis.
Tunnel (e.g., cloudflared) runs separately.

## Health Check

```bash
# Quick health
curl -s http://127.0.0.1:8080/api/health | python -m json.tool

# Expected: {"status": "healthy", "components": {"postgres": true, "redis": true, "collector_feed": true}}
```

### Healthz / Readyz

- `GET /healthz` — returns 200 if HTTP process is alive (no DB check)
- `GET /readyz` — returns 200 only if postgres, redis, forecast worker, and collector heartbeat are all fresh; 503 otherwise with component details

## Common Operations

### Restart a Service

```bash
# On Windows, use cmd.exe for Docker commands
cmd.exe /c "docker compose -f D:\WORED\docker-compose.yml restart webui"
```

### Restart After Config Change

Environment variables are read at container start. After changing `.env`:

```bash
cmd.exe /c "docker compose -f D:\WORED\docker-compose.yml up -d --force-recreate"
```

**Never** use just `restart` — it does NOT re-read `.env`.

### Database Recovery

Postgres takes ~30 seconds after container start to leave recovery mode.
If health check shows `postgres: false`:

```bash
# Wait 30s, then restart webui (its pg_pool is created at lifespan startup)
cmd.exe /c "docker compose restart webui"
```

### Migrations

```bash
# Dry-run (check without applying)
python scripts/migrate_stabilization.py --check

# Apply
python scripts/migrate_stabilization.py

# Custom DSN
python scripts/migrate_stabilization.py --dsn "postgresql://user:pass@host:5432/db"
```

Migrations use advisory locks and checksum verification. Re-running is idempotent.
Changing an already-applied migration's SQL causes an abort.

### Queue Monitoring

```bash
# Check forecast_jobs queue
cmd.exe /c "docker exec -it htx_trading_bot_postgres psql -U bot -d wored_qa -c \"SELECT state, count(*) FROM forecast_jobs GROUP BY state\""
```

### LLM Budget Monitoring

```bash
# Check daily budget usage
cmd.exe /c "docker exec -it htx_trading_bot_postgres psql -U bot -d wored_qa -c \"SELECT scope_key, period_kind, used_requests, used_tokens, used_cost FROM llm_budget_buckets WHERE period_kind = 'day'\""
```

## Troubleshooting

### SSL Error on Windows (`_ssl.c:3108`)

MSYS/Git Bash sets `SSL_CERT_FILE` to empty string, breaking Python's ssl module.
Fix: conftest.py sets `SSL_CERT_FILE=certifi.where()` at module level before imports.

### WebUI Infinite Redirect (auth_enabled=false)

When `WEBUI_AUTH_ENABLED=false`, loopback requests bypass principal check.
If `resolve_principal()` redirects even for loopback with auth disabled, ensure the middleware returns `await call_next(request)` for this case.

### Forecast Queue Stuck

1. Check job states: `SELECT state, count(*) FROM forecast_jobs GROUP BY state`
2. Check deadlines: `SELECT request_id, state, deadline_at, finished_at FROM forecast_jobs WHERE state = 'running'`
3. Stale running jobs (>20 min): reaper marks them as `failed` with `legacy_missing_payload`

### Model Returns Empty Content

Ollama Cloud reasoning models (glm-5.1, glm-5.2, kimi-k2.6, deepseek-v4-flash) return empty `content` field via `/v1/chat/completions`. Use native `/api/chat` endpoint instead.

### Read-Only API Key

Keys with read-only scope return 200 on `/v1/models` but 401 on `/v1/chat/completions`. Ensure inference-scope keys are used.

## Backup

```bash
# Database dump
cmd.exe /c "docker exec htx_trading_bot_postgres pg_dump -U bot wored_qa > backup_$(date +%Y%m%d).sql"

# Restore
cmd.exe /c "docker exec -i htx_trading_bot_postgres psql -U bot wored_qa < backup_20260908.sql"
```
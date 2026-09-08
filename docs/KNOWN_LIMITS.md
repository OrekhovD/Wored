# KNOWN LIMITS — WORED Stabilization 2026-09-08

## Architecture Limits

1. **Single-node PostgreSQL** — no replication, no failover. Container restart causes ~30s recovery mode.
2. **Redis in-memory** — no persistence config; restart loses non-committed queue state.
3. **WebUI pool lifecycle** — pg_pool created at FastAPI lifespan startup; must restart webui after postgres recovery.
4. **No horizontal scaling** — each service runs as single container; no load balancing.

## Data Limits

5. **Forecast queue TTL** — 20 minutes from creation; expired jobs are not re-processed automatically.
6. **Heartbeat timeout** — evaluate_forecasts 5min/12min, execution_watch 10sec/30sec, market_context 30sec/90sec.
7. **Simulation versioning** — existing positions keep their calculation version (1 or 2); new positions use v3 (Decimal, ROUND_HALF_UP). No automatic migration of v1/v2 positions.

## LLM / Provider Limits

8. **Reasoning models return empty content field** via OpenAI SDK `/v1/chat/completions` — must use native `/api/chat` endpoint for Ollama Cloud.
9. **Read-only API keys** — return 200 on `/v1/models` but 401 on `/v1/chat/completions`. Ensure inference-scope keys are used.
10. **Budget defaults** — daily 200 attempts / 20M tokens; weekly 1K / 100M; monthly 2K / 200M. These are local safety limits, not Ollama Pro account limits.
11. **Validation gate** — minimum 100 completed forecast cases for gate approval. Below 30 data points → `insufficient_data`, no rating built.

## Security Limits

12. **Loopback bypass** — when `WEBUI_AUTH_ENABLED=false`, loopback (127.x.x.x) requests bypass principal check. This is intentional for local development only.
13. **Cookie: 12 hours, HttpOnly, SameSite=Lax, Secure=true when HTTPS** — no refresh token mechanism.
14. **Telegram initData HMAC** — 300-second window; future timestamps rejected.
15. **CSRF on POST** — requires exact Origin from current origin or `WEBUI_PUBLIC_BASE_URL`.

## Compatibility Limits

16. **Metrics versioning** — v1 and v2 metrics are NOT mixed in aggregates. Historical v1 data is preserved as-is.
17. **Legacy pending cleanup** — jobs older than 20 minutes with missing payload are marked `failed` with reason `legacy_missing_payload`. No automatic re-processing.
18. **Migration checksum verification** — modifying an already-applied migration's SQL causes an abort. Changes must be a new migration version.
19. **SSL on Windows** — `SSL_CERT_FILE` set to empty string by MSYS causes crashes. Workaround: conftest.py sets `SSL_CERT_FILE=certifi.where()` at module level.

## UI Limits

20. **No frontend framework** — plain HTML/CSS/JS. Routes `/`, `/alerts`, `/predictions`, `/journal` must be preserved.
21. **Chart containers** — price, volume, RSI, MACD must not be deleted.
22. **Simulation is educational** — funding rate is a fixed assumption (0.01% per 8h), not real exchange data. UI labels must not claim it is.
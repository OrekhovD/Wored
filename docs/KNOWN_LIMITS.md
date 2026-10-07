# KNOWN LIMITS — WORED Stabilization 2026-09-08

## Architecture Limits

1. **Single-node PostgreSQL** — no replication, no failover. Container restart causes ~30s recovery mode.
2. **Redis in-memory** — no persistence config; restart loses non-committed queue state.
3. **WebUI pool lifecycle** — pg_pool created at FastAPI lifespan startup; must restart webui after postgres recovery.
4. **No horizontal scaling** — each service runs as single container; no load balancing.

## Data Limits

5. **Forecast queue TTL — code/DB drift (RESOLVED 2026-09-26).** `webui/forecast_queue.py`
   declares `deadline_at TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '30 minutes'`, but the
   statement is `CREATE TABLE IF NOT EXISTS`, so it never alters an already-created table;
   `enqueue` does not pass `deadline_at` explicitly, so any table originally created by
   `scripts/migrate_stabilization.py` kept an effective **20-minute** TTL. Fixed with an
   additive migration `migrations/20260926_forecast_jobs_deadline_30min.sql`
   (`ALTER TABLE forecast_jobs ALTER COLUMN deadline_at SET DEFAULT NOW() + INTERVAL
   '30 minutes'`), applied to the **live production DB** (column default verified as
   `now() + 30 minutes`), and `scripts/migrate_stabilization.py` was synced to `30 minutes`
   so fresh environments are born correct. Any other pre-existing working database needs the
   same migration run once.
6. **Heartbeat timeout** — evaluate_forecasts 5min/12min, execution_watch 10sec/30sec, market_context 30sec/90sec.
7. **Simulation versioning** — existing positions keep their calculation version (1 or 2); new positions use v3 (Decimal, ROUND_HALF_UP). No automatic migration of v1/v2 positions.
7b. **HTX perpetual kline backfill ceiling (~33h)** — `GET /linear-swap-ex/market/history/kline`
   ignores **every** range parameter (`start_ms`, `end_ms`, `since`, `start`, `end`, `from`,
   `start_date`/`end_date`, `start_time`/`end_time`, verified live with the loader's own call
   shape `contract_code`/`period`/`size`) and returns only the latest ≤2000 candles
   (~33 hours at 1min). The spot endpoint `api.huobi.pro/market/history/kline` behaves the same
   way: a request for the 2026-09-30 window returned the current day. So
   `scripts/backfill_perp_candles.py` cannot reach a hole older than
   that ceiling, and a historical gap in `trader_v1_perp_candles` beyond ~33h is **unrecoverable
   from the public API** (e.g. the 2026-09-30 15:00→23:00 UTC 1m gap is permanent). Only
   `collector/htx/gap_monitor.py` self-heals, and only within its rolling
   `GAP_WINDOW_MINUTES=360` window; older gaps are a known limit, not a bug. The non-history
   `/market/kline` variant returns `404`.
7c. **`candle_gap_count` window alignment (RESOLVED 2026-10-05)** — `check_candle_gaps` built
   its expected-bucket grid from `now - 360min` with seconds/microseconds intact, so
   `generate_series` buckets landed on non-`:00` seconds and never matched minute-aligned
   candle `open_time`; the `LEFT JOIN` then counted the whole window missing, pinning the
   gauge at ~360 (false positive). Fixed by flooring `now` to the minute in
   `collector/htx/gap_monitor.py` (verified: `check_candle_gaps()` returns 1, not 360).
7d. **Forecast evaluation venue (RESOLVED 2026-10-07)** — `collector/predictions/evaluator.py`
   resolved the realised price from HTX **spot** `/market/history/kline`, while the V3 forecast
   base price (`webui/forecast_command_v3.py`) is taken from the **perpetual** store. That mixed
   venues in one metric stream; measured basis on rows `1210`/`1214` was **0.035 %**
   (spot 83736.9 vs perp 83707.7). The evaluator now resolves the realised fact from
   `trader_v1_perp_candles` first — the declared price source-of-truth, same venue as the base
   price, and not capped by the 33h reach-back — and keeps the HTX spot call only as a fallback
   for contracts the collector does not persist. Bucket matching is exact (`load_closes_at`), so
   a missing candle is never substituted by a neighbour. `metrics_version` stays `2` on purpose:
   the formulas are unchanged, and `webui/app.py` / `webui/prediction_engine.py` filter on
   `metrics_version = 2`, so a new version number would silently drop these points from the
   learning loop and the model ranking.
7e. **Pre-2026-09-19 forecast points are permanently un-evaluable** — 476 due points
   (targets 2026-06-29 … 2026-09-18) precede both the start of local collection
   (`trader_v1_perp_candles` oldest 1m row = 2026-09-19 07:35 UTC) and the HTX reach-back
   described in 7b, so no verified actual price exists for them. Accuracy metrics and role
   ranking for that period are simply computed over fewer points; the rows keep
   `evaluated_at IS NULL` and are never filled with invented numbers. `ethusdt` has no local
   series at all (the collector persists only `PAPER_CONTRACTS=BTC-USDT`), so it stays on the
   HTX fallback and cannot be evaluated historically.
7f. **`candle_gap_count` is a pre-heal counter, not an unresolved hole** — the gauge is
   written from the count measured *before* `check_candle_gaps` runs its repair pass, so a
   non-zero value means "buckets that were missing at the start of this cycle and were then
   re-persisted", not "the series still has holes". Verified 2026-10-07: `/healthz` reported
   `candle_gap_count: 6`, collector log the same cycle shows `candle gaps BTC-USDT: 6 detected,
   6 rows re-persisted`, and a direct contiguity query over the same rolling 360-minute window
   returned **1** missing bucket — the leading edge (`now - 1min`, whose candle is not closed
   yet at check time). The aggregation is single-contract (`PAPER_CONTRACTS=BTC-USDT`, `1min`
   only), not a mix of instruments/timeframes. Consequence: treat the gauge as a self-heal rate,
   and use the DB contiguity query for a data-integrity verdict. Unresolved observation: one
   cycle at 2026-10-08 00:09 +07 logged `candle gap check failed for BTC-USDT:` with an empty
   exception message; the next cycle recovered, and the exception text was not captured.

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
15b. **Local HTTP entry boundary** — with `WEBUI_COOKIE_SECURE=true` (production
    default for HTTPS), the session cookie is `Secure`, so a plain-HTTP route over
    `127.0.0.1` cannot hold a session and POSTs fail the CSRF/Origin check (observed
    `403`). A local HTTP 200 on `GET /` does **not** prove authenticated flows work;
    real login/POST acceptance requires the chosen HTTPS route (or an explicitly
    `WEBUI_COOKIE_SECURE=false` local dev posture). Do not disable external protection
    just to make a test green.

## Compatibility Limits

16. **Metrics versioning** — v1 and v2 metrics are NOT mixed in aggregates. Historical v1 data is preserved as-is.
17. **Legacy pending cleanup** — jobs older than 20 minutes with missing payload are marked `failed` with reason `legacy_missing_payload`. No automatic re-processing.
18. **Migration checksum verification** — modifying an already-applied migration's SQL causes an abort. Changes must be a new migration version.
19. **SSL on Windows** — `SSL_CERT_FILE` set to empty string by MSYS causes crashes. Workaround: conftest.py sets `SSL_CERT_FILE=certifi.where()` at module level.

## UI Limits

20. **No frontend framework** — plain HTML/CSS/JS. Routes `/`, `/alerts`, `/predictions`, `/journal` must be preserved.
21. **Chart containers** — price, volume, RSI, MACD must not be deleted.
22. **Simulation is educational** — funding rate is a fixed assumption (0.01% per 8h), not real exchange data. UI labels must not claim it is.
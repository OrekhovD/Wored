# Weekly Changelog

## 2026-09-08 — Stabilization S1

**Verification date**: 2026-09-08
**Source**: HERMES-WORED delivery package, stabilization patch
**Models/limits affected**: All Ollama Cloud models, budget defaults

### Changed Models
- No model retirements or additions
- `minimax-m3` confirmed as the only model returning structured JSON in `content` on both endpoints
- Reasoning models (glm-5.x, kimi-k2.x, deepseek-v4) must use native `/api/chat` endpoint

### Limits & Defaults
- Daily budget: 200 attempts / 20M tokens
- Weekly: 1K / 100M
- Monthly: 2K / 200M
- Forecast job TTL: 20 minutes
- Heartbeat intervals: market_context 30s/90s, evaluate 5min/12min, execution 10s/30s

### Key Changes
- R01: Forecast job queue with TTL, heartbeat, state normalization
- R02: Consistent snapshot hashing, data freshness enforcement
- R03: Principal-based auth, dual bot verification, CSRF
- R05: Simulation v3 with Decimal, idempotency, NaN rejection
- R06: Forecast revisions, metrics v2 formulas
- R07: Health/readiness endpoints with component checks
- R08: Reproducibility — QA lock files, CI config, Python pin
- R12: Versioned schema migrations with advisory lock and checksums

### Known Limits
- Reasoning models return empty content via OpenAI SDK — use native endpoint
- Read-only API keys authenticate but fail on inference calls
- Metrics v1 and v2 are not mixed in aggregates
- Windows SSL_CERT_FILE workaround required (certifi CA bundle)
- Postgres takes ~30s after start to leave recovery mode
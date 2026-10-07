# Weekly Changelog

## 2026-10-07 — Self-learning history recovered + isolated QA gate closed

**Verification date**: 2026-10-07
**Runtime baseline**: `e379f55` (the `$5::numeric` evaluation fix, pushed earlier today)

### Changed behaviour (collector)
- **Forecast evaluation is venue-correct and no longer capped by the HTX reach-back**:
  `collector/predictions/evaluator.py` resolves the realised price from
  `trader_v1_perp_candles` (the declared source-of-truth, same venue as the V3 base price)
  through the new exact-bucket helper `load_closes_at()` plus `list_contracts()` in
  `collector/storage/perp_candles.py`; the HTX **spot** call stays only as a fallback for
  contracts the collector does not persist (`ethusdt`). Measured perp/spot basis removed:
  **0.035 %** on rows `1210`/`1214`.
- Recovered backlog: evaluated points went **379 → 747** of 1231; only **2** due points
  remain pending inside local coverage. `metrics_version` deliberately stays `2`
  (WebUI/prediction_engine filter on `= 2`). See `docs/KNOWN_LIMITS.md` 7d.

### Known limits recorded
- **7e** — 476 due points with targets before 2026-09-19 are permanently un-evaluable;
  `evaluated_at` stays `NULL`, no invented numbers.
- **7f** — `candle_gap_count` in `/healthz` is a *pre-heal* self-repair counter, not an
  unresolved hole (gauge 6 in the same cycle as `6 rows re-persisted`, DB contiguity query
  over the rolling 360-minute window returned 1 leading-edge bucket).

### Tests / QA
- Golden assertions recalculated for the 0.0005 taker fee (6b5e9cc) in
  `tests/stabilization/test_contracts.py`, `test_execution_contracts.py`,
  `test_simulation_v3.py`; the v1 path stays frozen at its original numbers.
- Stale model contracts updated to the committed behaviour: `think` defaults to `true`
  with `LOCAL_LLM_THINK=false` still honoured (81753b9), `done_reason=length` with content
  is accepted (8a2d403), and the cloud-fallback assertion reads the model id from
  `_build_runtime_candidates` instead of pinning the renamed `glm-5.1` (50ca344, 22425c3).
- `tests/stabilization/test_reproducibility.py` no longer hardcodes a Windows absolute
  payload path; it resolves from the repo root (`WORED_HERMES_PAYLOAD_DIR` overrides).
- New `.github/workflows/stabilization.yml` runs the same isolated gate as the workstation.
- `.dockerignore`: `**/.venv/`, TASOCHKI re-include narrowed to the two files the suite
  actually reads, `.env.example`/`.env.wored.example` re-included. Build context went from
  ~300 MB to ~1 MB.
- **Gate result**: `docker compose -p wored-qa -f docker-compose.qa.yml run --rm --build checks`
  → stabilization suite `Ran 227 tests ... OK` with **0 failures and 0 skips**, plus
  `webui/tests`, `collector/tests/test_predictions.py`, the two chatbot suites, `ruff`
  (E9,F821,F822,F823) and `mypy` all clean.

### Docs
- `AGENTS.md`: the legacy/caution zone files are verified **dead code** (zero importers
  repo-wide, including dynamic `import_module` paths); no files were deleted.

## 2026-09-26 — Trading-day P0 (settlement_pending stall) + doc truth-sync

**Verification date**: 2026-09-26
**Source**: external ChatGPT audit verified against code; Phase 1 fix of the
`Trading Day P0 Fix And Acceptance` plan
**Git baseline**: `dd1be35`

### Changed behaviour (paper_trading)
- **P0 fixed**: an expired `running` day that cannot be closed for lack of a valid
  quote enters `settlement_pending`; the auto-finish loop now selects
  `state IN ('running','settlement_pending','closing')`, so a deferred close is
  retried every cycle instead of stalling the trading-day shift forever
  (`uq_days_one_incomplete` no longer wedges the next day).
- Day closure is now driven exclusively by the canonical **persisted idempotent
  `finish-{day_id}` command** (`PaperTradingService.finish_day`); the runner no
  longer bypasses it with a synthetic command.
- **No false `closed`**: `closed` is recorded only when
  `update_day_state(closed)` returns `True`; a `False`/exception keeps the day in
  `settlement_pending` for retry. No fabricated close/fill and no account reset.
- **Atomic command claim** (`claim_command` via `SELECT … FOR UPDATE SKIP LOCKED`
  + status-guarded `UPDATE`): exactly one runner executes a command; a deferred
  close requeues its claim; crash-leftover `processing` rows are released by
  `requeue_stale_processing`.
- Next day inherits policy (timezone/end_time_local/mode/strategy_version) from the
  closed day's `settings_snapshot`; an unrecoverable policy raises
  `recovery_required` instead of silently defaulting to `Asia/Bangkok/21:00`.
- Recovery now restores owners whose day is `settlement_pending`/`closing` and keeps
  new entries blocked until the pending closure completes.

### Docs
- `README.md`, `docs/04-configuration.md`, `docs/ENV-REGISTRY.md`,
  `docs/PROVIDER-REGISTRY.md`: TokenRouter / NVIDIA NIM / OpenRouter / Qwen gateway
  re-labelled as **archived / no-op**; active stack documented as Ollama Cloud Pro
  (`:cloud`) → local Bonsai failover, with the chatbot↔WebUI env-name/chain-order split.
- New `docs/AI-ROUTING-DECISION-20260926.md` (ADR).
- `docs/KNOWN_LIMITS.md`: forecast `deadline_at` code/DB drift (code 30 min vs live
  DB default 20 min) and the local HTTP/HTTPS entry boundary recorded.

### Status
- Phase 1 code + tests (10 rollover cases pass locally; 4 PG concurrency tests
  ready). Phase 2 isolated-QA financial acceptance **BLOCKED**: Docker Desktop VM
  storage I/O failure on the workstation (not a code failure); must not be faked as
  passed.

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
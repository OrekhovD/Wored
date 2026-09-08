# Stabilization Implementation — 2026-09-08

## Summary

This document records the implementation of the WORED stabilization assignment (HERMES-WORED delivery package). All changes are applied to the `hermes/stabilization-20260908` branch in `D:\WORED_STAGING_20260908`.

## Phases Completed

### P0: Package Verification
- Verified payload integrity (stabilization.patch, reference files, tools)
- `verify_payload.py` passed

### P1: Staging Setup
- Cloned WORED to `D:\WORED_STAGING_20260908`
- Created branch `hermes/stabilization-20260908`
- Applied `stabilization.patch` — 40 files (24 modified + 16 new)

### P2: Baseline QA
- 207 tests passed, 8 skipped (PostgreSQL integration), 0 failed
- Fixed SSL/aiohttp workaround in conftest.py
- Fixed test_router.py fallback chain assertion
- Fixed QueueTests mock Connection for new forecast_queue.py

## Requirements Implemented

### R01: Forecast Execution and States
- **Files**: `webui/forecast_input.py`, `webui/forecast_queue.py`, `webui/app.py`, `chatbot/integrations/webui_client.py`, `chatbot/handlers/predictions.py`
- **Tests**: 37 (test_forecast_lifecycle.py)
- **Commit**: 48693a0
- Input validation, job queue with TTL/heartbeat, state normalization (queued→running→completed/failed/expired)

### R02: Data and Freshness
- **Tests**: 11 (test_snapshot_consumers.py)
- Consistent hash, null RSI, zero MACD, stale blocks entry, gap/future/NaN rejection

### R03: Access, Dual Bot Auth, CSRF
- **Files**: `webui/principal.py`, `webui/access_control.py`, `webui/app.py`
- **Tests**: 37 (test_principal.py)
- Principal dataclass, dual bot verification, CSRF enforcement, cookie/telegram/internal auth, revoked admin blocking

### R05: Simulation and Plan Atomicity (partial)
- **Tests**: 29 offline (test_simulation_v3.py)
- Decimal v3, NaN/negative rejection, idempotency, legacy compat
- DB-backed order/revision tests deferred (require live PostgreSQL with schema)

### R06: History, Revisions, and Metrics
- **Files**: `chatbot/services/forecast_revisions.py`
- **Tests**: (test_postgres_forecast_history.py — DB integration)
- Revision chain management, metrics v2 formulas, legacy pending cleanup

### R07: Health, Readiness, Errors
- **Tests**: 3 (test_readiness_contract.py)
- healthz=200 always, readyz=503 on deps down, /api/health no provider call

### R08: Reproducibility and QA
- **Tests**: 12 (test_reproducibility.py)
- QA requirements, CI workflow, Python 3.11 pin, test discovery, Dockerfile verification

### R12: Migrations and Backward Compatibility
- **Files**: `scripts/migrate_stabilization.py`
- **Tests**: 15 offline (test_migrations.py)
- Versioned schema migrations with advisory lock, checksum verification, idempotent DDL
- Three migration steps: 01 (forecast_jobs + extensions), 02 (LLM accounting), 03 (forecast revisions)

## Key Fixes Applied

1. **SSL workaround** — conftest.py sets `SSL_CERT_FILE=certifi.where()` at module level for Windows uv Python 3.11
2. **Auth-disabled middleware** — loopback bypass returns `await call_next(request)` when `auth_enabled=False`
3. **Queue state** — `pending` → `queued` per R01 normalization (forecast_jobs CHECK constraint)
4. **QueueTests mock** — Connection.execute handles SQL string args, not just positional tuples
5. **test_router.py** — fallback chain assertion updated for new model config

## Test Count

- **182 stabilization tests** passed, 1 skipped, 0 failed
- Total with webui/collector/chatbot suites: 207+

## Commits

1. `5bed365` — base (upstream main)
2. `9eb52a0` — docs: changeset review for independent specialist
3. `6fd4555` — feat(ops): fix_tunnel.sh
4. `5bed365` — base merge point
5. `48693a0` — feat(R01): forecast lifecycle
6. `ee13f4c` — feat(R02,R03,R05,R07): snapshots, principal, simulation, readiness
7. `47deab3` — feat(R12): migration script and offline tests
8. `0efa50f` — feat(R06,R08,R12): forecast revisions, reproducibility, migrations

## Remaining Work

- **R04**: Provider gateway (delegated, in progress)
- **R05**: DB-backed order/revision tests (requires live schema)
- **R09**: Documentation and configuration (in progress)
- **R10**: UI/UX after S1 acceptance (not started)
- **R11**: UI/UX acceptance criteria (not started)
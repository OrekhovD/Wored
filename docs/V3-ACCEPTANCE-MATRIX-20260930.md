# V3 Market–Forecast–Simulation — MC-01…MC-22 Acceptance Matrix (P6.5)

Date (UTC): 2026-09-30 · Repo SHA: `7e5b66b` (nothing committed; V3 work is the working tree)
Spec: `docs/QODER-MARKET-FORECAST-SIMULATION-V3-TZ-20260928.md` §10.
Updated same day with the P1.3 supplement (forming-candle & gap indicators) — see the P1.3 runs table and the revised MC-02 row.
Updated again with the **G3/G4 supplement** (SSE resync/fallback §53, URL-state §39) — see that runs table and the "Residual ТЗ items" section.
L4 **re-executed after G3/G4** (`webui` rebuilt/restarted); the earlier stale-asset caveat is closed — see "L4 re-verify after G3/G4".
Updated again with the **G2 staged supplement** (`webui/forecast_command_v3.py` + 35 tests) — **written and tested, but NOT mounted in `webui/app.py`**, so §55 is not yet closed at runtime.
**G2 step 3** then added the additive row-level identity DDL (§117) — see "G2 step-3 supplement"; routes are **still unmounted** and the live database has **not** been migrated in this session.
**G2 step 4** mounted the router in `webui/app.py` (see "G2 step-4 supplement"); the Captain then chose **close-without-L4** — the serving-level restart is *waived and recorded*, not silently skipped. Pre-restart probing found a **live-runtime incident** (collector wedged) — see "Live-runtime incident". **Update 2026-10-02:** the host wedge cleared on reboot; the collector feed is restored; a step-3 comment bug that broke the webui DB bootstrap was fixed; and **G2 step 5 was executed** (`docker compose restart webui`) — see "Recovery + step-5 execution". **Update 2026-10-03:** the §51 worker perpetual-context was proven by **read-only execution** against live `trader_v1_perp_candles` (surfacing and fixing a real `normalize_period` 400-on-`15m` bug), and the **disposable-PG DDL rehearsal (risk 8b) executed — 4 passed** on isolated `wored_qa`; the only G2 item left is the Captain-declined authenticated live `POST`.

Evidence levels used below:

* **L1 deterministic/unit** — pure host tests, fixed inputs.
* **L2 fixture integration** — in-process or disposable-PG tests against real
  route/engine/DDL code with synthetic stores.
* **L3 real browser** — Playwright/Chromium against the fixture server on
  `127.0.0.1:18080` (`tests/ui/fixture_app_v3.py`, real templates, real JS, real
  `/api/v3` routers).
* **L4 live environment** — real Docker Compose runtime, real HTX feed, real
  Telegram. **Re-executed 2026-09-30 (P6.6)**; see the L4 runs table below.

## Consolidated runs behind this matrix (executed 2026-09-30, this session)

| Run | Command (from `D:\WORED`) | Result |
|---|---|---|
| Host suites | `python -m pytest tests\test_instrument_registry.py tests\test_market_workspace.py tests\test_market_stream_unit.py tests\test_forecast_workspace.py tests\test_positions_workspace.py tests\test_simulation_api.py tests\test_simulation_proposal.py tests\test_session_report.py tests\test_session_report_export.py tests\test_replay_distribution.py tests\paper_trading\test_liquidation.py tests\ui\test_v3_market_ui.py -q` | **249 passed** |
| PG suites (disposable `wored-pgqa`, port 18090, db `wored_qa`) | `WORED_TEST_DATABASE_URL=… python -m pytest tests\paper_trading\test_session_execution_pg.py tests\paper_trading\test_session_report_pg.py tests\paper_trading\test_session_replay.py -q` | **26 passed** |
| Browser | `python -m pytest tests\ui\test_v3_market_browser.py -q` (fixture uvicorn on :18080) | **23 passed** |
| Security UI | `python -m pytest tests\ui\test_security.py -q` | **28 passed** |

### P1.3 supplement (forming-candle & gap indicators, executed 2026-09-30, this session)

| Run | Command (from `D:\WORED`) | Result |
|---|---|---|
| API + UI + registry | `python -m pytest tests\test_market_workspace.py tests\test_instrument_registry.py tests\ui\test_v3_market_ui.py -q` | **86 passed** (7 new `build_forming`/candles-field unit+API tests, 2 new UI hook/style tests) |
| Browser (re-run) | `python -m pytest tests\ui\test_v3_market_browser.py -q` (fixture uvicorn on :18080) | **24 passed** (adds `test_forming_and_gap_indicators_render_P13`; screenshot `artifacts/v3-browser/04-forming-gaps.png`) |
| Lint/type | `ruff check` + `mypy` on `webui\market_workspace.py` | clean (3 pre-existing lint debts cleared: 1×F401, 2×E741) |

### G3/G4 supplement (stream resync §53 + URL-state §39, executed 2026-09-30, this session)

| Run | Command (from `D:\WORED`) | Result |
|---|---|---|
| Stream + market API + V3 UI | `python -m pytest tests\test_market_stream_unit.py tests\test_market_workspace.py tests\ui\test_v3_market_ui.py -q` | **87 passed** (3 new `id:`-frame unit tests, 2 new G3/G4 source guards, cache-buster assertion bumped to `?v=20260930-2`) |
| V3 browser suite | `python -m pytest tests\ui\test_v3_market_browser.py -q` (fixture uvicorn on :18080) | **28 passed** — adds `test_url_selections_restored_and_synced_G4`, `test_unknown_url_period_falls_back_G4`, `test_stream_mode_sse_when_healthy_G3`, `test_stream_falls_back_to_poll_and_resumes_G3`; screenshots `05-url-state-g4.png`, `06-stream-fallback-g3.png` |
| Full non-browser UI layer | `python -m pytest tests\ui -q --ignore=tests\ui\test_v3_market_browser.py` | **260 passed** |
| Lint/type | `ruff check webui\market_workspace.py tests\…(4 files)` · `mypy webui\market_workspace.py` | **All checks passed** · **no issues found** (1 new F401 in `test_market_stream_unit.py` cleared) |

G3 evidence is functional, not just structural: the browser test aborts the
`/api/v3/market/{key}/stream` route, shows the controller switching the chart
container to `data-stream-mode="poll"` while `/state` polling keeps the quality
strip and the `is-live` entry badge correct, then un-routes, reloads and asserts
the mode returns to `"sse"`. G4 evidence: `?period=5m&horizon=1h` restores both
selectors, `data-url-synced-to` carries the instrument/period/horizon triple,
changing a selector rewrites the URL in place (no navigation), and an unsupported
`?period=99m` is rejected rather than fabricated.

**G4 evidence limit:** browser proof covers instrument/period/horizon. The
positions `status`/`account` filters are restored and mirrored by the same code
path and are covered by the source guard in `test_v3_market_ui.py`
(`TestV3MarketG3G4SourceContract`), but have no dedicated browser assertion.
**G3 evidence limit:** the browser test proves transport *degradation and recovery*
at the UI level; it does not prove replay of frames missed during the outage.

### G2 supplement (staged, §55 `POST /api/v3/forecasts`, executed 2026-09-30, this session)

| Run | Command (from `D:\WORED`) | Result |
|---|---|---|
| New command suite | `python -m pytest tests\test_forecast_command_v3.py -q` | **35 passed** (9 pre-request guards, 6 history-integrity, 6 accepted-semantics, 5 idempotency, 8 status contract, 1 session attribution) |
| V3 regression | `python -m pytest tests\test_forecast_command_v3.py tests\test_forecast_workspace.py tests\test_market_workspace.py tests\test_market_stream_unit.py tests\test_instrument_registry.py -q` | **117 passed** |
| Full non-browser UI layer | `python -m pytest tests\ui -q --ignore=tests\ui\test_v3_market_browser.py` | **260 passed** — identical to the pre-G2 baseline, no regression |
| Lint/type | `ruff check webui\forecast_command_v3.py tests\test_forecast_command_v3.py` · `mypy webui\forecast_command_v3.py` | **All checks passed** · mypy reports **0 errors in the new module** (62 errors remain in the pre-existing import graph: `app.py`, `paper_api.py`, `workspace_read.py` — the untouched `forecast_workspace.py` yields the same 62, which is the baseline proof) |
| Wider sweep (observation) | `python -m pytest tests\stabilization tests\ui\test_v3_market_ui.py tests\test_perp_candles.py -q` | **266 passed, 7 failed, 12 skipped** — the 7 failures are in `tests\stabilization` (`test_contracts.py` 3× adapter-contract `AssertionError: ValueError not raised`; `test_forecast_lifecycle.py::TestForecastQueueDB` 4× `TimeoutError` connecting to Postgres). They ran in a process that **did not import the new module**, are outside the F01–F16 gate set, and are recorded here as pre-existing/environment-dependent, not as a V3 regression. Not fixed in this session (stabilization is another agent's active zone). |

What the staged module proves at L1/L2-fake (no real DB):

* **§47 acknowledgement semantics** — `202` always carries `accepted=true,
  completed=false`; `forecast_id`/`result_path` appear only when the stored row is
  `completed`; `queued`/`running` are non-terminal, `failed`/`expired` terminal.
* **§57 base from fact** — the base is the **last closed** perpetual candle
  (`base_time` = its `end_at`, `base_price` = its `close`), identified by
  `base_snapshot_id`; no bid/ask/mark, no forming bar, no live ticker path at all.
* **§51 perpetual-only** — history is read from `trader_v1_perp_candles` through the
  same registry/derivation/validation code as the chart (`5m` reads `1min` rows at
  5× depth, `1d` from `60min`); an unregistered or spot key is `404` with **zero**
  candle queries issued, a foreign `venue` row is `503 venue_mixture`.
* **§59 pre-request matrix** — incompatible `horizon`/`period` and an over-budget
  step count are answered **before** any history read; the exact
  `forecast_candle_count` is returned in the accepted body.
* **Fail-closed history** — `insufficient_history`, `missing_intervals` (with the
  named gaps and `last_good_end_at`), `invalid_candle_ohlc`, `history_query_failed`,
  `history_unavailable` — never a shortened or substituted series.
* **Idempotency** — advisory xact lock + payload-intent compare: same key/same
  intent replays the stored request (echoing the **original** base, not a
  re-derived one), same key/different intent is `409 idempotency_conflict`.

**G2 evidence limits (important — §55 is NOT closed by this):**

1. **Not mounted.** `webui/app.py` is unchanged, so `POST /api/v3/forecasts` and
   `GET /api/v3/forecasts/requests/{id}` do not exist in the running service. There
   is no L3 browser and no L4 live evidence for G2.
2. **Row-level identity was not stored** *(closed by step 3 — see the G2 step-3
   supplement below; the *runtime* half of this limit still stands because the
   routes are unmounted and the live DB is not migrated)*. The legacy integer
   `horizon_hours` remains a documented lossy mapping (a 15m horizon floors to `1`
   there) — covered by `test_legacy_horizon_hours_mapping_is_documented_lossy`.
3. **Execution context is still spot-derived.** The queue worker
   (`app.run_prediction_request_async` → `build_prediction_context` →
   `fetch_klines` → HTX **spot** `/market/history/kline`) is untouched. So
   `market_data_source: "trader_v1_perp_candles"` in the 202 body describes what
   *this command validated and stored*, not what the model's pattern context was
   built from. Closing that gap requires parameterising the context builder — the
   riskiest remaining change, touching the F-gate forecast path.
4. Tests use a recording fake pool; no real Postgres was touched (the
   DB-integration `TestForecastQueueDB` cases could not reach Postgres on this host
   — see the wider-sweep row above).

### G2 step-3 supplement (additive row-level identity DDL, §55/§117, executed 2026-09-30, this session)

Captain's choice was **"step 3: additive DDL, no `app.py`"** — give the stored
request row its own V3 identity without touching the live route table.

| Run | Command (from `D:\WORED`) | Result |
|---|---|---|
| DDL contract | `python -m pytest tests\test_forecast_command_v3.py::TestAdditiveDdlContract -q` | **6 passed** (migration declares all 6 columns; startup script declares all 6; startup script stays single-statement-per-`;`-chunk (the `ensure_prediction_schema` splitter); every migration statement is `ADD COLUMN IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` / `DO $$…$$` CHECK only, with `DROP/TRUNCATE/DELETE/UPDATE/SET NOT NULL/ALTER COLUMN` forbidden; new columns nullable and default-free; status-query columns exist) |
| Command suite (full) | `python -m pytest tests\test_forecast_command_v3.py -q` | **47 passed, 4 skipped** (4 skips are the opt-in disposable-PG cases, no `WORED_TEST_DATABASE_URL` on this host) |
| Module SQL ↔ DDL columns | `python -m pytest tests\test_forecast_command_v3.py::TestModuleQueriesMatchTheDdl -q` | **3 passed.** The fake pool accepts *any* statement, so a misspelled or non-existent column would stay green at L1 and fail only on a real DB. These tests capture the module's **own** INSERT/SELECT text (`_recorded_queries`) and assert: every written column exists in `forecast_requests` (CREATE + ALTER of `forecast_schema` + `forecast_queue.SCHEMA` — 23 columns found), all six identity columns are in the INSERT, `VALUES` arity == column count with exactly one inlined literal (`'pending'`) and 14 binds, and every `r.<col>` the status query reads exists too. `test_ddl_column_extraction_is_itself_sane` pins known columns so the guard cannot pass vacuously, and `test_status_select_shape_matches_the_real_table` now runs **that captured statement** on Postgres instead of a hand-copied query |
| Identity behaviour | included above: `test_stored_row_carries_v3_identity`, `test_status_reports_identity_from_row`, `test_legacy_row_admits_it_has_no_v3_identity` | the INSERT writes `instrument_key/period/horizon/horizon_steps/base_snapshot_id/market_data_source`; the status body reads them **from the row** and labels provenance `row` vs `legacy_row_no_v3_identity` — a legacy row is never back-filled or guessed |
| V3 regression | `python -m pytest tests\test_forecast_workspace.py tests\test_market_workspace.py tests\test_market_stream_unit.py tests\test_instrument_registry.py -q` | **82 passed**; the same four plus the new command suite in one process: **129 passed, 4 skipped** |
| Perp store + full non-browser UI layer | `python -m pytest tests\test_perp_candles.py tests\ui -q --ignore=tests\ui\test_v3_market_browser.py` | **272 passed, 1 skipped**; `tests\ui` alone re-run: **260 passed** — identical to the pre-G2 baseline |
| Lint/type | `python -m ruff check webui\forecast_command_v3.py webui\forecast_schema.py tests\test_forecast_command_v3.py scratch\build_qa_v3_ddl_rehearsal.py` · `python -m mypy --config-file mypy.ini webui\forecast_command_v3.py` | **All checks passed** · mypy: **0 errors in the changed module** (the 62 errors are the pre-existing import-graph set, identical from the untouched `forecast_workspace.py`) |
| Disposable-PG rehearsal | `docker compose -p woredqa -f docker-compose.qa.yml run --rm --build checks python -m pytest tests/test_forecast_command_v3.py::TestDdlOnDisposablePostgres -q -rs` | **EXECUTED 2026-10-03 — 4 passed** on the isolated `wored_qa` disposable Postgres (`postgres:16`, tmpfs, internal network, no host ports/volumes): the additive V3 identity DDL + migration apply cleanly to a fresh schema and the six identity columns + `idx_forecast_requests_v3_identity` are created. *(Originally, 2026-09-30, this was blocked by the host WSL/HCS wedge — `docker start` hung; that block cleared after the reboot.)* **Isolation gotcha:** `docker-compose.qa.yml` uses the default project name, so a bare `run` collides with production's `wored_default` network — it must be run under a separate `-p woredqa` project, and **never** with `--remove-orphans` (that would target the live containers) |

What step 3 adds:

* `db/migrations/20260930_forecast_request_v3_identity.sql` — six **nullable**
  columns (`instrument_key`, `period`, `horizon`, `horizon_steps`,
  `base_snapshot_id`, `market_data_source`), four DO-block CHECK domain guards
  (key grammar, period set, horizon set, steps 1–48) and a **partial** index
  (`WHERE instrument_key IS NOT NULL`, so the legacy planner is untouched). Header
  records the rationale and the non-destructive rollback (drop the 6 columns +
  index; the same facts remain in the `forecast_jobs` JSONB payload).
* `webui/forecast_schema.py` — the same six `ADD COLUMN IF NOT EXISTS` plus the
  partial index, **single statements only**, because
  `app.ensure_prediction_schema` splits that script on `;` (proven by the contract
  test). The CHECK guards deliberately live only in the psql file.
* Enforcement split is honest: §51 (perpetual vs spot) is **not** a DB concern. The
  grammar CHECK accepts `htx:spot:BTC-USDT`; the venue authority is the registry +
  `forecast_command_v3`, which 404s an unregistered/spot key before any insert.
  `test_instrument_check_is_a_grammar_guard_not_a_venue_gate` records that fact
  instead of pretending the constraint covers it.

**Step-3 evidence limits:** all green results are **L1** (source contract +
recording fake pool; the combined V3 run is 126 passed / 4 PG-skipped). No
L2-Postgres proof exists for this DDL yet (see the blocked rehearsal row), and because
`forecast_schema.py` runs at webui startup, **the live `trading` database will gain
these nullable columns at the next `webui` restart** — an action deliberately *not*
performed in this turn.

### G2 step-4 supplement (routes mounted in `webui/app.py`, §55, executed 2026-09-30, this session)

Captain's choice: **"Монтаж без perpetual-контекста"** — mount the command router,
leave `build_prediction_context`/`fetch_klines` (HTX spot) untouched.

| Run | Command (from `D:\WORED`) | Result |
|---|---|---|
| Mount contract (source) | `python -m pytest tests\test_forecast_command_v3.py::TestMountedInRealApp -q` | **3 passed** — `app.py` imports and `include_router`s the command router; the read-model is mounted **before** it (so `result_path = /api/v3/forecast/{id}` stays owned by the read router); and a method+path diff against `forecast_workspace` shows **no route collision** |
| In-process app (host env) | `python -c "import app; …"` with `PYTHONPATH=webui;chatbot;collector` | **the production app object imports and exposes** `POST /api/v3/forecasts` + `GET /api/v3/forecasts/requests/{request_id}` beside the read-model's `GET /api/v3/forecasts` / `GET /api/v3/forecasts/accuracy`; **22 `/api/v3` routes** (method-level) on the host FastAPI build. This is not the container — see the pre-flight table below for the runtime check |
| Command suite | `python -m pytest tests\test_forecast_command_v3.py -q` | **50 passed, 4 skipped** |
| V3 + simulation suites | `python -m pytest tests\test_forecast_command_v3.py tests\test_forecast_workspace.py tests\test_market_workspace.py tests\test_market_stream_unit.py tests\test_instrument_registry.py tests\test_positions_workspace.py tests\test_simulation_api.py tests\test_simulation_proposal.py tests\test_session_report.py -q` | **233 passed, 4 skipped** |
| F-gate (unchanged set) | `python -m pytest tests\test_f16_telegram_contract.py tests\test_owner_identity.py tests\test_day_report.py tests\test_execution_status.py tests\test_perp_candles.py tests\test_ui_presenters.py tests\test_workspace_presenters.py -q` | **115 passed, 1 skipped** — identical to the recorded F01–F16 baseline |
| Full non-browser UI layer | `python -m pytest tests\ui -q --ignore=tests\ui\test_v3_market_browser.py` | **260 passed** — baseline |
| Lint/type on `app.py` | `python -m ruff check webui\app.py` vs a HEAD copy (`scratch\appbaseline\app_head.py`) | **21 findings before, 21 after** → no new lint debt; `mypy` total stays **62** with **zero** findings on the touched lines (68, ~2000) |

**Not yet true at runtime:** the container runs uvicorn **without `--reload`**, so the
mounted routes exist only in the working tree. Making them live needs
`docker compose restart webui` (or `up -d --build webui`), which the Captain must
confirm because it also (a) applies `forecast_schema.PREDICTION_TABLES_SQL` to the
**live `trading` database** — the six nullable identity columns arrive at that boot —
and (b) recreates **collector** through `depends_on`, restarting ingestion for ~1 min.
Until then §55 has **no L4 evidence** in the *serving* sense: the routes are not
answered by the running process. The re-probe after the restart must show the path
count **18 → 19** (`/api/v3/forecasts` gains `post`, `/api/v3/forecasts/requests/{request_id}`
is new), an unauthenticated `POST /api/v3/forecasts` → **401**, and no Traceback in
`docker compose logs webui`.

Container pre-flight (executed this turn, **read-only**, no restart, no DB write):

| Probe | Command | Result |
|---|---|---|
| Deployed source is current | `docker compose exec -T webui ls -l /app/forecast_command_v3.py` | present, **24 037 bytes**, mtime matches the working tree (bind mount `./webui:/app`) |
| New imports exist in `/app/app.py` | `docker compose exec -T webui grep -c v3_router /app/app.py` | **12** (= the host file: 6 imports + 6 `include_router`) |
| Runtime env can load the mounted app | `docker compose exec -T webui python -c "import app; app.app.openapi()"` | imports cleanly; **19 `/api/v3` paths** (was 18) with **`/api/v3/forecasts` → `get` + `post`** and a new **`/api/v3/forecasts/requests/{request_id}`** → dependencies resolve inside the image, so the restart cannot fail on `ImportError` for this router |
| Process is still the old one | `docker ps` (webui `Up 4 hours`) vs `/app/app.py` mtime (this minute); uvicorn has no `--reload` (proven in the G3/G4 L4 round) | the **running** process serves 18 paths; the 19-path app exists only in a fresh import |
| **Version skew (new finding)** | host `pip show fastapi starlette` vs in-container `import fastapi, starlette` | host **fastapi 0.135.1 / starlette 0.52.1** vs container **fastapi 0.141.1 / starlette 1.7.0** (`webui/requirements.txt` pins only `fastapi>=0.115.0`, starlette is transitive). In 0.141.1 `include_router` stores lazy `_IncludedRouter` placeholders, so a host-style `getattr(route,'path')` scan reports **0** v3 routes inside the container while `openapi()` correctly reports **19**. Route-shape assertions in host tests are host-version artefacts; runtime truth must be read from `openapi()` / live HTTP |
| Stale-bytecode watch-out | `ls webui/__pycache__/app.cpython-311.pyc` (bind-mounted, 260 974 bytes, written by the container's 3.11) | the runtime revalidates that `.pyc` against `app.py`'s mtime+size, which the Windows bind mount reports — expected to invalidate correctly, but **if the restarted process still shows 18 paths, the first suspect is this stale `app.cpython-311.pyc`**, not the mount |

**Method note — use `openapi()["paths"]` for route-mount claims.** In fastapi 0.141.1 the
`include_router` result is kept as a lazy `_IncludedRouter` placeholder whose objects have
no `.path`, so a `getattr(route, "path")` scan over `app.app.routes` inside the container
returns **0** `/api/v3` entries even though the routers are registered; the same scan on
host fastapi 0.135.1 materialises `APIRoute`s and returns 22. `openapi()["paths"]` resolves
both cases and is the probe used in the table above.

**Housekeeping note:** running host-side python against `webui/` writes `*.cpython-314.pyc`
into the shared `./webui/__pycache__`, which is bind-mounted into the container. The
container uses 3.11 (`*.cpython-311.pyc`), so the tags never collide and nothing is
loaded from them — recorded because it makes host test runs visible in the runtime tree.

**Step-4 closure decision (Captain, 2026-09-30):** *"Закрыть шаг 4 без L4."* G2 step 4 is
recorded as closed **at the mount/import-evidence level**. The serving-level restart was
deliberately **not** performed and is **not** claimed: `POST /api/v3/forecasts` remains
*not answered by the running process*, the live `trading` DB has **not** gained the six
identity columns, and the disposable-PG DDL rehearsal (risk 8b) is still outstanding.
*(Update 2026-10-03: risk 8b is now CLOSED — the rehearsal executed, 4 passed on isolated `wored_qa`; see the "Disposable-PG rehearsal" row above.)*
§55 therefore stays **STAGED — NOT CLOSED**, with the path to closure fully documented
(risks 7–11) rather than papered over. Residual risk 11 stays open by decision, not by omission.

### Live-runtime incident (found 2026-09-30 UTC ~19:25–19:55, pre-restart probe)

While preparing the restart confirmation, the live stack was probed read-only and found
**already degraded**, independently of any V3 change:

| Probe | Result |
|---|---|
| `docker ps` | `htx_trading_bot_webui` — `Up 5 hours **(unhealthy)**` (the healthcheck calls `/readyz`, which 503s) |
| `GET /readyz` | **503** — `{"redis":true,"postgres":true,"collector_feed":false,"forecast_worker":true,"ready":false}` |
| `GET /healthz` | `200 {"alive":true,"candle_gap_count":360}` — liveness fine |
| `redis-cli keys "*"` | **2 keys only**: `paper_trading:runner:heartbeat`, `market:perpetual:htx:candle_gap_count` |
| `ticker:btcusdt` | **(nil)** — the writer is `collector/htx/websocket.py` (`ex=60`) |
| `market:perpetual:htx:BTC-USDT` | **missing** — the key `webui/market_workspace.py` reads for the V3 quote state (`ex=60`) |
| Redis eviction check | `maxmemory=0`, `noeviction`, 1.24 MB used → **nothing evicted; the writers stopped** |
| `ai_journal` max timestamp | `14:29:36 UTC`, ageing exactly +0.5 min/min (300→316.7 min across probes) — the 15-min journal job produces nothing since then |
| Collector restart | startup lines (`Starting Collector with WebSocket…` … `Collector running in background.`) at **14:42:52–14:43:21 UTC**, `RestartCount=0` — the container had restarted ~14 min before the probe |
| Post-restart behaviour | HTX REST 200s and WS `Connected to HTX WebSocket` seen at 21:44–21:46 (+07), then **zero log lines in the following 20 min** (`docker logs --since 20m` → 0 lines) while previously logging every few seconds |
| Heartbeat freeze test | `paper_trading:runner:heartbeat` `completed_at=1790781484.16` (**14:38:04 UTC**) identical across a 12 s re-read, although the heartbeat job runs every 5 s |

Diagnosis (honest level: **symptom-proven, root-cause-unproven**): the collector process is
**wedged since ~14:38 UTC** — container `Up`, Python event loop no longer progressing
(scheduler heartbeat frozen, logging stream silent, both market-data writers producing
nothing, and because the keys carry `ex=60` their absence is exactly what readiness then
reports). One loop stall explains all four observations at once; nothing in the V3 working
tree was involved, and no V3 code path was changed by the probes (all read-only).
Consequence accepted with the close-without-L4 decision: live V3 `/api/v3/market/{key}/state`
would currently report an honest **source outage** — fail-closed behaviour working as
specified, on a degraded runtime.

**Fix attempt this turn (authorized: "минимальный фикс" of the collector zone):** the Captain
approved a minimal collector fix. The correct first move is to recycle the wedged container so
the `restart: unless-stopped` policy brings a fresh event loop, then re-probe. That recycle did
**not** complete — it is a Docker-Desktop host fault, not a code fault:

| Action | Result |
|---|---|
| `docker compose restart collector` | stuck at `Restarting` for **>20 min**, never `Started`; `docker ps` still shows the **same** `Up 6 hours` process |
| `docker kill htx_trading_bot_collector` | hung with no return within the turn |
| heartbeat re-read after both | `completed_at=1790781484` **byte-identical** (same `instance_id`) → still wedged |
| `GET /readyz` re-read | still **503**, `last_journal_age_minutes` now **351.5** |
| light docker calls | `docker ps`/`docker version` **do** answer; heavy ones (`exec`, `inspect`, `logs --since`, `top`, `restart`, `kill`) **hang** — the containerd/shim lifecycle path is wedged |

**No collector code was edited.** A source change would be unjustified here: the loop-freeze
root cause is unproven, the change could not take effect while the container cannot be
recycled, and `collector/htx/*` sits on the F-gate ingestion path — editing it on an unproven
theory is exactly the speculative risk AGENTS forbids. **This incident is NOT resolved by the
agent and is handed back to the Captain**: recovery needs a Docker-Desktop / WSL restart
(`wsl --shutdown` or Restart Docker Desktop), which drops **all five services** and is a
destructive, host-level action that must be an explicit Captain decision, not an agent side
effect. Once Docker can service lifecycle again, `docker compose up -d collector` and re-probe
`redis-cli keys "ticker:*"` + `/readyz` → expect the keys to return and readiness to flip 200.

**Root-cause correction (executed after the Captain's explicit "Да, терминировать
docker-desktop"):** the fault is **not** the collector application — it is the host **WSL/HCS
VM layer**. Evidence, in order:

| Step | Result |
|---|---|
| `wsl --terminate docker-desktop` | **OK** — `docker-desktop` → `Stopped`; the wedged collector was reaped |
| `docker version` right after | `500 Internal Server Error` on the named pipe → engine mid-restart (expected) |
| relaunch `Docker Desktop.exe` + poll ×10 (150 s) | engine **never** returned → `Error response from daemon: Docker Desktop is unable to start` |
| Captain's surfaced error | `Wsl/Service/CreateInstance/HCS_E_CONNECTION_TIMEOUT` on `wsl-bootstrap run --base-image …/docker-desktop.iso` |
| `wsl -l -v` | **hangs** (no response) |
| `wsl --shutdown` | **hangs**, never returns |
| service check | `vmcompute` = Running (Manual), `WslService` = Running (Automatic) — service objects up, but the HCS VM worker will not instantiate |
| privilege check | `(IsInRole(Administrator))` = **False** — the agent shell is **non-elevated** |

Interpretation: `vmcompute`/`WslService` are Running, yet every HCS *instance creation* times
out, so the WSL↔Hyper-V VM subsystem is wedged at the host level. This retroactively explains
the original symptom mix: all in-guest docker ops (`exec`/`inspect`/`logs`/`top`/`restart`/
`kill`) route through HCS and hung, while metadata-only calls (`ps`, `docker version`,
`redis-cli` via an already-running container, `curl`) kept answering. The collector "wedge"
was a downstream symptom, not the cause.

**Recovery the agent cannot perform (needs elevation):** restart the WSL/HCS services from an
**elevated** PowerShell — `Restart-Service vmcompute -Force` then `Restart-Service WslService
-Force` (older builds: `LxssManager`) — or reboot Windows; then start Docker Desktop and wait
through the ISO cold-boot, then `docker compose up -d`. The agent's non-elevated shell cannot
restart these services and its `wsl` control commands hang. Several hung client processes are
now accumulated (collector restart, kill, recreate, `wsl -l`, `wsl --shutdown`); a service
restart or reboot will clear them. **No V3 result is invalidated** by this incident: G1–G4,
MC-01…MC-22 and P0–P6 remain as recorded; only step-5 live L4 re-probe was blocked on the host
— **that block has since cleared** (see "Recovery + step-5 execution").

### Recovery + step-5 execution (2026-10-02)

**Host recovery (Captain action).** The Captain rebooted Windows. Docker came back
(`Server 29.8.1`, no hang), all five services returned, and the collector market feed was
**fully restored** — confirming the wedge was the host **WSL/HCS VM layer**, not app code:

| Probe | Result |
|---|---|
| `redis-cli keys "*"` | full keyspace back: `ticker:btcusdt`, `ticker:ethusdt`, `market:perpetual:htx:BTC-USDT`, `market:perpetual:htx:candles:*`, `market_context:*` |
| `paper_trading:runner:heartbeat` | **new** `instance_id` (`297dd78a…`, was `3176fa75…`) and advancing `completed_at` — a fresh loop is running |

**Second, unrelated fault found immediately after.** Post-reboot `webui` was still `unhealthy`
with `/readyz` `{"redis":true,"postgres":false,"collector_feed":true,"forecast_worker":false}`.
Logs showed `Postgres pool bootstrap` failed **30×** then `gave up`, every attempt with
`AttributeError: 'NoneType' object has no attribute 'decode'`. This was **not** the host/WSL
issue and **not** Postgres — Postgres was healthy and reachable.

**Read-only diagnosis (throwaway probes, live DB never mutated).**

| Probe | What it did | Result |
|---|---|---|
| connect probe | `asyncpg.connect(dsn)` + `SELECT 1` only — **no DDL** | `CONNECT_OK 1` → the pool-open is fine, so the throw is in the schema step |
| rollback probe | ran every bootstrap schema statement inside a transaction that is **always rolled back** | pinpointed `PREDICTION_TABLES_SQL[23]` = a **comment-only fragment**, head `-- Additive V3 identity … db/migrations/ -- 2`; `ROLLED_BACK — live DB unchanged` |

**Root cause — a self-inflicted regression from G2 step 3.** `webui/forecast_schema.py` step-3
comment contained a **literal semicolon inside a `--` line**: `-- splits this script on ";", …`.
`app.ensure_prediction_schema` splits `PREDICTION_TABLES_SQL` with a naive `.split(";")` that is
unaware of comments, so that in-comment `;` cut the string and isolated a comment-only statement.
Postgres returns a **nil command tag** for a comment-only query and asyncpg's simple-query path
then crashes on `.decode()` of `None`. (The Phase-2 comment works precisely because it has no `;`.)

**Fix (semantics-preserving, zero DDL change).** Removed the raw `;` from that comment in
`webui/forecast_schema.py` (worded as "the semicolon character") and added an explicit caution
comment forbidding raw semicolons in these lines. Re-ran the rollback probe → `ALL_STATEMENTS_OK`
(PREDICTION 30 / QUEUE 11 / SIM 4 / PAPER 9) with `ROLLED_BACK — live DB unchanged`. Both
throwaway probes were deleted from `./webui` after use.

**Step 5 executed (Captain-approved: "Перезапустить webui сейчас").** `docker compose restart
webui` — the deliberate serving-level action the close-without-L4 decision had waived:

| Check | Result |
|---|---|
| `docker ps` webui | `Up (healthy)` |
| `docker logs` | `Postgres pool bootstrap complete (attempt 1)` — no `gave up`, no Traceback |
| `GET /readyz` | `ready:true`, `{"redis":true,"postgres":true,"collector_feed":true,"forecast_worker":true}`, `last_journal_age_minutes:11.5` |
| in-container `openapi()` | **19** `/api/v3` paths; `/api/v3/forecasts` = `get`+`post`; `/api/v3/forecasts/requests/{request_id}` present |
| `POST /api/v3/forecasts` (unauth) | **401** — served by the running process (**L4**, not just the imported object) |
| live `trading` DB columns | all six present: `instrument_key, period, horizon, horizon_steps, base_snapshot_id, market_data_source` |
| live `trading` DB index | `idx_forecast_requests_v3_identity` present |

**What is now CLOSED vs still open for G2.** Step 5 is **CLOSED at L4** for both the serving
level (routes live, auth-guarded) and the additive DDL (six nullable columns + partial index on
the real DB, legacy rows untouched). **Not** claimed: end-to-end perpetual correctness — the
worker's pattern context is still spot-derived (the §55 residual), so a V3 forecast is not yet
perpetual-correct through the worker. Housekeeping: two stale `Created` duplicates
(`18cc…_redis`, `fe00…_postgres`) from earlier remain and are harmless; a future
`docker compose up -d` prunes them.

### Worker perpetual-context path (implemented + LIVE, code + L1 + executed read-only proof — 2026-10-02)

Closed the §51 gap in code. **Latent crash found first:** the V3 `job_payload` carries a top-level
`v3` key, and the bootstrap `runner` does `run_prediction_request_async(app, request_id, **payload)`,
but that signature had no `v3` parameter — so **any V3 job would `TypeError` and fail in the worker**
(not merely forecast from spot). Even ignoring that, `build_prediction_context` sourced *everything*
(history, recent candles, RSI/MACD/SMA, seasonal patterns, `spot_snapshot`) from `fetch_klines`
(HTX **spot** `/market/history/kline`).

Changes in `webui/app.py` (4 edits, additive; the legacy spot path is the untouched default):
- `run_prediction_request_async` now takes `v3: dict | None = None`; when `v3.market_data_source ==
  trader_v1_perp_candles` it builds context through the new perpetual branch and pins `as_of` to
  `v3["base_time"]` (§57: steps counted from the closed base candle, not `now()`).
- New `build_prediction_context_perpetual(connection, v3, ...)` reads closed candles through the
  **imported** `forecast_command_v3._closed_candles` (same table/validation as the command and chart),
  adapts ISO-`start_at`+string-OHLCV items to the `{time, open..volume: float}` indicator shape, runs
  the same feature/pattern helpers, and emits a `market_snapshot` with perpetual provenance
  (`market_data_source`, `instrument_key`, `period`, `base_snapshot_id`) instead of `spot_snapshot`.
- Short/gapped perpetual history **fails closed** (`ValueError("perpetual_history:…")`) — no spot
  backfill (§51). Base price pinned to `v3["base_price"]` (§57).
- New stdlib import `from decimal import Decimal`; module imports `_closed_candles`, `HISTORY_TABLE`,
  `load_registry`, `_require_spec`, `CandleSourceError`.

Evidence: `python -m pytest tests/test_forecast_command_v3.py -q` → **54 passed, 4 skipped** (added
`TestWorkerPerpetualContext`, 4 source-contract tests; fixed the mount-import assertion for the new
multi-symbol import). `py_compile` clean under container 3.11.

**Honest limits (why this is NOT yet closed at L4):** the tests are **source-contract**, matching the
suite's standing rule that no host test imports the production `app` (needs env/Redis/Postgres) — they
pin wiring and the §51/§57 guarantees, they do **not** execute a live perpetual forecast.
**Status after the approved restart (`docker compose restart webui`, Captain chose "только рестарт, без
POST")**: the new code is now **live in the running process** — in-container check shows
`build_prediction_context_perpetual` present and `run_prediction_request_async` carries the `v3`
parameter, so the latent `TypeError` no longer fails V3 jobs; `bootstrap complete (attempt 1)`,
`/readyz` `ready:true`, **19** `/api/v3` paths, webui `healthy`. **Still not done:** an executed live
perpetual forecast — no authenticated `POST /api/v3/forecasts` was made (by the Captain's choice), so
`completed` with `market_data_source=trader_v1_perp_candles` and perpetual-derived context remains
unproven. The code path is deployed and ready; the runtime proof is deferred, not assumed.

### Executed read-only §51 proof + a real bug found by running it (2026-10-02)

To lift the §51 context guarantee above *source-contract* without touching financial state, the
new `build_prediction_context_perpetual` was executed once inside the container against the **live**
`trader_v1_perp_candles` (a throwaway script that only `SELECT`s closed candles — no forecast row,
no model call, no write). Live rows exist (BTC-USDT 1min 18481, 15min 1233, 60min 309, 4hour 79).

Running it **caught a real bug the source tests could not**: the builder called `app.normalize_period`,
which only accepts `ALLOWED_PERIODS` (`15min`) and raises `HTTPException(400)` on the V3 workspace
form `15m` — a live perpetual job would have failed at context build. (The V3 *command* avoids this
because it imports `normalize_period` from `prediction_timeframes`, not `app`'s shadowed local one.)
**Fix** (`webui/app.py`): import the alias-aware `prediction_timeframes.normalize_period as
normalize_period_v3` and use it in the builder and the perpetual worker branch; the legacy branch keeps
app's own normalizer. Pinned by a new source-contract test `test_period_uses_the_v3_aware_normalizer`.

Executed result (post-fix), proving perpetual provenance with **no spot substitution** (§51):
`market_data_source=trader_v1_perp_candles`, `market_snapshot.kind=perpetual_closed_candle`,
`spot_snapshot` absent, `base_price` pinned (86385.8), 36 recent closed candles, 3 seasonal patterns,
RSI/MACD computed from the perpetual series.

Evidence after the fix + restart: `pytest tests/test_forecast_command_v3.py -q` → **55 passed,
4 skipped**; `py_compile` clean (3.11); `docker compose restart webui` → webui `healthy`,
`/readyz` `ready:true`, `bootstrap complete (attempt 1)`, **19** `/api/v3` paths; in-container introspect
of the **running** worker confirms `build_prediction_context_perpetual` uses `normalize_period_v3`, the
shadowed `normalize_period(period)` call is gone, and `run_prediction_request_async` carries `v3`.
**Remaining for a caveat-free G2 close:** an *authenticated* `POST /api/v3/forecasts` that runs to
`completed` (writes a real forecast + calls the model = financial-state, gated on Captain approval).

## Residual ТЗ items (audit of 2026-09-30, gaps G1–G5)

| ID | ТЗ item | Status | Evidence / reason |
|---|---|---|---|
| G1 | §9/§129 — F01–F16 non-regression as a P6 exit gate | **CLOSED** | "F01–F16 non-regression" section below — no F-ID dropped |
| G2 | §55 — `POST /api/v3/forecasts` async command (`request_id`) | **CLOSED at serving+DDL (L4); perpetual-context IMPLEMENTED code+L1, executed/L4 pending** | steps 1/3/4/5 as above (routes live, `POST → 401`, six identity columns + index on the real `trading` DB). The §51 worker gap is now **coded**: `run_prediction_request_async` accepts the `v3` block (fixes a latent `TypeError` that failed every V3 job) and routes perpetual jobs through a new `build_prediction_context_perpetual` that reads the same `trader_v1_perp_candles` source as the command/chart and fails closed — see "Worker perpetual-context path". **Live now, executed-proof pending:** the new code is live in the running `webui` after the approved restart (`v3` param present → latent `TypeError` gone; builder exposed; `/readyz` 200; 19 paths), and the perpetual **context builder was executed read-only against live candles** (see "Executed read-only §51 proof") — which surfaced and fixed a real `normalize_period` 400-on-`15m` bug — but **no authenticated live `POST` was performed** (Captain: restart only), so an executed perpetual forecast (`completed` with `market_data_source=trader_v1_perp_candles`) is not yet observed. The read-model rows and MC-05…08 are unaffected |
| G3 | §53 — SSE resync/backoff | **CLOSED (with a stated limit)** | Server tags snapshot frames with `id: <sequence>`; client falls back to `/state` polling and resumes SSE. **Limit:** the server does not *replay* from `Last-Event-ID` — on reconnect the browser sends it, and the stream resumes with the newest snapshot; no missed-frame replay |
| G4 | §39 — URL-state persistence of market selections | **CLOSED** | instrument/period/horizon/positions-status/account mirrored via `history.replaceState`; validated against real option/registry values; browser tests above |
| G5 | §5 — scenario mode | **DEFERRED (documented)** | Returns the exact reason code `mode_not_supported`; a separate product decision, not a defect |

## L4 live-stack checks (P6.6, executed 2026-09-30, UTC ~11:2x)

| Check | Command | Result |
|---|---|---|
| Runtime up | `docker compose ps` | 5 services up; webui/chatbot/postgres healthy |
| V3 routes live | `docker compose exec -T webui python -c "…app.openapi()…"` | **18 `/api/v3` paths mounted** (market/forecast/positions/simulation) |
| Live code freshness | md5 compare container vs workspace | `templates/workspace.html`, `static/ui/market-chart.js`, `static/ui/market-workspace.js` **byte-identical** to current tree |
| Old URLs preserved | `curl /`, `/alerts`, `/predictions`, `/journal`, `/workspace` | **303 → login** (routes exist, auth enforced; not 404); `/login` 200 |
| V3 API auth scope | `curl /api/v3/instruments` unauthenticated | **401** — API guard active |
| Live HTX read-only | `docker compose logs collector --since 5m` | `GET https://api.hbdm.com/linear-swap-ex/market/detail/merged?contract_code=BTC-USDT "200 OK"` — **perpetual** endpoint, seconds old |
| Live quote freshness | `redis-cli get ticker:btcusdt` | age **9.0 s**, price present |
| Telegram live | `docker compose logs chatbot` | aiogram polling running for bot `@W_W_O_O_bot` since 2026-09-29, container healthy 29h; poll watchdog active |

Telegram note: an inbound message round-trip (Captain sends `/status` to the bot)
was not performed by the agent; polling liveness is the verified evidence.

### L4 re-verify after G3/G4 (executed 2026-09-30, UTC ~14:4x; webui recreated)

The P1.3/G3 caveat in "Residual risks" item 1 is **resolved**: `webui` was
rebuilt/restarted, so the running process now matches the working tree.

| Check | Command | Result |
|---|---|---|
| Rebuild/restart | `docker compose up -d --build webui` | webui **Recreated → Started**, later `Up (healthy)`. **Side effect:** compose also recreated **collector** (webui `depends_on` it) → ingestion restarted ~1 min; chatbot/postgres/redis untouched |
| Collector recovered | `docker compose logs --since 2m collector` | HTX **perpetual** endpoints `200 OK`: `linear-swap-ex/market/detail/merged`, `linear-swap-api/v1/swap_index`, `swap_funding_rate`, `mark_price_kline`, `swap_contract_info` |
| Live quote freshness | in-container redis read of `ticker:btcusdt` | found, price **83701.51**, age **0.6 s** |
| Asset parity (file) | md5 host tree vs container `/app` | `workspace.html 57a2eb68…`, `market-workspace.js c0e07d4d…`, `market-chart.js 6fd3dbc5…`, `market-workspace.css fee55af7…`, `market_workspace.py 4392cf5f…` — **all five identical** (bind mount `./webui:/app`) |
| Process parity (code) | `docker compose top webui` before restart | uvicorn runs **without `--reload`** → the *reason* a restart was required: files were current, the loaded Python process was not |
| G3/G4 client live | `curl /static/ui/market-workspace.js` | **200, 37 257 bytes**, contains `function syncUrl`, `function startPollFallback`, `function applyUrlSelections` |
| G3 server code live | in-container `market_workspace._sse_event('snapshot',…,event_id=7)` | first line **`id: 7`**; `keepalive` frame still starts with `event: keepalive` (no `id:`) |
| V3 routes mounted | in-container `app.app.openapi()` | **18 `/api/v3` paths** (unchanged from P6.6), incl. `/api/v3/market/{instrument_key}/stream` |
| Old URLs preserved | `curl` on :8080 | `/`, `/alerts`, `/predictions`, `/journal`, `/workspace` → **303 → login**; `/login` → **200** |
| V3 API auth scope | `curl /api/v3/instruments`, `/api/v3/market/…/state` unauthenticated | **401** on both |
| Post-restart errors | `docker compose logs --since 4m webui` (grep Traceback/ERROR/Exception) | **no matches**; `/readyz` 200 |

L4 limits for this round: no authenticated page-level browser run on the live
stack (the agent used no credentials), and no live SSE frame was captured from the
running process (the stream route is auth-guarded) — the `id:` line is proven by
executing the **deployed** source in-container, and poll-fallback/resume behaviour
is proven at L3 on the fixture server. `/openapi.json` on the published port is
itself auth-guarded (303 → `/login?next=/openapi.json`), hence in-container
enumeration. Two `docker.exe` invocations crashed with host memory/page-file
errors and were re-run successfully.

## Matrix

| ID | Requirement (short) | Status | Evidence | Level |
|---|---|---|---|---|
| MC-01 | Perpetual identity identical across quote/candles/forecast/order/report; no spot substitution | PASS | `test_instrument_registry.py`, `test_market_workspace.py`, browser `test_instruments_only_perpetual_MC01` | L1–L3 |
| MC-02 | Valid OHLCV, wicks, increasing UTC; gap/duplicate/revision detected; forming period + gaps made visible without a synthesized body | PASS | candle validator + `build_forming` in `market_workspace`; unit/API tests in `test_market_workspace.py` (empty→None, bounds from last close, elapsed/remaining clamp, endpoint `forming`/`period_seconds`); browser `test_live_feed_allows_entry_MC02` + `test_forming_and_gap_indicators_render_P13` (`dataset.formingState="forming"`, `gapCount`, gaps hidden with no holes); screenshot `04-forming-gaps.png` | L1–L3 |
| MC-03 | Bid/ask/mark/index/funding + per-source age visible; stale blocks entry, no fake prices | PASS | `test_market_stream_unit.py`, browser `test_degraded_feed_blocks_entry_MC03`, screenshot `artifacts/v3-browser/03-degraded-blocked.png` | L1–L3 |
| MC-04 | 1440×900 and 390×844: chart/axes/grid/legend/crosshair/volume/price readable, no horizontal overflow | PASS | browser MC04 tests (paint, overflow, 44px tap targets); screenshots `02-chart-candles.png`, `04-mobile-390-market.png` | L3 |
| MC-05 | 4 horizons with numbers/text/request/model/version/coverage; pending/partial/failed/stale distinct | PASS | `test_forecast_workspace.py`, browser MC05 tests, screenshot `05-forecast-overlay.png` | L1–L3 |
| MC-06 | Forecast candle mathematically valid; point-only drawn as line+band, never invented wicks | PASS | overlay unit tests + browser `test_forecast_overlay_draws_line_band_MC05_06` | L1–L3 |
| MC-07 | Future separated from fact; immutable forecast_id compared to later real closes | PASS | forecast read-model tests + browser `test_forecast_vs_fact_in_window_MC07`, screenshot `07-forecast-vs-fact.png` | L1–L3 |
| MC-08 | Accuracy/coverage on holdout vs baseline; insufficient sample → N/A | PASS | `test_forecast_workspace.py`; browser `test_forecast_accuracy_na_when_unsettled_MC08` + `…beats-baseline…MC08`; screenshot `08-forecast-accuracy-holdout.png` | L1–L3 |
| MC-09 | All `SimulationPlanV1` fields, two budgets, time; exact persist + restore after refresh | PASS | `test_simulation_api.py`; browser `test_simulation_persist_and_restore_MC09`; screenshot `09-session-constructor.png` | L1–L3 |
| MC-10 | Unexecutable strategy / bad limits / short history / stale quote block approval with exact reason | PASS | `test_simulation_api.py` negative tests; browser MC10 stale-quote path | L1–L3 |
| MC-11 | Approve fixes plan_hash; no silent mutation; days share session_id | PASS | plan-hash + immutability tests in `test_simulation_api.py` | L2 |
| MC-12 | Questionnaire → AI proposal is draft only; invalid proposal rejected by deterministic validator | PASS | `test_simulation_proposal.py` (stub LLM); browser MC12; screenshot `12-ai-proposal.png` | L1–L3 |
| MC-13 | Timeout/quota/invalid AI keeps manual start; no paid fallback, no auto-start | PASS | `test_simulation_proposal.py`; browser `test_ai_proposal_failure_keeps_manual_path_MC13`, screenshot `13-proposal-failed.png` | L1–L3 |
| MC-14 | Manual/auto accounts, budgets, limits, origin/actor, ledger independent | PASS | `test_session_execution_pg.py` (11 tests on disposable PG) | L2-PG |
| MC-15 | Real paper lifecycle accepted→order→fill→position→close→ledger→net; idempotent against duplicate/lost response | PASS | `test_session_execution_pg.py` lifecycle + idempotency | L2-PG |
| MC-16 | Mark-triggered liquidation: exactly one close fill, postings, status, reason, marker; stale → pending/blocked | PASS | `test_liquidation.py` (state machine, L1) + `test_session_execution_pg.py` (PG crash/retry, L2) | L1–L2-PG |
| MC-17 | UI filters open/pending/closed/liquidated; IDs/price/time/source stable across transitions | PASS | `test_positions_workspace.py`; browser MC17 tests; screenshot `17-positions-dashboard.png` | L1–L3 |
| MC-18 | Report only after settlement/reconciliation; includes liquidations, costs, funding, both accounts | PASS | `test_session_report.py` (pure), `test_session_report_pg.py` golden (5) | L1–L2-PG |
| MC-19 | Old report immutable after new session/wiring; JSON/CSV/HTML == screen byte-normalized | PASS | `test_session_report_pg.py` immutability; `test_session_report_export.py` round-trip parity | L1–L2-PG |
| MC-20 | ROI/drawdown/win rate/expectancy/PF from ledger/equity series; div-by-zero & small sample → null/N/A | PASS | `test_session_report.py` metric gating tests | L1 |
| MC-21 | Multi-window replay reproducible by data hash; train ≠ holdout; no-trade/buy-hold with same costs | PASS | `tests/paper_trading/test_session_replay.py` (10, incl. engine golden 83.920012 and PG bridge), `test_replay_distribution.py` (14) | L1–L2-PG |
| MC-22 | Auth/CSRF/owner scope; mobile/WebView; old URLs + chart containers preserved; **live HTX read-only and real Telegram as separate evidence level** | PASS | auth/CSRF/scope: `tests/ui/test_security.py` (L3-fixture) + live 401/303 checks (L4); mobile: MC04 browser tests (L3); old URLs/containers: live 303-not-404 sweep + byte-identical `workspace.html`/chart JS in the running container (L4); live HTX: collector `linear-swap-ex` 200 + 9 s Redis quote (L4); Telegram: live polling for `@W_W_O_O_bot` (L4-liveness) | L3–L4 |

## F01–F16 non-regression (P6 exit gate, §9/§129 — verified 2026-09-30)

The P6 exit criterion requires proving the older functional-parity matrix
`docs/WEBUI-FUNCTION-PARITY.md` (F01–F16) **did not regress** under the V3 work.
Baseline before V3 (2026-09-28 S7): **F02, F03 = PASS; F01, F04–F16 = PARTIAL;
0 FAIL / 0 BLOCKED.** "Non-regression" means no F-ID may drop to FAIL and the
AGENTS guardrails (old routes, `app.js`, chart containers, palette, auth) stay intact.

| Check | Command | Result |
|---|---|---|
| UI fixture/unit layer (F01, F06–F16 evidence + V3 UI) | `python -m pytest tests\ui -q --ignore=tests\ui\test_v3_market_browser.py` | **258 passed** |
| F-adjacent root suites (F02 owner-identity, F05 day-report, F16 telegram, F11 async/presenters, execution-status, perp-candles) | `python -m pytest tests\test_f16_telegram_contract.py tests\test_owner_identity.py tests\test_day_report.py tests\test_execution_status.py tests\test_perp_candles.py tests\test_ui_presenters.py tests\test_workspace_presenters.py -q` | **115 passed, 1 skipped** |
| Property-based finance (`test_trading_math.py`) | `pytest …` | **BLOCKED on host — `ModuleNotFoundError: hypothesis`** (pre-existing env gap, runs in the Docker QA image; not a V3 change) |
| Guardrails preserved | grep templates/static | `/static/app.js` still referenced (`index.html`); chart containers intact (`#predictionChart`, `#trChartWrap`, `#tdChartContainer`); `base.html` palette + LWC v5 script unchanged; old URLs 303-not-404 (MC-22 L4) |

**Verdict:** **no F-ID regressed** from PASS/PARTIAL → FAIL. F02/F03 remain PASS
(their evidence was staging/prod; V3 added new `simulation_*` read-models and did
not alter the `paper_v2_*` execution path). The 14 PARTIAL rows stay PARTIAL at the
same evidence level — V3 neither fixed nor broke their higher-level acceptance. The
only BLOCKED item is an environmental missing-dependency (`hypothesis`), unrelated
to V3. Per §172 the PG/browser higher-level F-acceptance (F02/F03/F05/F15 integration)
was established on staging in the S7 round; this gate confirms V3 did not degrade it,
not that V3 re-ran staging.

## Overall verdict

MC-01…MC-22 **PASS** at their recorded evidence levels (L1–L4), with the
explicit limits below. This is not a production-readiness claim: fixture-level
browser runs use the synthetic QA server, MC-21 runs a labelled probe strategy,
and the Telegram item proves polling liveness, not an inbound round-trip.
The **P6 F01–F16 non-regression gate is verified green** (see the F01–F16
section): no functional-parity ID regressed under V3.
Of the residual ТЗ items found in the 2026-09-30 audit, **G1 (F-gate), G3 (§53
stream resync) and G4 (§39 URL-state) are closed**. **G2 (`POST /api/v3/forecasts`)
is CLOSED at serving + DDL level (L4)** — the router is mounted and live (19 `/api/v3`
paths, `POST → 401`), the additive identity DDL is applied on the real `trading` DB
(six columns + `idx_forecast_requests_v3_identity`) and rehearsed clean on disposable
`wored_qa` (4 passed), and the §51 worker now routes perpetual jobs through
`build_prediction_context_perpetual` (proven by read-only execution against live
candles, which surfaced and fixed a real `normalize_period` bug). **The one thing not
done is an authenticated live `POST → completed`** (a financial write the Captain
declined), so G2 is honestly *serving+DDL+context-correct, executed-forecast-declined*,
not fully caveat-free. **G5 (scenario mode) remains deliberately deferred**. The
overall MC verdict above does not depend on G2 or G5, and neither is presented as
satisfied.

## Residual risks / limits (honest)

1. **L4 freshness** — re-verified **after** the G3/G4 round (see the "L4 re-verify
   after G3/G4" table): webui was rebuilt/restarted, container assets are md5-equal
   to the working tree, the deployed `_sse_event` emits `id:` frames, 18 `/api/v3`
   routes are mounted, old URLs still 303 and the live HTX perpetual feed was 0.6 s
   old. Any later code change invalidates this row until re-probed. Authenticated
   page-level rendering on the live stack was **not** exercised (no credentials used
   by the agent), and no live SSE frame was captured from the running process —
   both stay at L3-fixture / deployed-source-level evidence.
   (Historical note: the earlier "container serves pre-P1.3 assets" caveat is
   obsolete — it was resolved by the restart, and the files were always md5-equal
   because `./webui:/app` is a bind mount; what lagged was the Python process,
   which runs uvicorn without `--reload`.)
2. **Replay strategy** — MC-21 is proven with a labelled long-momentum probe on
   synthetic bars, not `BaselineV1Strategy` on real historical candles; the
   reproducibility/holdout/benchmark contract holds, strategy quality does not
   follow from it.
3. **PG isolation form** — suites ran against a disposable single-container
   Postgres (port 18090, throwaway schema per run, `/trading` refused by guard),
   not `docker-compose.qa.yml`; isolation properties are equivalent, the compose
   form should be used in a CI run.
4. **Ledger scale** — `NUMERIC(20,8)` rounds per stored row; reports match the
   *stored* ledger exactly, host-engine nets may differ ≤ 1e-8 per row (documented
   in `test_session_replay.py`).
5. **`db/` is gitignored** — the V3 simulation/report DDL and
   `20260930_forecast_request_v3_identity.sql` under `db/migrations/` are untracked;
   must be resolved deliberately at commit time.
6. Screenshots under `artifacts/v3-browser/` were produced by the browser suites
   in the earlier P1–P5 runs; the same tests were re-executed green in this run,
   but fresh screenshot files were not re-saved here.
7. **G2 spot-context gap — RESOLVED in code, executed read-only (2026-10-02/03).**
   The queue worker no longer builds V3 pattern context from HTX **spot**: for a
   perpetual job (`v3.market_data_source == trader_v1_perp_candles`) it routes through
   `build_prediction_context_perpetual`, which reads the same `trader_v1_perp_candles`
   source as the command/chart, pins `base_price`/`as_of` to the closed base candle (§57)
   and **fails closed** on short/gapped history (no spot backfill, §51). This builder was
   **executed read-only against live candles** (proving perpetual provenance, no spot),
   which is how the `normalize_period` 400-on-`15m` bug was found and fixed. **Residual:**
   an *authenticated live `POST → completed`* (financial write) was declined, so a stored
   `completed` V3 forecast with perpetual-derived context has not been observed.
8. **G2 step-3 DDL — EXECUTED (2026-10-02/03).** (a) Step 5 (`docker compose restart
   webui`, Captain-approved) applied the DDL to the **live `trading` DB**: the six identity
   columns + `idx_forecast_requests_v3_identity` were confirmed present (`psql -U bot -d
   trading`), legacy rows untouched. (b) The **disposable-PG rehearsal ran 2026-10-03** —
   `docker compose -p woredqa -f docker-compose.qa.yml run --rm --build checks python -m
   pytest …::TestDdlOnDisposablePostgres` → **4 passed** on isolated `wored_qa`. The
   bootstrap-breaking `.split(";")` comment bug in `forecast_schema.py` was also found and
   fixed. Remaining related risk is only commit-time tracking (risk 5).
9. **`forecast_requests` identity columns are nullable by design** — nothing forces a
   V3 row to carry them, so an insert from another code path can still write a
   half-described request. The status endpoint admits this
   (`identity_provenance: "legacy_row_no_v3_identity"`) instead of guessing; tightening
   it into a NOT-NOW guarantee would violate §117 for existing rows.
10. **Host ≠ runtime web framework** — `webui/requirements.txt` pins `fastapi>=0.115.0`
    and lets starlette float, so the host test env (fastapi 0.135.1 / starlette 0.52.1)
    and the running container (0.141.1 / 1.7.0) differ. This already changed observable
    behaviour once: route enumeration (`_IncludedRouter` placeholders vs materialised
    `APIRoute`s). Any V3 assertion about routing must be re-checked inside the container
    (`openapi()`), not only on the host. Pinning the versions is a separate deliberate task.
11. **Mounted but not serving — RESOLVED (2026-10-02).** After the Captain-approved
    step-5 restart the live uvicorn process serves **19** `/api/v3` paths (was 18 on the
    stale process), `POST /api/v3/forecasts → 401`, and the live `trading` DB carries the
    six identity columns. §55 is now serving-level real; the only caveat left is the
    declined authenticated `completed` forecast (risk 7), not routing.

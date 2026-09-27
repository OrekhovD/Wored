# Trader Integration RFC — F08 Data-Lineage & V2 Mapping

> Status: DRAFT  
> Date: 2026-09-27  
> TZ: QODER-WEBUI-WORKSPACE-V2-TZ-20260926.md §13 (data lineage), F08  

## 1. Problem statement

`webui/trader_api.py` (prefix `/api/trader`) serves 7 endpoints:

| Endpoint | Data source | Identity |
|---|---|---|
| `/candles` | Redis HTX stream → JSON | global (single instrument) |
| `/forecast` | Postgres `forecast_points` JOIN | global |
| `/state` | Postgres `paper_v2_days` (latest) + Redis `trader:mode` + _mock_state skeleton | **latest-day, no owner scoping** |
| `/positions` | Postgres `paper_v2_positions` (all) | **no owner filter** |
| `/activity` | Postgres `paper_v2_events` | global |
| `/mode` (POST) | In-process `_current_mode` + Redis `trader:mode` | **no owner** |
| `/stream` | SSE placeholder | N/A |

The `paper_trading` domain (canonical WP-A) uses `owner_id`-scoped access:
`owner_id_from_webui("admin")` → UUID5 → `get_active_day(owner_id)` → positions by `account_id`.

Both read the **same tables** (`paper_v2_days`, `paper_v2_positions`) but:
- Trader API has no `owner_id` filter — it's safe today because there's exactly one owner, but architecturally divergent.
- Trader `mode` (trade / reduce_only / pause) is an **operational gate** orthogonal to `AutomationState`.

## 2. Identity mapping

```
┌──────────────────┐       ┌────────────────────────────┐
│ Principal        │       │ paper_trading owner_id      │
│ (auth layer)     │       │ (domain layer)             │
├──────────────────┤       ├────────────────────────────┤
│ password_admin   │──────▶│ UUID5("wored:owner:admin") │
│ telegram_admin   │──────▶│ UUID5("wored:owner:{tg_id}")│
│ internal_service │──────▶│ N/A (read-only prediction) │
└──────────────────┘       └────────────────────────────┘
```

Currently `paper_api.py` hardcodes `_pt_owner_id("admin")`. When F08 integrates, this becomes `resolve_principal(request) → owner_id_from_webui(principal.subject, principal.telegram_user_id)`.

## 3. Mode semantics

| Trader mode | Effect | Maps to |
|---|---|---|
| `trade` | entries allowed (default) | automation state unchanged; entries NOT blocked |
| `reduce_only` | no new entries; only closes | equivalent to `risk_blocked` gate at entry level |
| `pause` | full halt of the automation runner | equivalent to `AutomationState.paused` |

The mode does NOT change `DayState`. It's an additional "operational overlay" that the Command Drawer and automation engine consult before executing.

## 4. V2 integration plan

### Phase 4a: Read integration (this RFC scope)
1. **workspace BFF** reads `trader:mode` from Redis → adds to `status_bar.mode`.
2. **workspace presenter** derives `can_enter` capability from mode + day state + automation state.
3. No changes to `/api/trader/*` endpoints (read-only, already work).

### Phase 4b: Owner scoping (future)
1. Add `owner_id` filter to `get_positions` and `get_state` queries.
2. Remove `_mock_state()` skeleton; source everything from domain.
3. Single code path: webui → adapter → service → repository (owner-scoped).

### Phase 4c: Mode unification (future)
1. Migrate `trader:mode` from Redis-only to `paper_v2_days.settings_snapshot.operational_mode`.
2. Command Drawer gains `mode.set` action → POST to domain (idempotent).
3. Automation engine reads mode from domain (not Redis).

### Phase 4d: UI merge (post-4c)
1. `/trader` → 301 `/workspace?tab=trader-deck` (deep-link preserved).
2. Chart overlays (candles + forecast band) render inside workspace context panel.
3. Agent roster/budget → removed (skeleton placeholders, never were real data).

## 5. Data lineage table

```
HTX WebSocket → collector → Redis (ticker:*)
                                    ↓
                            /api/trader/candles (read)
                            
forecast_engine → PG forecast_points → /api/trader/forecast (read)

paper_trading runner → PG paper_v2_days ──┬→ /api/trader/state (read)
                                          └→ workspace_read → BFF (read)

paper_trading execution → PG paper_v2_positions ─┬→ /api/trader/positions (read)
                                                 └→ workspace_read (read)

trader:mode (Redis) ──→ /api/trader/state (read)
                     ──→ workspace BFF (read, Phase 4a)
```

No WRITE path goes through trader_api except `POST /mode` (Redis/in-process). All financial writes go through WP-A (`paper_api.py` → `adapter.py` → `service.py`).

## 6. Normative constraints

1. **WP-A is the only canonical write path.** `/api/trader/mode` (Redis) is an operational gate, not a financial command. When Phase 4c migrates it, the domain becomes canonical for mode too.
2. **Never fabricate trader data.** `_mock_state()` must only populate fields marked `source="skeleton"`. Real data fields (`mode`, `plan_status`, `leverage`) must come from PG or Redis.
3. **Owner scoping is a precondition for multi-user.** Until 4b ships, single-owner assumption holds and is documented.
4. **URL compat (V2-10).** `/api/trader/*` endpoints remain at their paths through all phases.

## 7. Immediate action items (4a)

- [x] This RFC
- [ ] workspace BFF reads trader mode → exposes in `status_bar`
- [ ] presenter: add `can_enter` capability (mode=trade AND state=running)
- [ ] UI: status bar shows "Режим: trade/reduce_only/pause"
- [ ] test: mode=reduce_only → `can_enter=False`
- [ ] `/trader` redirect target updated to `/workspace` (not `/trading-day`)

## 8. Future (out of this RFC scope)

- Owner-scoped trader queries (4b)
- Mode migration to domain (4c)
- Chart overlay inside workspace context panel (4d)
- Agent roster real data from Decision Journal (4d)
- SSE `/api/trader/stream` real implementation

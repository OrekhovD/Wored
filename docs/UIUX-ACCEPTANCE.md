# WORED UI/UX Acceptance — S2

## Acceptance Status

| ID | Description | Status | Evidence |
|---|---|---|---|
| UI-00 | Baseline | PASSED | commit 90c3252 |
| UI-01 | Tokens, formatters, presenters | PASSED | commit a81a262, 48 tests |
| UI-02 | Navigation, IA, shell, /system | PASSED | commit bc0f116 |
| UI-03 | Async states, errors, recovery | PASSED | commit 0c62c3e, O04/O06 fixed |
| UI-04 | API contract, backend gaps | PASSED | commit 8deb8c6 |
| UI-05 | Charts | PASSED | commit 1d42e4e |
| UI-06 | Forecast form | PASSED | commit 42a959e |
| UI-07 | Ticket dialog | PASSED | commit 9de1a38 |
| UI-08 | Daily session | PASSED | commit c3eba59 |
| UI-09 | Supporting pages | PASSED | commit d51f0de |
| UI-10 | Mobile, a11y, Telegram | PASSED* | commit d51f0de (*Telegram manual pending) |
| UI-11 | Deterministic tests | IN PROGRESS | fixture_data, fixture_app, test files being created |
| UI-12 | Release, docs, rollback | IN PROGRESS | implementation report, testing docs created |

## Defects Fixed
- O01: Mixed RU/EN nav → 4 groups (UI-02)
- O02: command_deck zoom → removed (UI-10)
- O04: double % → fixed (UI-03)
- O06: legacy pending → execution_state (UI-03)
- O10: Login mixed lang → RU only (UI-02)

## Backend Gaps (Explicit)
1. R05 DB-backed order/revision tests deferred
2. v3/net/idempotency/policy verification (UI-04 bridge gate)
3. Telegram WebView manual check pending owner
4. LLM_PAID_ENABLED=false, metered gate untested

## Rollback Reference
- Pre-UI commit: `b9102587761665f1cae86ecf6f2d04de1b087cd5`
- UI commits: `90c3252` through `8d2cb63`
- Rollback: `git revert <commit>` in reverse order, or restore pre-UI image
- No DB migrations affected by UI changes
- No .env, provider config, trading formulas, or collector/chatbot changes

## Manual Checks Pending
- Telegram Mini App on both bots (portrait/landscape)
- Real device keyboard in forms
- Owner confirmation of visual changes
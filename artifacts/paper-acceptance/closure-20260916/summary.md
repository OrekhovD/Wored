# Paper Trading Acceptance — Summary

**Date:** 16 September 2026
**Git SHA:** `3f29a05`
**Validator:** `scripts/validate_paper_evidence.py`

## Computed Summary

| Status | Count |
|---|---|
| PASS | 18 |
| FAIL | 1 |
| BLOCKED | 9 |
| **Total** | **28** |

## Key Findings

### AC-26: FAIL (was incorrectly PASS)
- `feed_freshness` assertion FAIL: 12/13 snapshots had feed, 1 missing (snapshot #1 before collector fully up)
- `state_transition` assertion FAIL: `day_states=[null]`, `day_transitions=0`. Recovery blocked→unblocked is not a day state transition.
- Previous report declared overall PASS despite nested FAIL — corrected to FAIL.

### Corrected count: 18/28 PASS (not 19 or 20)
Previous report said 19/9 — incorrect. Table had 20 PASS marks but summary said 19. Actual honest count: 18 PASS, 1 FAIL, 9 BLOCKED.

### BLOCKED cases (9)
| AC | Blocker | Next Action |
|---|---|---|
| AC-05 | test_postgres_idempotency.py not created | Write PostgreSQL concurrent idempotency test |
| AC-06 | test_postgres_recovery.py not created | Write crash/recovery fault injection test |
| AC-14 | No recorded HTX perpetual dataset | Record or find historical dataset with real bid/ask/mark |
| AC-16 | test_postgres_plan_races.py not created | Write plan race test in QA |
| AC-19 | Browser testing not performed | Run browser assertions at 1440x900 and 390x844 |
| AC-20 | Telegram client testing not performed | Run mock + real Telegram tests |
| AC-21 | test_closeout.py not created | Write dual-account closeout test in QA |
| AC-24 | Migration rehearsal not performed | Write migration rehearsal in disposable QA |
| AC-27 | No natural signal observed | Observe after day started with running state |

### Audit fixes F01-F08
All 8 defects fixed in code. Regression evidence: collector logs show PostgreSQL wired, recovery complete, entries unblocked. F07/F08 deployed in latest collector rebuild.
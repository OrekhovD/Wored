# WORED UI/UX Testing — S2

## Test Infrastructure
- **Isolated server:** `127.0.0.1:18080` (no production .env/PG/Redis/Telegram/LLM)
- **Fixture app:** `tests/ui/fixture_app.py` — real templates/static, substituted dependencies
- **Clock:** Fixed `2026-09-09T12:00:00Z`
- **IDs:** 9001 (forecast), 7001 (session), 501 (entry)
- **Runner:** `scripts/run_ui_acceptance.py --host 127.0.0.1 --port 18080 --browser all --artifacts artifacts/uiux`

## Test Files
| File | Coverage |
|---|---|
| test_shell.py | 13 routes, nav structure, /system, login |
| test_async.py | execution_state, backoff, freshness, HTTP errors |
| test_forecast.py | form, idempotency, 202 navigation, 48-step chart |
| test_ticket.py | Long/Short dialog, focus trap, preview, confirm |
| test_session.py | readiness states, controls, close_all |
| test_accessibility.py | keyboard, labels, aria-live, contrast |
| test_security.py | auth redirect, CSRF, 401/403, no secrets |

## Presenter Tests
- `tests/test_ui_presenters.py` — 48 tests, all passing
- Coverage: fmt_usdt, fmt_price, fmt_pct, fmt_pnl, fmt_qty, fmt_time, state_label, is_terminal, is_valid_forecast, present_deck_ui, present_preview_ui, present_health

## Requirements
- `playwright==1.51.0`, `pytest==8.3.4`
- Lock: `tests/ui/requirements.lock` (with hashes)
- Install: `pip install --require-hashes -r tests/ui/requirements.lock`
- Browsers: `playwright install chromium webkit`

## Viewport Matrix
- 390×844 (mobile portrait)
- 844×390 (mobile landscape)
- 1280×800 (desktop)
- 320×800 (reflow)

## UI States Tested
ready, stale, error, queued, running, partial, failed, expired, no_trade, preview_invalid

## Artifacts
- `artifacts/uiux/<commit>/junit.xml`
- `artifacts/uiux/<commit>/results.json`
- `artifacts/uiux/<commit>/screenshots/`
- `artifacts/uiux/<commit>/contrast.json`
- `artifacts/uiux/<commit>/console.json`
- `artifacts/uiux/<commit>/network.json`
- `artifacts/uiux/<commit>/manual-checks.md`

## Manual Checks (Pending Owner)
- Telegram Mini App: both bots (@RACHELLO_BOT, @W_W_O_O_bot)
- Portrait/landscape safe areas
- Keyboard in form
- Restore existing request ID
- Deny unauthorized user
# WORED UI/UX Manual Checks — Pending Owner

## Telegram Mini App Verification (UI-10)

**Status:** PENDING OWNER

### Bots to test
- @RACHELLO_BOT
- @W_W_O_O_bot

### Checklist
- [ ] Authorized user can enter Mini App
- [ ] Return/reopen works
- [ ] Safe areas portrait (390×844)
- [ ] Safe areas landscape (844×390)
- [ ] Keyboard opens in form fields
- [ ] Restore existing request ID after reload
- [ ] Unauthorized user denied (fixture or test circuit)
- [ ] No initData/session/token in localStorage or console

### Environment to record
- OS: _________
- Telegram version: _________
- Date: _________
- Device: _________

### Notes
- Automated mock WebApp and Chromium do NOT replace this check
- If no device/owner available: status remains `telegram_manual=pending`, S2=partial
- Production SDK URL not changed to shim
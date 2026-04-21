# Project Backlog

## Pending Features & Technical Debt

### 1. Gemini 3 Flash Adapter (Phase 4 of Qodo Audit)
**Status:** ⏸ Paused / Moved to Backlog
**Description:** Implement a new provider adapter for Gemini 3 Flash (`gemini-3.0-flash`).
**Details:**
- **Adapter Path:** `providers/gemini_flash_adapter.py`
- **Interface:** Must inherit from `AIProviderInterface`.
- **API Choice:** Need to finalize whether to use Native Gemini API or OpenAI-Compatible API endpoint.
- **Resilience:** Must wrap requests in `ResilienceOrchestrator` (`CircuitBreaker`, `RetryHandler`, `TimeoutHandler`).
- **Accounting:** Ensure accurate `TokenUsage` extraction. If tokens are missing/unreliable, set `tokens_uncertain = True`.
- **Security:** Ensure `GEMINI_FLASH_API_KEY` is not leaked in logs or during exceptions.
- **Testing Requirements:** 9 unit tests, 2 integration tests, 2 concurrency tests, and 1 smoke test.
**Reference:** Full TЗ is available in `от qodo.md` (lines 46-315).

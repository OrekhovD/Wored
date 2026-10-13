"""Application layer for the Telegram console (ТЗ Telegram Bot V1 §6).

Handlers stay thin: they resolve a Principal, call one use case here, and hand
the result to a presenter.  No trade, risk or accounting maths lives in this
package — those numbers come from ``paper_trading`` and the MC-V3 backend.
"""

import json
from storage.redis_client import get_redis
from storage.postgres_client import get_recent_alert_history
from ai.knowledge_base import build_analysis_knowledge


async def build_context(ticker: str) -> str:
    """Build a rich contextual prompt for the analyst AI using DB data.
    Uses the new knowledge_base with short journal (2 entries / 30 min).
    """
    context = await build_analysis_knowledge(ticker, depth="short")
    return context


async def build_deep_context(ticker: str) -> str:
    """Build an extended context for deep analysis.
    Uses 8 journal entries (2 hours of history).
    """
    context = await build_analysis_knowledge(ticker, depth="deep")
    return context


async def build_comparison_context(ticker_a: str, ticker_b: str) -> str:
    """Build a rich contextual prompt comparing two tickers."""
    ctx_a = await build_analysis_knowledge(ticker_a, depth="short")
    ctx_b = await build_analysis_knowledge(ticker_b, depth="short")
    return f"Запрос на сравнение:\n{ctx_a}\n\n--- vs ---\n\n{ctx_b}\nОсновываясь исключительно на этих свежих данных, проведи лаконичное сравнение активов."

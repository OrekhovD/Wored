"""
News Sentiment Analyzer — scores news sentiment via Ollama Cloud AI.
Uses deepseek-v4-flash as worker model for cost-effective JSON scoring.
Results cached in Redis, persisted to Postgres.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

from news.fetcher import NewsItem, FearGreedData

log = logging.getLogger(__name__)

OLLAMA_CLOUD_URL = os.getenv("OLLAMA_CLOUD_URL", "https://ollama.com/v1")
OLLAMA_CLOUD_API_KEY = os.getenv("OLLAMA_CLOUD_API_KEY", "")
SENTIMENT_MODEL = os.getenv("OLLAMA_WORKER_MODEL", "deepseek-v4-flash")


@dataclass
class SentimentResult:
    """Result of news sentiment analysis."""
    symbol: str
    score: float  # -1.0 to +1.0
    category: str  # Positive / Neutral / Negative
    rationale: str
    key_factors: list[str]
    news_count: int
    fear_greed_value: Optional[int] = None
    fear_greed_class: Optional[str] = None
    analyzed_at: datetime = None

    def __post_init__(self):
        if self.analyzed_at is None:
            self.analyzed_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "score": self.score,
            "category": self.category,
            "rationale": self.rationale,
            "key_factors": self.key_factors,
            "news_count": self.news_count,
            "fear_greed_value": self.fear_greed_value,
            "fear_greed_class": self.fear_greed_class,
            "analyzed_at": self.analyzed_at.isoformat() if self.analyzed_at else None,
        }

    def to_redis_value(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @staticmethod
    def from_dict(data: dict) -> "SentimentResult":
        analyzed_at = None
        if data.get("analyzed_at"):
            try:
                analyzed_at = datetime.fromisoformat(data["analyzed_at"])
            except (ValueError, TypeError):
                pass
        return SentimentResult(
            symbol=data.get("symbol", ""),
            score=float(data.get("score", 0.0)),
            category=data.get("category", "Neutral"),
            rationale=data.get("rationale", ""),
            key_factors=data.get("key_factors", []),
            news_count=int(data.get("news_count", 0)),
            fear_greed_value=data.get("fear_greed_value"),
            fear_greed_class=data.get("fear_greed_class"),
            analyzed_at=analyzed_at,
        )


NEWS_SENTIMENT_PROMPT = """Ты — AI-аналитик новостного сентимента криптовалютного рынка.

Получаешь заголовки и краткие выдержки из последних новостей о криптовалюте.

Задача:
1. Оцени общий сентимент новостного фона от -1.0 (крайне негативный) до +1.0 (крайне позитивный).
2. Определи категорию: Positive / Neutral / Negative.
3. Кратко объясни (2-3 предложения) почему ты дал такую оценку.
4. Выдели 1-2 ключевых фактора, влияющих на сентимент.

Верни СТРОГО JSON (без markdown, без ```):
{"score": 0.35, "category": "Positive", "rationale": "...", "key_factors": ["factor1", "factor2"]}

Если новостей нет или они нерелевантны, верни:
{"score": 0.0, "category": "Neutral", "rationale": "Недостаточно данных для оценки.", "key_factors": []}"""


async def _call_ollama_cloud(prompt: str, user_content: str) -> Optional[dict]:
    """Call Ollama Cloud API for sentiment scoring."""
    if not OLLAMA_CLOUD_API_KEY:
        log.warning("OLLAMA_CLOUD_API_KEY not set, cannot score sentiment")
        return None

    headers = {
        "Authorization": f"Bearer {OLLAMA_CLOUD_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": SENTIMENT_MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "max_tokens": 500,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{OLLAMA_CLOUD_URL}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"].strip()
        # Remove potential markdown wrapping
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        return json.loads(content)

    except json.JSONDecodeError as exc:
        log.error("Sentiment AI returned invalid JSON: %s", exc)
        return None
    except Exception as exc:
        log.error("Ollama Cloud sentiment call failed: %s", exc)
        return None


def _build_news_text(items: list[NewsItem], fear_greed: Optional[FearGreedData] = None) -> str:
    """Build text context from news items for AI analysis."""
    parts = []

    if fear_greed:
        parts.append(f"Fear & Greed Index: {fear_greed.value}/100 ({fear_greed.classification})")
        parts.append("")

    if not items:
        parts.append("Нет свежих новостей по данной криптовалюте.")
        return "\n".join(parts)

    parts.append(f"Последние {len(items)} новостей:")
    parts.append("")

    for i, item in enumerate(items, 1):
        line = f"{i}. [{item.source}] {item.title}"
        if item.summary:
            line += f" — {item.summary[:150]}"
        parts.append(line)

    return "\n".join(parts)


async def analyze_sentiment(
    symbol: str,
    news_items: list[NewsItem],
    fear_greed: Optional[FearGreedData] = None,
) -> SentimentResult:
    """
    Analyze sentiment of collected news via Ollama Cloud AI.
    Returns SentimentResult with score, category, rationale.
    """
    news_text = _build_news_text(news_items, fear_greed)

    # Try AI scoring
    ai_result = await _call_ollama_cloud(NEWS_SENTIMENT_PROMPT, news_text)

    if ai_result and "score" in ai_result:
        result = SentimentResult(
            symbol=symbol,
            score=max(-1.0, min(1.0, float(ai_result.get("score", 0.0)))),
            category=ai_result.get("category", "Neutral"),
            rationale=ai_result.get("rationale", ""),
            key_factors=ai_result.get("key_factors", []),
            news_count=len(news_items),
            fear_greed_value=fear_greed.value if fear_greed else None,
            fear_greed_class=fear_greed.classification if fear_greed else None,
        )
        log.info("Sentiment for %s: score=%.2f category=%s", symbol, result.score, result.category)
        return result

    # Fallback: no AI available, return neutral with Fear & Greed data
    log.warning("AI sentiment unavailable for %s, using fallback", symbol)
    fg_score = 0.0
    fg_category = "Neutral"
    if fear_greed:
        # Map Fear & Greed (0-100) to sentiment (-1 to +1)
        fg_score = (fear_greed.value - 50) / 50.0
        if fear_greed.value >= 60:
            fg_category = "Positive"
        elif fear_greed.value <= 40:
            fg_category = "Negative"

    return SentimentResult(
        symbol=symbol,
        score=fg_score,
        category=fg_category,
        rationale=f"AI недоступен. Fear & Greed Index: {fear_greed.value}/100 ({fear_greed.classification})." if fear_greed else "AI и источники недоступны.",
        key_factors=[f"Fear & Greed: {fear_greed.classification}"] if fear_greed else [],
        news_count=len(news_items),
        fear_greed_value=fear_greed.value if fear_greed else None,
        fear_greed_class=fear_greed.classification if fear_greed else None,
    )

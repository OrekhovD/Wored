"""
News Sentiment Pipeline — fetches crypto news from RSS feeds,
scores sentiment via Ollama Cloud, caches in Redis.
"""
import asyncio
import json
import logging
import re
import time
from typing import Optional
from xml.etree import ElementTree as ET

import httpx
from storage.redis_client import get_redis

log = logging.getLogger(__name__)

# RSS feeds — free, no API key needed
RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://bitcoinist.com/feed/",
    "https://cryptoslate.com/feed/",
]

CACHE_KEY = "news:latest"
CACHE_TTL = 300  # 5 minutes


async def fetch_news() -> list[dict]:
    """Fetch latest crypto news from RSS feeds."""
    r = get_redis()
    cached = await r.get(CACHE_KEY)
    if cached:
        try:
            return json.loads(cached)
        except Exception:
            pass

    items = []
    async with httpx.AsyncClient(timeout=10) as client:
        tasks = [client.get(url) for url in RSS_FEEDS]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

    for resp in responses:
        if isinstance(resp, Exception) or resp.status_code != 200:
            continue
        try:
            root = ElementTree.fromstring(resp.content)
            for item in root.findall(".//item")[:10]:
                title = item.findtext("title", default="")
                description = item.findtext("description", default="")
                pub_date = item.findtext("pubDate", default="")
                link = item.findtext("link", default="")

                # Strip HTML from description
                desc_clean = re.sub(r"<[^>]+>", "", description).strip()
                if len(desc_clean) > 200:
                    desc_clean = desc_clean[:200] + "..."

                items.append({
                    "title": title,
                    "description": desc_clean,
                    "pub_date": pub_date,
                    "link": link,
                })
        except Exception as exc:
            log.warning("Failed to parse RSS feed: %s", exc)

    # Deduplicate by title
    seen = set()
    unique = []
    for item in items:
        key = item["title"].lower().strip()
        if key not in seen and key:
            seen.add(key)
            unique.append(item)

    # Keep top 20
    unique = unique[:20]

    if unique:
        await r.setex(CACHE_KEY, CACHE_TTL, json.dumps(unique, ensure_ascii=False))

    return unique


async def score_sentiment(news_items: list[dict]) -> dict:
    """Score news sentiment using Ollama Cloud."""
    if not news_items:
        return {
            "score": 0.0,
            "category": "Neutral",
            "rationale": "Новостей не найдено.",
            "key_factors": [],
            "headlines": [],
        }

    # Build headlines summary for AI
    headlines = [item["title"] for item in news_items[:10]]
    headlines_text = "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines))

    from ai.router import _call_with_fallback
    from ai.prompts import get_prompt

    prompt = f"Последние заголовки крипто-новостей:\n\n{headlines_text}"
    response = await _call_with_fallback("worker", "news_sentiment", prompt, None)

    # Try to parse JSON from response
    try:
        # Strip badge prefix if present
        json_text = response
        if "</code>\n\n" in json_text:
            json_text = json_text.split("</code>\n\n", 1)[1]
        # Find JSON in response
        match = re.search(r'\{[^{}]*"score"[^{}]*\}', json_text, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            raise json.JSONDecodeError("No JSON found", json_text, 0)
    except Exception as exc:
        log.warning("Sentiment parse failed: %s. Response: %s", exc, response[:200])
        data = {
            "score": 0.0,
            "category": "Neutral",
            "rationale": "Не удалось оценить сентимент.",
            "key_factors": [],
        }

    data["headlines"] = headlines[:5]
    return data
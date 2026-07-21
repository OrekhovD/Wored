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
            root = ET.fromstring(resp.content)
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

    # Direct API call to avoid badge prefix from _call_with_fallback
    import os
    from openai import AsyncOpenAI
    from ai.prompts import get_prompt

    api_key = os.getenv("OLLAMA_CLOUD_API_KEY", "").strip()
    base_url = os.getenv("OLLAMA_CLOUD_BASE_URL", "https://ollama.com/v1")
    model = os.getenv("OLLAMA_SENTIMENT_MODEL", "glm-5.1")

    system_prompt = get_prompt("news_sentiment")
    user_prompt = f"Последние заголовки крипто-новостей:\n\n{headlines_text}"

    data = None
    try:
        client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=30, max_retries=0)
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=2000,
            temperature=0.3,
        )
        raw = (response.choices[0].message.content or "").strip()
        # If content is empty, try reasoning field (some models return reasoning only at low max_tokens)
        if not raw and response.choices[0].message.reasoning:
            raw = response.choices[0].message.reasoning.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            raw = "\n".join(lines).strip()
        # Try direct JSON parse first
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Search for JSON object in response
            match = re.search(r'\{.*?"score".*?\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                raise ValueError(f"No JSON in response: {raw[:200]}")
    except Exception as exc:
        log.warning("Sentiment scoring failed: %s", exc)
        data = {
            "score": 0.0,
            "category": "Neutral",
            "rationale": "Не удалось оценить сентимент.",
            "key_factors": [],
        }

    data["headlines"] = headlines[:5]
    return data
"""
News Fetcher — collects crypto news from multiple sources.
Sources:
  1. CryptoPanic API (if CRYPTOPANIC_API_KEY is set)
  2. CoinTelegraph RSS (free, no key)
  3. CoinDesk RSS (free, no key)
  4. Fear & Greed Index (free, no key)
"""
from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

log = logging.getLogger(__name__)

CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")
CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"
COINTELEGRAPH_RSS = "https://cointelegraph.com/rss"
COINDESK_RSS = "https://www.coindesk.com/arc/outboundfeeds/rss/"
FEAR_GREED_URL = "https://api.alternative.me/fng/"

# Mapping of WORED watchlist symbols to CryptoPanic currency codes
SYMBOL_TO_CURRENCY = {
    "btcusdt": "BTC",
    "ethusdt": "ETH",
    "solusdt": "SOL",
    "bnbusdt": "BNB",
    "xrpusdt": "XRP",
    "dogeusdt": "DOGE",
}

HTTP_TIMEOUT = 15


@dataclass
class NewsItem:
    """Single news article/headline."""
    title: str
    source: str
    url: str = ""
    published_at: Optional[datetime] = None
    summary: str = ""
    currencies: list[str] = field(default_factory=list)


@dataclass
class FearGreedData:
    """Fear & Greed Index snapshot."""
    value: int  # 0-100
    classification: str  # e.g. "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


async def fetch_cryptopanic(symbol: str, limit: int = 10) -> list[NewsItem]:
    """Fetch news from CryptoPanic API. Requires CRYPTOPANIC_API_KEY."""
    if not CRYPTOPANIC_API_KEY:
        log.debug("CryptoPanic API key not set, skipping")
        return []

    currency = SYMBOL_TO_CURRENCY.get(symbol.lower(), symbol.replace("usdt", "").upper())
    params = {
        "auth_token": CRYPTOPANIC_API_KEY,
        "currencies": currency,
        "kind": "news",
        "public": "true",
    }

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(CRYPTOPANIC_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        items = []
        for post in data.get("results", [])[:limit]:
            published = None
            if post.get("published_at"):
                try:
                    published = datetime.fromisoformat(post["published_at"].replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass

            items.append(NewsItem(
                title=post.get("title", ""),
                source="CryptoPanic",
                url=post.get("url", ""),
                published_at=published,
                currencies=[c.get("code", "") for c in post.get("currencies", [])],
            ))
        log.info("CryptoPanic: fetched %d items for %s", len(items), currency)
        return items

    except Exception as exc:
        log.warning("CryptoPanic fetch failed: %s", exc)
        return []


async def fetch_rss(feed_url: str, source_name: str, limit: int = 5) -> list[NewsItem]:
    """Fetch news from an RSS feed (CoinTelegraph, CoinDesk, etc.)."""
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(feed_url)
            resp.raise_for_status()

        root = ET.fromstring(resp.text)
        items = []

        # Standard RSS 2.0 structure
        for item_elem in root.iter("item"):
            title_el = item_elem.find("title")
            link_el = item_elem.find("link")
            desc_el = item_elem.find("description")
            pubdate_el = item_elem.find("pubDate")

            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            if not title:
                continue

            published = None
            if pubdate_el is not None and pubdate_el.text:
                try:
                    # RFC 822 date parsing
                    from email.utils import parsedate_to_datetime
                    published = parsedate_to_datetime(pubdate_el.text)
                except Exception:
                    pass

            items.append(NewsItem(
                title=title,
                source=source_name,
                url=link_el.text.strip() if link_el is not None and link_el.text else "",
                published_at=published,
                summary=(desc_el.text.strip()[:200] if desc_el is not None and desc_el.text else ""),
            ))

            if len(items) >= limit:
                break

        log.info("RSS %s: fetched %d items", source_name, len(items))
        return items

    except Exception as exc:
        log.warning("RSS %s fetch failed: %s", source_name, exc)
        return []


async def fetch_fear_greed() -> Optional[FearGreedData]:
    """Fetch the current Fear & Greed Index (free, no API key)."""
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(FEAR_GREED_URL)
            resp.raise_for_status()
            data = resp.json()

        fg_data = data.get("data", [{}])[0]
        return FearGreedData(
            value=int(fg_data.get("value", 50)),
            classification=fg_data.get("value_classification", "Neutral"),
            timestamp=datetime.fromtimestamp(int(fg_data.get("timestamp", 0)), tz=timezone.utc),
        )
    except Exception as exc:
        log.warning("Fear & Greed fetch failed: %s", exc)
        return None


async def fetch_all_news(symbol: str, rss_limit: int = 5) -> list[NewsItem]:
    """
    Fetch news from all available sources for a given symbol.
    Returns combined list of NewsItems, sorted by relevance (CryptoPanic first, then RSS).
    """
    all_items: list[NewsItem] = []

    # 1. CryptoPanic (symbol-specific, highest relevance)
    cp_items = await fetch_cryptopanic(symbol, limit=10)
    all_items.extend(cp_items)

    # 2. RSS feeds (general crypto news — filter by symbol keyword below)
    currency = SYMBOL_TO_CURRENCY.get(symbol.lower(), symbol.replace("usdt", "").upper())
    keywords = {currency.lower(), symbol.lower().replace("usdt", "")}

    for feed_url, source in [(COINTELEGRAPH_RSS, "CoinTelegraph"), (COINDESK_RSS, "CoinDesk")]:
        rss_items = await fetch_rss(feed_url, source, limit=rss_limit * 3)
        # Filter: keep only items mentioning the target currency
        for item in rss_items:
            text = (item.title + " " + item.summary).lower()
            if any(kw in text for kw in keywords):
                all_items.append(item)
            if len(all_items) >= 15:  # cap total items
                break

    log.info("Total news items for %s: %d", symbol, len(all_items))
    return all_items

"""Public HTX linear-swap WebSocket adapter.

Subscribes to BBO, depth.size_20, mark price and closed 1m/60m candles
for one USDT-margined perpetual contract.  No authentication, no private
channels, no order routing.

The adapter decodes GZIP payloads, maintains ping/pong, reconnects with
backoff, and stamps every message with an explicit ``received_at`` UTC
timestamp.  Decoded messages are delivered to an async callback; the
caller decides what to publish to Redis or PostgreSQL.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import websockets
from websockets.asyncio.client import connect

log = logging.getLogger(__name__)

DEFAULT_WS_URL = "wss://api.hbdm.com/linear-swap-ws"
DEFAULT_PING_INTERVAL = 20.0
DEFAULT_RECONNECT_BASE = 1.0
DEFAULT_RECONNECT_MAX = 30.0

# Subscriptions required for v0.1
SUB_TOPICS = [
    "market.{contract}.bbo",          # best bid/ask
    "market.{contract}.depth.size_20",  # 20-level depth
    "market.{contract}.mark_price",   # mark price for risk/funding
    "market.{contract}.kline.1min",   # 1-minute candles
    "market.{contract}.kline.60min",  # 1-hour candles
]


@dataclass
class WSConfig:
    contract_code: str = "BTC-USDT"
    ws_url: str = DEFAULT_WS_URL
    ping_interval: float = DEFAULT_PING_INTERVAL
    reconnect_base: float = DEFAULT_RECONNECT_BASE
    reconnect_max: float = DEFAULT_RECONNECT_MAX
    # Optional: if set, subscribe only to these topics
    topics_override: list[str] = field(default_factory=list)

    @property
    def topics(self) -> list[str]:
        if self.topics_override:
            return self.topics_override
        return [t.format(contract=self.contract_code) for t in SUB_TOPICS]


@dataclass
class WSMessage:
    topic: str
    data: dict[str, Any]
    received_at: datetime


def _gzip_decompress(raw: bytes) -> str:
    """Decompress GZIP payload from HTX WebSocket."""
    try:
        return gzip.decompress(raw).decode("utf-8")
    except Exception as exc:
        raise ValueError(f"Failed to decompress WS payload: {exc}") from exc


def _parse_message(raw: str, received_at: datetime) -> Optional[WSMessage]:
    """Parse a decoded JSON message into WSMessage or None (pong/ping)."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("WS: non-JSON payload: %s", raw[:200])
        return None

    # HTX sends ping as {"ping": <timestamp>}
    if "ping" in payload:
        return None
    # HTX sends pong as {"pong": <timestamp>}
    if "pong" in payload:
        return None

    topic = payload.get("ch") or payload.get("topic") or ""
    if not topic:
        # Could be a subscription response
        if payload.get("status") == "ok":
            log.debug("WS: subscribed to %s", payload.get("subbed", "?"))
        return None

    return WSMessage(topic=topic, data=payload, received_at=received_at)


def _build_ping() -> str:
    return json.dumps({"pong": int(datetime.now(timezone.utc).timestamp() * 1000)})


async def _subscribe(ws, topics: list[str]) -> None:
    """Send subscription requests for all topics."""
    for topic in topics:
        sub_msg = {"sub": topic}
        await ws.send(json.dumps(sub_msg))
        log.debug("WS: subscribing to %s", topic)


async def run_ws(
    config: WSConfig,
    on_message: Callable[[WSMessage], Awaitable[None]],
    *,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Run the WebSocket loop with reconnection and backoff.

    ``on_message`` is called for every decoded market message.  Exceptions
    in ``on_message`` are logged but do not crash the loop.

    ``stop_event`` (if provided) terminates the loop gracefully.
    """
    stop = stop_event or asyncio.Event()
    attempt = 0

    while not stop.is_set():
        try:
            log.info("WS: connecting to %s (attempt %d)", config.ws_url, attempt + 1)
            async with connect(
                config.ws_url,
                ping_interval=config.ping_interval,
                ping_timeout=config.ping_interval * 2,
                max_size=2**20,  # 1 MiB
            ) as ws:
                await _subscribe(ws, config.topics)
                attempt = 0  # reset backoff on successful connect

                async for raw in ws:
                    if stop.is_set():
                        break

                    # HTX may send binary (GZIP) or text
                    if isinstance(raw, (bytes, bytearray)):
                        try:
                            decoded = _gzip_decompress(raw)
                        except ValueError:
                            log.warning("WS: decompress failed, skipping")
                            continue
                    else:
                        decoded = raw

                    received_at = datetime.now(timezone.utc)
                    msg = _parse_message(decoded, received_at)

                    # Respond to ping
                    if decoded and '"ping"' in decoded:
                        await ws.send(_build_ping())
                        continue

                    if msg is None:
                        continue

                    try:
                        await on_message(msg)
                    except Exception:
                        log.exception("WS: on_message callback error for %s", msg.topic)

        except asyncio.CancelledError:
            log.info("WS: cancelled, stopping")
            break
        except Exception as exc:
            attempt += 1
            delay = min(
                config.reconnect_base * (2 ** (attempt - 1)),
                config.reconnect_max,
            )
            log.warning("WS: disconnected (%s), reconnecting in %.1fs", exc, delay)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
                break  # stop signaled during backoff
            except asyncio.TimeoutError:
                continue

    log.info("WS: loop terminated")


def extract_kline(msg: WSMessage) -> Optional[dict[str, Any]]:
    """Extract a kline/candle from a WS message."""
    data = msg.data
    tick = data.get("tick")
    if not tick:
        return None
    topic = msg.topic
    # Determine timeframe from topic
    if "kline.1min" in topic:
        timeframe = "1min"
    elif "kline.60min" in topic:
        timeframe = "60min"
    else:
        return None

    return {
        "timeframe": timeframe,
        "open_time": tick.get("id"),
        "open": tick.get("open"),
        "high": tick.get("high"),
        "low": tick.get("low"),
        "close": tick.get("close"),
        "volume": tick.get("vol"),
        "count": tick.get("count"),
        "received_at": msg.received_at.isoformat(),
    }


def extract_bbo(msg: WSMessage) -> Optional[dict[str, Any]]:
    """Extract best bid/offer from a WS message."""
    tick = msg.data.get("tick")
    if not tick:
        return None
    return {
        "bid_price": tick.get("bidPrice"),
        "bid_qty": tick.get("bidSize"),
        "ask_price": tick.get("askPrice"),
        "ask_qty": tick.get("askSize"),
        "received_at": msg.received_at.isoformat(),
    }


def extract_mark_price(msg: WSMessage) -> Optional[dict[str, Any]]:
    """Extract mark price from a WS message."""
    tick = msg.data.get("tick")
    if not tick:
        return None
    return {
        "mark_price": tick.get("markPrice"),
        "index_price": tick.get("indexPrice"),
        "funding_rate": tick.get("fundingRate"),
        "next_funding_time": tick.get("nextFundingTime"),
        "received_at": msg.received_at.isoformat(),
    }


def extract_depth(msg: WSMessage) -> Optional[dict[str, Any]]:
    """Extract 20-level depth from a WS message."""
    tick = msg.data.get("tick")
    if not tick:
        return None
    return {
        "bids": tick.get("bids", [])[:20],
        "asks": tick.get("asks", [])[:20],
        "version": tick.get("version"),
        "received_at": msg.received_at.isoformat(),
    }
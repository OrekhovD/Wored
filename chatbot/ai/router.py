from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from openai import AsyncOpenAI

from ai.resilience import CircuitBreakerError, get_resilience_handler

log = logging.getLogger(__name__)

_clients: dict[str, AsyncOpenAI | None] = {}
_logged_client_messages: set[str] = set()


def _log_client_message_once(key: str, message: str) -> None:
    if key not in _logged_client_messages:
        log.warning(message)
        _logged_client_messages.add(key)


def get_client(tier: str) -> Optional[AsyncOpenAI]:
    from ai.models import MODELS

    if tier not in _clients:
        cfg = MODELS[tier]
        api_key = os.getenv(cfg.api_key_env, "").strip()
        if not api_key:
            _log_client_message_once(
                f"{tier}:missing_key",
                f"AI tier '{tier}' skipped: env var {cfg.api_key_env} is not set.",
            )
            _clients[tier] = None
            return None

        if tier == "minimax" and not api_key.startswith("nvapi-"):
            _log_client_message_once(
                "minimax:unsupported_key",
                "AI tier 'minimax' skipped: current router supports MiniMax only via NVIDIA NIM nvapi- keys.",
            )
            _clients[tier] = None
            return None

        _clients[tier] = AsyncOpenAI(
            api_key=api_key,
            base_url=cfg.endpoint,
            timeout=cfg.timeout,
            max_retries=0,
        )
    return _clients[tier]


def format_badge(tier: str, model_id: str, elapsed: float) -> str:
    badges = {
        "worker": "🤖 Р",
        "analyst": "🧠 А",
        "premium": "🎯 С",
        "minimax": "⚖️ О",
    }
    badge = badges.get(tier, "❓")
    return f"<b>{badge}</b> | <code>{model_id} · {elapsed:.1f}s</code>\n\n"


async def route_request(message: str, context: list[dict] | None = None) -> str:
    from ai.context_builder import build_comparison_context, build_context, build_deep_context
    from ai.dispatcher import classify
    from storage.redis_client import get_redis

    start = time.monotonic()
    intent = await classify(message)
    log.info("Classified intent: %s", intent)

    if intent["intent"] == "price":
        tickers = intent.get("tickers", ["btcusdt"]) or ["btcusdt"]
        ticker = tickers[0]

        redis_client = get_redis()
        data = await redis_client.get(f"ticker:{ticker}")
        elapsed = time.monotonic() - start

        if data:
            payload = json.loads(data)
            return (
                f"⚡ 💰 <b>{ticker.upper()}</b>: "
                f"${payload['price']} ({payload['change_pct']:+.2f}%) "
                f"`[Redis · {elapsed:.3f}s]`"
            )
        return f"❌ Нет данных по {ticker.upper()}"

    if intent["intent"] == "chat":
        return await _call_with_fallback("worker", "worker_chat", message, context)

    if intent["intent"] == "simple":
        return await _call_with_fallback("worker", "worker_quick", message, context)

    if intent["intent"] == "deep_analysis":
        tickers = intent.get("tickers", [])
        ticker = tickers[0] if tickers else "btcusdt"
        enriched = await build_deep_context(ticker)
        enriched += "\nПроведи глубокий пошаговый анализ с использованием chain-of-thought."
        full_context = (context or []) + [{"role": "system", "content": enriched}]
        return await _call_with_fallback("premium", "analyst_deep", message, full_context)

    if intent["intent"] == "comparison":
        tickers = intent.get("tickers", [])
        if len(tickers) >= 2:
            enriched = await build_comparison_context(tickers[0], tickers[1])
            full_context = (context or []) + [{"role": "system", "content": enriched}]
            return await _call_with_fallback("analyst", "analyst_compare", message, full_context)

    tickers = intent.get("tickers", [])
    ticker = tickers[0] if tickers else "btcusdt"
    enriched = await build_context(ticker)
    enriched += "\nОсновываясь только на этих свежих данных и индикаторах, проведи лаконичный анализ."
    full_context = (context or []) + [{"role": "system", "content": enriched}]
    return await _call_with_fallback("analyst", "analyst_single", message, full_context)


async def _call_with_fallback(
    preferred: str,
    prompt_skill: str,
    message: str,
    context: list[dict] | None = None,
) -> str:
    from ai.models import MODELS, expand_fallback_tiers
    from ai.prompts import get_prompt

    last_error: Exception | None = None

    for tier in expand_fallback_tiers(preferred):
        if tier not in MODELS:
            continue

        cfg = MODELS[tier]
        client = get_client(tier)
        if client is None:
            continue

        handler = get_resilience_handler(tier)
        if not await handler.circuit_breaker.can_execute():
            log.info("Circuit OPEN for '%s', skipping. Stats: %s", tier, handler.get_circuit_stats())
            continue

        async def _do_call():
            messages = [{"role": "system", "content": get_prompt(prompt_skill)}]
            if context:
                messages.extend(context)
            messages.append({"role": "user", "content": message})
            request_kwargs = {
                "model": cfg.model_id,
                "messages": messages,
                "max_tokens": cfg.max_tokens,
                "temperature": 0.7,
            }
            if cfg.tier == "worker" and "dashscope-intl.aliyuncs.com" in cfg.endpoint:
                request_kwargs["extra_body"] = {"enable_thinking": False}
            return await client.chat.completions.create(
                **request_kwargs,
            )

        try:
            started = time.monotonic()
            response = await handler.execute(_do_call)
            elapsed = time.monotonic() - started
            text = response.choices[0].message.content
            badge = format_badge(cfg.tier, cfg.model_id, elapsed)
            log.info("AI '%s' responded in %.2fs", tier, elapsed)
            return f"{badge}{text}"
        except CircuitBreakerError:
            log.warning("Circuit breaker rejected '%s'", tier)
            continue
        except Exception as exc:
            last_error = exc
            log.warning("AI tier '%s' (%s) failed: %s. Trying next...", tier, cfg.model_id, exc)
            continue

    error_detail = f" Последняя ошибка: {last_error}" if last_error else ""
    return f"❌ Все AI-модули сейчас недоступны.{error_detail} Попробуйте позже."

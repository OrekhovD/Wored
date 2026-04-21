import os
from openai import AsyncOpenAI
import logging
import asyncio
import json
import time

log = logging.getLogger(__name__)

# Singleton pool of clients
_clients = {}

def get_client(tier: str) -> AsyncOpenAI:
    from ai.models import MODELS
    if tier not in _clients:
        cfg = MODELS[tier]
        _clients[tier] = AsyncOpenAI(
            api_key=os.getenv(cfg.api_key_env, "dummy"),
            base_url=cfg.endpoint,
            timeout=cfg.timeout,
        )
    return _clients[tier]

def format_badge(tier: str, model_id: str, elapsed: float) -> str:
    badges = {"worker": "🤖 Р", "analyst": "🧠 А", "premium": "🎯 С"}
    badge = badges.get(tier, "❓")
    return f"<b>{badge}</b> | <code>{model_id} · {elapsed:.1f}s</code>\n\n"

async def route_request(message: str, context: list[dict] = None) -> str:
    from ai.dispatcher import classify
    from ai.context_builder import build_context, build_comparison_context
    from storage.redis_client import get_redis
    
    # 1. Classify intent
    start = time.monotonic()
    intent = await classify(message)
    log.info(f"Classified intent: {intent}")
    
    # 2. Direct fast path for price check
    if intent["intent"] == "price":
        tickers = intent.get("tickers", ["btcusdt"])
        if not tickers: tickers = ["btcusdt"]
        ticker = tickers[0]
        
        r = get_redis()
        data = await r.get(f"ticker:{ticker}")
        elapsed = time.monotonic() - start
        
        if data:
            t = json.loads(data)
            return f"⚡ 💰 <b>{ticker.upper()}</b>: ${t['price']} ({t['change_pct']:+.2f}%) `[Redis · {elapsed:.3f}s]`"
        return f"❌ Нет данных по {ticker.upper()}"
        
    # 3. Simple / chat questions go to worker
    if intent["intent"] == "chat":
        return await _call_with_fallback("worker", "worker_chat", message, context)
        
    if intent["intent"] == "simple":
        return await _call_with_fallback("worker", "worker_quick", message, context)
        
    # 4. Comparison goes to analyst with dual context
    if intent["intent"] == "comparison":
        tickers = intent.get("tickers", [])
        if len(tickers) >= 2:
            enriched = await build_comparison_context(tickers[0], tickers[1])
            full_context = (context or []) + [{"role": "system", "content": enriched}]
            return await _call_with_fallback("analyst", "analyst_compare", message, full_context)
            
    # 5. Fallback or Single analysis goes to analyst with single context
    tickers = intent.get("tickers", [])
    ticker = tickers[0] if tickers else "btcusdt" 
    
    enriched = await build_context(ticker)
    enriched += "\nОсновываясь исключительно на этих свежих данных рынка, проведи лаконичный анализ."
    full_context = (context or []) + [{"role": "system", "content": enriched}]
    return await _call_with_fallback("analyst", "analyst_single", message, full_context)

async def _call_with_fallback(preferred: str, prompt_skill: str, message: str, context=None) -> str:
    from ai.models import MODELS, FALLBACK_ORDER
    from ai.prompts import get_prompt
    
    order = [preferred] + [m for m in FALLBACK_ORDER if m != preferred]
    
    for tier in order:
        if tier not in MODELS:
            continue
            
        cfg = MODELS[tier]
        
        try:
            client = get_client(tier)
            messages = [{"role": "system", "content": get_prompt(prompt_skill)}]
            if context:
                messages.extend(context)
            messages.append({"role": "user", "content": message})
            
            start = time.monotonic()
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=cfg.model_id,
                    messages=messages,
                    max_tokens=cfg.max_tokens,
                    temperature=0.7,
                ),
                timeout=cfg.timeout,
            )
            elapsed = time.monotonic() - start
            text = response.choices[0].message.content
            
            badge = format_badge(tier, cfg.model_id, elapsed)
            return f"{badge}{text}"
            
        except Exception as e:
            log.warning(f"AI Tier '{tier}' ({cfg.model_id}) failed: {e}. Trying next fallback...")
            continue
            
    return "❌ Все AI-модули сейчас недоступны. Попробуйте позже."

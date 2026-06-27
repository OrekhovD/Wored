from __future__ import annotations

import asyncio
import json
import logging

log = logging.getLogger(__name__)

CLASSIFY_PROMPT = """Ты — диспетчер сообщений для крипто-бота.
Определи намерение пользователя и верни JSON со схемой:
{{"intent":"price|analysis|deep_analysis|comparison|simple|chat|trade_plan|trade_sim","tickers":["btcusdt"]}}.

Правила:
- "price": запрос текущей цены или изменения цены монеты
- "analysis": прогноз или анализ одной монеты
- "deep_analysis": подробный глубокий разбор, стратегия, пошаговый анализ
- "comparison": сравнение двух активов
- "simple": простой вопрос по криптовалюте и терминам
- "trade_plan": запрос торгового плана, прогноза с рекомендацией, "что делать с монетой", "стоит ли входить", "торговый план", "trade plan", "план сделки"
- "trade_sim": открытие/закрытие симулированной позиции, "фьючерсы", "кросс", "плечо", "лимитный", "лонг на 30$", "торгуй", "закрой позицию", "мои позиции", "история позиций"
- "chat": все остальное

Тикеры верни в нижнем регистре с суффиксом usdt. Если тикеров нет, верни [].

Запрос пользователя: {message}"""



def _classify_regex_fallback(message: str) -> dict | None:
    """Regex-based intent classification when all AI models fail."""
    import re
    msg = message.lower().strip()
    
    # trade_sim patterns
    sim_patterns = [
        r'(фьючерс|фьюч|кросс|плечо|лимитн|лонг|шорт|short|long)',
        r'на\s+\d+\s*\$',
        r'(закрой позицию|закрой сделку|close position)',
        r'(мои позиции|мои сделки|my positions)',
        r'(история позиций|история сделок|position history)',
        r'(торгуй|торгуй лонг|торгуй шорт)',
        r'(на \d+\$)',
    ]
    for pat in sim_patterns:
        if re.search(pat, msg):
            return {"intent": "trade_sim", "tickers": []}
    
    # trade_plan patterns
    plan_patterns = [
        r'(торговый план|trade plan|план сделки)',
        r'(что делать с |стоит ли входить|стоит ли покупать)',
        r'(куда войти|куда зайти|точка входа)',
    ]
    for pat in plan_patterns:
        if re.search(pat, msg):
            return {"intent": "trade_plan", "tickers": []}
    
    # price patterns
    if re.search(r'(цена|price|курс|сколько стоит|почём)', msg):
        return {"intent": "price", "tickers": []}
    
    # analysis patterns
    if re.search(r'(анализ|прогноз|разбор|analysis|forecast)', msg):
        return {"intent": "analysis", "tickers": []}
    
    # comparison
    if re.search(r'(сравни|compare|vs\.? |против)', msg):
        return {"intent": "comparison", "tickers": []}
    
    return None

async def classify(message: str) -> dict:
    from ai.models import MODELS, WORKER_MODEL_CHAIN
    from ai.router import get_client

    for tier in WORKER_MODEL_CHAIN:
        cfg = MODELS[tier]
        client = get_client(tier)
        if client is None:
            continue

        try:
            request_kwargs = {
                "model": cfg.model_id,
                "messages": [{"role": "user", "content": CLASSIFY_PROMPT.format(message=message)}],
                "max_tokens": 150,
                "temperature": 0.1,
            }
            if "dashscope-intl.aliyuncs.com" in cfg.endpoint:
                request_kwargs["extra_body"] = {"enable_thinking": False}
            response = await asyncio.wait_for(
                client.chat.completions.create(**request_kwargs),
                timeout=cfg.timeout,
            )
            content = (response.choices[0].message.content or "").strip()
            if content.startswith("```"):
                lines = content.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                content = "\n".join(lines).strip()

            data = json.loads(content)
            if "ticker" in data and isinstance(data["ticker"], str):
                data["tickers"] = [data["ticker"]]
            if "tickers" not in data:
                data["tickers"] = []
            return data
        except Exception as exc:
            log.warning("Classification failed on %s: %s. Trying next worker model...", cfg.model_id, exc)

    log.warning("Classification failed across worker chain. Using regex fallback.")
    regex_intent = _classify_regex_fallback(message)
    if regex_intent:
        log.info('Regex fallback classification: %s', regex_intent)
        return regex_intent
    log.warning('Regex fallback also failed. Falling back to default analysis intent.')
    return {"intent": "analysis", "tickers": ["btcusdt"]}

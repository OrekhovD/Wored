from __future__ import annotations

import asyncio
import json
import logging

log = logging.getLogger(__name__)

CLASSIFY_PROMPT = """Ты — диспетчер сообщений для крипто-бота.
Определи намерение пользователя и верни JSON со схемой:
{"intent":"price|analysis|deep_analysis|comparison|simple|chat","tickers":["btcusdt"]}.

Правила:
- "price": запрос текущей цены или изменения цены монеты
- "analysis": прогноз или анализ одной монеты
- "deep_analysis": подробный глубокий разбор, стратегия, пошаговый анализ
- "comparison": сравнение двух активов
- "simple": простой вопрос по криптовалюте и терминам
- "chat": все остальное

Тикеры верни в нижнем регистре с суффиксом usdt. Если тикеров нет, верни [].

Запрос пользователя: {message}"""


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

    log.warning("Classification failed across worker chain. Falling back to default analysis intent.")
    return {"intent": "analysis", "tickers": ["btcusdt"]}

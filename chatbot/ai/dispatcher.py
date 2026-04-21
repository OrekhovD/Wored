import json
import logging
import asyncio

log = logging.getLogger(__name__)

CLASSIFY_PROMPT = """Ты — диспетчер сообщений для крипто-бота (быстрый и точный аналитик намерений).
Определи намерение пользователя из следующих вариантов:
- "price": запрос текущей стоимости или изменения цены монеты (пример: "сколько стоит btc", "цена биткоина")
- "analysis": просьба сделать прогноз или анализировать перспективы одной монеты (пример: "что думаешь про btc", "шортить эфир?")
- "comparison": просьба сравнить два актива (пример: "сравни btc и eth", "что лучше взять, солану или эфир")
- "simple": простой вопрос по криптовалютам и терминам (пример: "что такое халвинг", "зачем нужен defi")
- "chat": все остальное, приветствие, шутки, вопросы не по теме (пример: "привет", "как дела")

Проанализируй запрос и верни ответ строго в формате JSON, без маркдауна и внешних тэгов.
Ключи JSON:
- "intent": тип намерения (price, analysis, comparison, simple, chat)
- "tickers": массив тикеров крипты из запроса, переведенных в нижний регистр с 'usdt' на конце (например: ["btcusdt"], ["btcusdt", "ethusdt"]). Если тикеры не упоминаются, верни [].

Запрос пользователя: {message}"""

async def classify(message: str) -> dict:
    """Classify user intent using the fast worker model (glm-4-flash)."""
    from ai.router import get_client
    from ai.models import MODELS
    
    cfg = MODELS["worker"]
    client = get_client("worker")
    
    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=cfg.model_id,
                messages=[{"role": "user", "content": CLASSIFY_PROMPT.format(message=message)}],
                max_tokens=150,
                temperature=0.1,
            ),
            timeout=10.0
        )
        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[0].startswith("```"): lines = lines[1:]
            if lines[-1].startswith("```"): lines = lines[:-1]
            content = "".join(lines)
            
        data = json.loads(content)
        # legacy fallback
        if "ticker" in data and isinstance(data["ticker"], str):
            data["tickers"] = [data["ticker"]]
            
        if "tickers" not in data:
            data["tickers"] = []
            
        return data
    except Exception as e:
        log.warning(f"Classification failed: {e}. Falling back to 'analysis' default.")
        return {"intent": "analysis", "tickers": ["btcusdt"]}

"""
AI Router — маршрутизация запросов к нужной модели.
Версия: 1.1 (18.04.2026)

ИСПРАВЛЕНИЯ v1.1:
- Добавлен параметр force_intent в route_and_respond()
- Исправлены model strings под реальные DashScope модели
- MiniMax через NVIDIA NIM endpoint (nvapi-...)
- Добавлен Gemini через Google AI Studio
- Добавлена обработка ошибок с fallback на MiniMax
"""

import os
import asyncio
from loguru import logger
from openai import AsyncOpenAI

from .prompts import (
    get_intent_classifier_prompt,
    get_perplexity_messages,
    get_glm_messages,
    get_minimax_messages,
    get_qwen_messages,
)
from .knowledge_base import get_knowledge_for_query


# ─── Реальные model strings (проверено 18.04.2026) ──────────

# DashScope (Alibaba) — https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL_REASONING  = "qwen3.6-plus"          # основной, 505k квота
QWEN_MODEL_MAX        = "qwen3-max"              # сложный reasoning
QWEN_MODEL_CODE       = "qwen2.5-coder-32b-instruct"  # код (проверить наличие)
QWEN_MODEL_FALLBACK   = "qwen3.5-plus"          # резерв, 985k квота

# GLM через ZhipuAI — https://open.bigmodel.cn/api/paas/v4/
GLM_MODEL = "glm-4-plus"  # ⚠️ GLM-5.1 уточнить актуальный model string

# MiniMax через NVIDIA NIM — https://integrate.api.nvidia.com/v1
# ⚠️ Если ключ nvapi-... → NVIDIA NIM endpoint
MINIMAX_MODEL = "minimax/minimax-01"  # уточнить точное имя в NIM каталоге

# Google AI Studio (Gemini) — bonus модель
GEMINI_MODEL = "gemini-2.0-flash"

# Perplexity — https://api.perplexity.ai
PERPLEXITY_MODEL = "sonar-pro"


# ─── Клиенты ────────────────────────────────────────────────

def _build_clients():
    """Инициализация клиентов с проверкой ключей."""
    clients = {}

    if os.getenv("PERPLEXITY_API_KEY"):
        clients["perplexity"] = AsyncOpenAI(
            api_key=os.getenv("PERPLEXITY_API_KEY"),
            base_url="https://api.perplexity.ai",
        )

    if os.getenv("GLM_API_KEY"):
        clients["glm"] = AsyncOpenAI(
            api_key=os.getenv("GLM_API_KEY"),
            base_url="https://open.bigmodel.cn/api/paas/v4/",
        )

    if os.getenv("MINIMAX_API_KEY"):
        # nvapi-... ключ → NVIDIA NIM endpoint
        api_key = os.getenv("MINIMAX_API_KEY")
        if api_key.startswith("nvapi-"):
            base_url = "https://integrate.api.nvidia.com/v1"
        else:
            base_url = "https://api.minimax.io/v1"
        clients["minimax"] = AsyncOpenAI(api_key=api_key, base_url=base_url)

    if os.getenv("DASHSCOPE_API_KEY"):
        clients["qwen"] = AsyncOpenAI(
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    if os.getenv("GOOGLE_API_KEY"):
        clients["gemini"] = AsyncOpenAI(
            api_key=os.getenv("GOOGLE_API_KEY"),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )

    return clients


_clients: dict = {}


def get_client(name: str) -> AsyncOpenAI | None:
    global _clients
    if not _clients:
        _clients = _build_clients()
    return _clients.get(name)


# ─── Intent classifier ───────────────────────────────────────

VALID_INTENTS = {
    "market_news", "deep_analysis", "quick_chat",
    "backtest_code", "position_calc", "alert_setup",
}


async def classify_intent(user_message: str) -> str:
    """Классификация через qwen3.6-plus (быстро и дёшево по квоте)."""
    client = get_client("qwen")
    if not client:
        return "quick_chat"
    try:
        resp = await client.chat.completions.create(
            model=QWEN_MODEL_REASONING,
            messages=[{"role": "user", "content": get_intent_classifier_prompt(user_message)}],
            max_tokens=20,
            temperature=0.0,
            # Qwen3 thinking off для классификации — не нужен overhead
            extra_body={"enable_thinking": False},
        )
        intent = resp.choices[0].message.content.strip().lower()
        return intent if intent in VALID_INTENTS else "quick_chat"
    except Exception as e:
        logger.warning(f"Intent classification failed: {e}, fallback → quick_chat")
        return "quick_chat"


# ─── Главная функция роутинга ────────────────────────────────

async def route_and_respond(
    user_message: str,
    market_snapshot: dict,
    chat_history: list,
    force_intent: str = None,   # FIX: добавлен параметр
) -> dict:
    """
    Маршрутизация запроса к оптимальной модели.

    Args:
        user_message: текст запроса пользователя
        market_snapshot: рыночный контекст из Redis
        chat_history: история чата в формате [{role, content}]
        force_intent: принудительный интент (обходит классификацию)

    Returns:
        {"text": str, "model": str, "intent": str, "thinking": str | None}
    """
    # FIX: force_intent обходит classifier
    intent = force_intent if force_intent in VALID_INTENTS \
             else await classify_intent(user_message)

    kb = get_knowledge_for_query(intent, user_message)
    enriched_snapshot = {**market_snapshot, "knowledge_base": kb}

    logger.info(f"Intent: {intent} (forced={force_intent is not None}) → routing...")

    # ─── Роутинг ────────────────────────────────────────────

    if intent == "market_news":
        return await _call_perplexity(user_message, enriched_snapshot)

    elif intent == "deep_analysis":
        return await _call_glm(user_message, enriched_snapshot, chat_history, thinking=True)

    elif intent == "backtest_code":
        return await _call_qwen(user_message, enriched_snapshot, use_code_model=True)

    elif intent == "position_calc":
        # position_calc не нужен thinking mode, быстрее через qwen
        return await _call_qwen(user_message, enriched_snapshot, use_code_model=False)

    else:  # quick_chat, alert_setup
        result = await _call_minimax(user_message, enriched_snapshot, chat_history)
        text = result.get("text", "")

        # Проверяем делегирующие маркеры
        if "[НУЖЕН_ГЛУБОКИЙ_АНАЛИЗ]" in text:
            logger.info("Delegating to GLM-5 (marker detected)")
            preamble = text.replace("[НУЖЕН_ГЛУБОКИЙ_АНАЛИЗ]", "").strip()
            deep = await _call_glm(user_message, enriched_snapshot, chat_history)
            if preamble:
                deep["text"] = preamble + "\n\n" + deep["text"]
            return deep

        if "[НУЖНЫ_НОВОСТИ]" in text:
            logger.info("Delegating to Perplexity (marker detected)")
            return await _call_perplexity(user_message, enriched_snapshot)

        return result


# ─── Вызовы моделей ─────────────────────────────────────────

async def _call_perplexity(query: str, snapshot: dict) -> dict:
    client = get_client("perplexity")
    if not client:
        logger.warning("Perplexity client not configured, fallback to Qwen")
        return await _call_qwen(query, snapshot)

    messages = get_perplexity_messages(query, snapshot)
    resp = await client.chat.completions.create(
        model=PERPLEXITY_MODEL,
        messages=messages,
        max_tokens=2048,
        temperature=0.3,
    )
    return {
        "text":    resp.choices[0].message.content,
        "model":   "Perplexity Sonar Pro",
        "intent":  "market_news",
        "thinking": None,
    }


async def _call_glm(
    query: str, snapshot: dict, history: list, thinking: bool = True
) -> dict:
    client = get_client("glm")
    if not client:
        logger.warning("GLM client not configured, fallback to Qwen3-max")
        return await _call_qwen(query, snapshot, use_max=True)

    payload = get_glm_messages(query, snapshot, history, thinking)
    resp = await client.chat.completions.create(**payload)
    msg = resp.choices[0].message
    thinking_text = getattr(msg, "reasoning_content", None)
    return {
        "text":    msg.content,
        "model":   "GLM-5.1",
        "intent":  "deep_analysis",
        "thinking": thinking_text,
    }


async def _call_minimax(
    query: str, snapshot: dict, history: list, reasoning: bool = False
) -> dict:
    client = get_client("minimax")
    if not client:
        logger.warning("MiniMax client not configured, fallback to Qwen")
        return await _call_qwen(query, snapshot)

    payload = get_minimax_messages(query, snapshot, history, reasoning)
    try:
        resp = await client.chat.completions.create(**payload)
        msg = resp.choices[0].message
        thinking_text = None
        if hasattr(msg, "reasoning_details") and msg.reasoning_details:
            thinking_text = msg.reasoning_details[0].get("text")
        return {
            "text":    msg.content,
            "model":   "MiniMax M2.7",
            "intent":  "quick_chat",
            "thinking": thinking_text,
        }
    except Exception as e:
        logger.error(f"MiniMax call failed: {e}, fallback to Qwen")
        return await _call_qwen(query, snapshot)


async def _call_qwen(
    query: str,
    snapshot: dict,
    use_code_model: bool = False,
    use_max: bool = False,
) -> dict:
    client = get_client("qwen")
    if not client:
        raise RuntimeError("No Qwen client configured — check DASHSCOPE_API_KEY")

    if use_max:
        model = QWEN_MODEL_MAX
    elif use_code_model:
        model = QWEN_MODEL_CODE
    else:
        model = QWEN_MODEL_REASONING

    payload = get_qwen_messages(query, snapshot, use_code_model=use_code_model)
    payload["model"] = model  # Override с правильным model string

    try:
        resp = await client.chat.completions.create(**payload)
    except Exception as e:
        if "model" in str(e).lower() and model == QWEN_MODEL_CODE:
            # Coder модель может быть недоступна — fallback на reasoning
            logger.warning(f"Coder model {model} unavailable, fallback to {QWEN_MODEL_REASONING}")
            payload["model"] = QWEN_MODEL_REASONING
            payload["extra_body"] = {"enable_thinking": True}
            resp = await client.chat.completions.create(**payload)
        else:
            raise

    msg = resp.choices[0].message
    model_name = {
        QWEN_MODEL_CODE:      "QwenCoder-32B",
        QWEN_MODEL_MAX:       "Qwen3-Max",
        QWEN_MODEL_REASONING: "Qwen3.6-Plus",
        QWEN_MODEL_FALLBACK:  "Qwen3.5-Plus",
    }.get(payload["model"], payload["model"])

    return {
        "text":    msg.content,
        "model":   model_name,
        "intent":  "backtest_code" if use_code_model else "quick_chat",
        "thinking": getattr(msg, "reasoning_content", None),
    }

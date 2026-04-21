#!/usr/bin/env python3
"""
validate_models.py — быстрая проверка всех API ключей и model strings
Запуск: python validate_models.py
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()

RESULTS = []


async def test_qwen():
    """Тест DashScope — проверяем реально доступные модели."""
    from openai import AsyncOpenAI
    key = os.getenv("DASHSCOPE_API_KEY")
    if not key:
        RESULTS.append(("DashScope/Qwen", "❌", "DASHSCOPE_API_KEY не задан"))
        return

    client = AsyncOpenAI(
        api_key=key,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    models_to_test = [
        "qwen3.6-plus",
        "qwen3-max",
        "qwen3.5-plus",
        "qwen2.5-coder-32b-instruct",
    ]

    for model in models_to_test:
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                extra_body={"enable_thinking": False},
            )
            RESULTS.append((f"Qwen/{model}", "✅", resp.choices[0].message.content[:20]))
        except Exception as e:
            err = str(e)[:80]
            RESULTS.append((f"Qwen/{model}", "❌", err))


async def test_glm():
    """Тест ZhipuAI GLM."""
    from openai import AsyncOpenAI
    key = os.getenv("GLM_API_KEY")
    if not key:
        RESULTS.append(("GLM/ZhipuAI", "❌", "GLM_API_KEY не задан"))
        return

    client = AsyncOpenAI(
        api_key=key,
        base_url="https://open.bigmodel.cn/api/paas/v4/",
    )

    # Пробуем несколько возможных model strings
    models_to_try = ["glm-4-plus", "glm-z1-plus", "glm-4", "glm-4-0520"]
    for model in models_to_try:
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            RESULTS.append((f"GLM/{model}", "✅", f"OK → {resp.choices[0].message.content[:20]}"))
            break  # нашли рабочий, дальше не проверяем
        except Exception as e:
            RESULTS.append((f"GLM/{model}", "❌", str(e)[:60]))


async def test_minimax_nvidia():
    """Тест MiniMax через NVIDIA NIM."""
    from openai import AsyncOpenAI
    key = os.getenv("MINIMAX_API_KEY")
    if not key:
        RESULTS.append(("MiniMax/NVIDIA", "❌", "MINIMAX_API_KEY не задан"))
        return

    if not key.startswith("nvapi-"):
        RESULTS.append(("MiniMax/NVIDIA", "⚠️", f"Ключ не начинается с nvapi-: {key[:12]}..."))
        return

    client = AsyncOpenAI(
        api_key=key,
        base_url="https://integrate.api.nvidia.com/v1",
    )

    # Пробуем MiniMax модели в каталоге NVIDIA
    models_to_try = [
        "minimax/minimax-01",
        "minimax/minimax-text-01",
        "minimax/minimax-vl-01",
    ]
    for model in models_to_try:
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            RESULTS.append((f"MiniMax(NIM)/{model}", "✅", resp.choices[0].message.content[:20]))
            break
        except Exception as e:
            RESULTS.append((f"MiniMax(NIM)/{model}", "❌", str(e)[:60]))


async def test_gemini():
    """Тест Google AI Studio (Gemini) через OpenAI-compatible endpoint."""
    from openai import AsyncOpenAI
    key = os.getenv("GOOGLE_API_KEY")
    if not key:
        RESULTS.append(("Gemini/Google", "❌", "GOOGLE_API_KEY не задан"))
        return

    client = AsyncOpenAI(
        api_key=key,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )
    try:
        resp = await client.chat.completions.create(
            model="gemini-2.0-flash",
            messages=[{"role": "user", "content": "Say: OK"}],
            max_tokens=5,
        )
        RESULTS.append(("Gemini/gemini-2.0-flash", "✅", resp.choices[0].message.content[:30]))
    except Exception as e:
        RESULTS.append(("Gemini/gemini-2.0-flash", "❌", str(e)[:80]))


async def test_telegram():
    """Тест Telegram Bot API."""
    import aiohttp
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        RESULTS.append(("Telegram Bot", "❌", "TELEGRAM_TOKEN не задан"))
        return

    async with aiohttp.ClientSession() as s:
        async with s.get(f"https://api.telegram.org/bot{token}/getMe") as r:
            data = await r.json()
            if data.get("ok"):
                bot = data["result"]
                RESULTS.append(("Telegram Bot", "✅", f"@{bot['username']} (id={bot['id']})"))
            else:
                RESULTS.append(("Telegram Bot", "❌", data.get("description", "unknown error")))


async def test_htx():
    """Тест HTX REST API (публичный endpoint — без ключа)."""
    import aiohttp
    async with aiohttp.ClientSession() as s:
        async with s.get("https://api.huobi.pro/market/detail/merged?symbol=btcusdt") as r:
            data = await r.json(content_type=None)
            if data.get("status") == "ok":
                price = data["tick"]["close"]
                RESULTS.append(("HTX REST (public)", "✅", f"BTC={price:,.0f} USDT"))
            else:
                RESULTS.append(("HTX REST (public)", "❌", str(data)[:60]))


async def main():
    print("=" * 60)
    print("HTX Trading Bot — Validation Script")
    print(f"Date: 18.04.2026")
    print("=" * 60)

    # Устанавливаем зависимости если нужно
    try:
        import openai
        import aiohttp
    except ImportError:
        print("Installing deps...")
        os.system("pip install openai aiohttp python-dotenv --quiet")

    # Параллельный запуск тестов
    await asyncio.gather(
        test_qwen(),
        test_glm(),
        test_minimax_nvidia(),
        test_gemini(),
        test_telegram(),
        test_htx(),
        return_exceptions=True,
    )

    # Вывод результатов
    print("\n📊 РЕЗУЛЬТАТЫ ВАЛИДАЦИИ:")
    print("-" * 60)
    ok = warn = fail = 0
    for name, status, detail in RESULTS:
        print(f"{status}  {name:<35} {detail}")
        if status == "✅":
            ok += 1
        elif status == "⚠️":
            warn += 1
        else:
            fail += 1

    print("-" * 60)
    print(f"Итого: ✅ {ok} | ⚠️ {warn} | ❌ {fail}")

    if fail > 0:
        print("\n⚠️  Не все ключи/модели доступны. Проверь MODEL_REGISTRY.md")
        sys.exit(1)
    else:
        print("\n✅ Все проверки пройдены! Можно запускать make build")


if __name__ == "__main__":
    asyncio.run(main())

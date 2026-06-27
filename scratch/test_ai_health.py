import os
import asyncio
import sys

# Add chatbot directory to path so we can import properly
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "chatbot"))

from ai.models import MODELS
from ai.router import get_client

async def test_all_models():
    print("=" * 60)
    print("AI SYSTEM HEALTH CHECK")
    print("=" * 60)
    
    # Pre-load environment from .env if running from host
    env_path = ".env"
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

    for name, cfg in MODELS.items():
        print(f"\n[Tier: {cfg.tier}] Testing: {name} ({cfg.model_id})...")
        client = get_client(name)
        if client is None:
            print(f"  [-] Skipped: No valid client (missing key or validation failed)")
            continue
            
        try:
            messages = [{"role": "user", "content": "Return exactly OK"}]
            request_kwargs = {
                "model": cfg.model_id,
                "messages": messages,
                "max_tokens": 8,
                "temperature": 0
            }
            if cfg.tier == "worker" and "dashscope-intl.aliyuncs.com" in cfg.endpoint:
                request_kwargs["extra_body"] = {"enable_thinking": False}
                
            start = asyncio.get_event_loop().time()
            response = await client.chat.completions.create(**request_kwargs)
            elapsed = asyncio.get_event_loop().time() - start
            text = response.choices[0].message.content.strip()
            print(f"  [+] SUCCESS (Time: {elapsed:.2f}s)")
            print(f"      Response: {text}")
        except Exception as e:
            print(f"  [-] FAILED: {str(e)[:150]}")

if __name__ == "__main__":
    asyncio.run(test_all_models())

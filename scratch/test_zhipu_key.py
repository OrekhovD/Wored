"""
Test Ollama API key through AI Gateway.
Reads AI_GATEWAY_BASE_URL from .env and tests available models.
"""
import requests
import json
import os

API_KEY = "6bab7f40e8ca4da0b9abc72a66fa6c12.tCjESJRFOnDALtZ3U8W88Dkp"

# Read gateway URL from .env — auto-detect WSL vs Windows path
env_path = "/mnt/d/WORED/.env" if os.path.exists("/mnt/d/WORED/.env") else "d:/WORED/.env"
env_vars = {}
try:
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()
except FileNotFoundError:
    print("ERROR: .env not found!")
    exit(1)

BASE_URL = env_vars.get("AI_GATEWAY_BASE_URL", "")
if not BASE_URL:
    print("ERROR: AI_GATEWAY_BASE_URL not found in .env!")
    exit(1)

# Ensure no trailing slash
BASE_URL = BASE_URL.rstrip("/")
print(f"Gateway URL: {BASE_URL}")
print(f"Key prefix: {API_KEY[:12]}...")
print()

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# === 1. List models ===
print("=" * 60)
print("1. GET /models — listing available models...")
print("=" * 60)

try:
    r = requests.get(f"{BASE_URL}/models", headers=headers, timeout=15)
    print(f"HTTP Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        models = data.get("data", [])
        print(f"\nTotal models available: {len(models)}\n")
        for m in models:
            mid = m.get("id", "?")
            owned = m.get("owned_by", "?")
            print(f"  ✓ {mid}  (owned_by: {owned})")
    else:
        print(f"Response: {r.text[:1000]}")
except Exception as e:
    print(f"Connection error: {e}")

# === 2. Test chat completions with common Ollama models ===
test_models = [
    "qwen3:8b",
    "qwen3:4b",
    "qwen3:1.7b",
    "llama3.1:8b",
    "gemma3:4b",
    "deepseek-r1:8b",
    "phi4:latest",
    "mistral:latest",
    "qwen2.5:7b",
    "glm4:9b",
]

print(f"\n{'=' * 60}")
print("2. Testing chat completions with common Ollama models...")
print("=" * 60)

for model in test_models:
    print(f"\nTesting {model}...")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Return OK"}],
        "max_tokens": 10,
        "temperature": 0
    }
    try:
        r = requests.post(f"{BASE_URL}/chat/completions", headers=headers, json=payload, timeout=30)
        if r.status_code == 200:
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            print(f"  [+] OK — Response: {content.strip()[:80]}")
            print(f"      Usage: prompt={usage.get('prompt_tokens', '?')}, completion={usage.get('completion_tokens', '?')}")
        else:
            err = r.text[:200]
            print(f"  [-] HTTP {r.status_code} — {err}")
    except requests.exceptions.Timeout:
        print(f"  [!] Timeout (30s)")
    except Exception as e:
        print(f"  [!] Error: {e}")

print(f"\n{'=' * 60}")
print("Done!")
print("=" * 60)

import requests

key = "sk-d459d8f6953699e7-4e5509-375fb584"
endpoints = [
    ("Together AI", "https://api.together.xyz/v1/models"),
    ("OpenRouter", "https://openrouter.ai/api/v1/models"),
    ("SiliconFlow", "https://api.siliconflow.cn/v1/models"),
    ("Groq", "https://api.groq.com/openai/v1/models"),
    ("Mistral", "https://api.mistral.ai/v1/models"),
    ("DeepSeek", "https://api.deepseek.com/v1/models"),
    ("Alibaba DashScope", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models"),
]

print(f"Probing new key: {key[:10]}...\n")

for name, url in endpoints:
    try:
        response = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=10)
        if response.status_code == 200:
            models = response.json().get("data", [])
            print(f"[+] {name}: OK")
            print(f"    Available models: {len(models)}")
            if isinstance(models, list) and len(models) > 0:
                # Show top 5 models
                for m in models[:5]:
                    print(f"     - {m.get('id')}")
                if len(models) > 5: print(f"     ... and {len(models)-5} more")
            
            # Try to get limits if possible (some providers support /v1/usage or similar)
        elif response.status_code == 401:
            pass # Unauthorized, normal for wrong provider
        else:
            print(f"[-] {name}: HTTP {response.status_code}")
    except Exception as e:
        print(f"[!] {name}: Error {str(e)}")

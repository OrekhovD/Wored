import requests

key = "sk-3747d92a995143508df8f1372b162b31"
endpoints = [
    ("Moonshot (Intl)", "https://api.moonshot.ai/v1/models"),
    ("OpenRouter", "https://openrouter.ai/api/v1/models"),
    ("Groq", "https://api.groq.com/openai/v1/models"),
    ("Mistral", "https://api.mistral.ai/v1/models"),
    ("Novita AI", "https://api.novita.ai/v1/models"),
]

for name, url in endpoints:
    try:
        response = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=10)
        if response.status_code == 200:
            models = response.json().get("data", [])
            print(f"\n[+] Provider: {name}")
            print(f"Models available ({len(models)}):")
            # For OpenRouter the structure might be different
            if isinstance(models, list):
                for m in models[:10]: # First 10
                    print(f" - {m.get('id')}")
                if len(models) > 10: print(f" ... and {len(models)-10} more")
        else:
            print(f"[-] {name}: HTTP {response.status_code}")
    except Exception as e:
        print(f"[!] {name}: Error {str(e)}")

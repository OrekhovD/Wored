import requests

key = "sk-3747d92a995143508df8f1372b162b31"
endpoints = [
    ("DeepSeek", "https://api.deepseek.com/v1/models"),
    ("Moonshot/Kimi", "https://api.moonshot.cn/v1/models"),
    ("SiliconFlow", "https://api.siliconflow.cn/v1/models"),
    ("Together AI", "https://api.together.xyz/v1/models"),
]

for name, url in endpoints:
    try:
        response = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=10)
        if response.status_code == 200:
            models = response.json().get("data", [])
            print(f"\n[+] Provider: {name}")
            print(f"Models available ({len(models)}):")
            for m in models:
                print(f" - {m.get('id')}")
        else:
            print(f"[-] {name}: HTTP {response.status_code}")
    except Exception as e:
        print(f"[!] {name}: Error {str(e)}")

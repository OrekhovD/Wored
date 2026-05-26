import requests

key = "sk-d459d8f6953699e7-4e5509-375fb584"
endpoints = [
    ("Kiro AI", "https://api.kiro.ai/v1/models"),
    ("OmniRoute Cloud", "https://api.omni-route.com/v1/models"),
    ("Kiro Dev", "https://api.kiro.dev/v1/models"),
]

print(f"Probing Kiro/OmniRoute key: {key[:10]}...\n")

for name, url in endpoints:
    try:
        response = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=10)
        if response.status_code == 200:
            models = response.json().get("data", [])
            print(f"[+] {name}: OK")
            print(f"    Available models: {len(models)}")
            for m in models[:10]:
                print(f"     - {m.get('id')}")
            if len(models) > 10: print(f"     ... and {len(models)-10} more")
        else:
            print(f"[-] {name}: HTTP {response.status_code}")
    except Exception as e:
        print(f"[!] {name}: Error {str(e)}")

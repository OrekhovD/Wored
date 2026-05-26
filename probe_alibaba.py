import requests

key = "sk-3f1da7af37cb41d3bf175068411e3a11"
endpoints = [
    ("Alibaba DashScope (Intl)", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models"),
    ("Alibaba DashScope (V1)", "https://dashscope-intl.aliyuncs.com/v1/services/aigc/text-generation/generation"),
]

print(f"Testing key against Alibaba Cloud (Singapore) endpoints...\n")

# Test 1: Compatible Mode (OpenAI-like)
try:
    url = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models"
    response = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=10)
    if response.status_code == 200:
        models = response.json().get("data", [])
        print(f"[+] Alibaba DashScope (Compatible Mode): OK")
        print(f"Models available ({len(models)}):")
        for m in models:
            print(f" - {m.get('id')}")
    else:
        print(f"[-] Compatible Mode: HTTP {response.status_code} - {response.text}")
except Exception as e:
    print(f"[!] Compatible Mode: Error {str(e)}")

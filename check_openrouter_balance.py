import requests

key = "sk-d459d8f6953699e7-4e5509-375fb584"
url = "https://openrouter.ai/api/v1/auth/key"

headers = {
    "Authorization": f"Bearer {key}",
    "HTTP-Referer": "https://wored.ai", # Site URL (optional)
    "X-Title": "WORED Hermes", # Site Title (optional)
}

try:
    response = requests.get(url, headers=headers, timeout=10)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json().get("data", {})
        print("\n[+] OpenRouter Key Info:")
        print(f"    Label: {data.get('label')}")
        print(f"    Usage: ${data.get('usage'):.4f}")
        print(f"    Limit: {data.get('limit') if data.get('limit') else 'Unlimited'}")
        print(f"    Is Free Tier: {data.get('is_free_tier')}")
    else:
        print(f"[-] Error: {response.text}")
except Exception as e:
    print(f"[!] Error: {str(e)}")

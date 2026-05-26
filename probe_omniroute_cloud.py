import requests

key = "sk-d459d8f6953699e7-4e5509-375fb584"
url = "https://cloud.omniroute.online/v1/models"

print(f"Probing OmniRoute Cloud: {url}")

try:
    response = requests.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=10)
    if response.status_code == 200:
        models = response.json().get("data", [])
        print(f"[+] OmniRoute Cloud: OK")
        print(f"    Available models: {len(models)}")
        # Filter for interesting models like Kiro, Grok, etc.
        interesting = [m.get("id") for m in models if "kiro" in m.get("id").lower() or "grok" in m.get("id").lower() or "thinking" in m.get("id").lower()]
        print(f"    Special models found ({len(interesting)}):")
        for m in interesting[:10]:
            print(f"     - {m}")
            
        # Try to check balance via /auth/key if it exists on cloud
        balance_url = "https://cloud.omniroute.online/v1/auth/key"
        b_res = requests.get(balance_url, headers={"Authorization": f"Bearer {key}"}, timeout=10)
        if b_res.status_code == 200:
            data = b_res.json().get("data", {})
            print(f"\n[+] Key Info:")
            print(f"    Usage: ${data.get('usage'):.4f}")
            print(f"    Limit: {data.get('limit') if data.get('limit') else 'Unlimited'}")
    else:
        print(f"[-] Error: HTTP {response.status_code} - {response.text}")
except Exception as e:
    print(f"[!] Error: {str(e)}")

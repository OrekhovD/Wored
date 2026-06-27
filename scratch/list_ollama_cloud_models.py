"""
Fetch all models from Ollama Cloud.
"""
import requests
import json

API_KEY = "6bab7f40e8ca4da0b9abc72a66fa6c12.tCjESJRFOnDALtZ3U8W88Dkp"
headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

try:
    r = requests.get("https://api.ollama.com/v1/models", headers=headers, timeout=10)
    if r.status_code == 200:
        models = r.json().get("data", [])
        print(f"Total models: {len(models)}")
        for m in models:
            print(f" - {m.get('id')}")
    else:
        print(r.text)
except Exception as e:
    print(e)

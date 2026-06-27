import time
import requests

url = "http://172.29.144.1:8081/v1/chat/completions"
headers = {
    "Authorization": "Bearer sk-ab9c0b7d84ccff33-3c7ca7-cd827e8b",
    "Content-Type": "application/json"
}
payload = {
    "model": "hermes/active-slot",
    "messages": [{"role": "user", "content": "Привет! Ответь одним словом."}],
    "max_tokens": 10
}

start = time.time()
try:
    print("Sending request to HER Gateway...")
    r = requests.post(url, headers=headers, json=payload, timeout=25)
    latency = time.time() - start
    print(f"HTTP Status: {r.status_code}")
    print(f"Latency: {latency:.2f} seconds")
    if r.status_code == 200:
        print("Response:")
        print(r.json()["choices"][0]["message"]["content"])
    else:
        print(f"Error: {r.text}")
except Exception as e:
    print(f"Connection error: {e}")

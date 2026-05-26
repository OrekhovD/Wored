import os
import requests
import json

keys = [
    ("Minimax 2.7", "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"),
    ("DeepSeek v4 Pro", "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"),
    ("GLM 4.7", "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"),
    ("Minimax M2.7", "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"),
    ("DeepSeek 3.2", "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"),
    ("Kimi K2 TH", "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"),
    ("Qwen 3 Coder", "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"),
    ("Kimi K2", "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"),
]

url = "https://integrate.api.nvidia.com/v1/chat/completions"
model = "meta/llama-3.1-70b-instruct"

print(f"{'Label':<20} | {'Status':<10} | {'Details'}")
print("-" * 50)

for label, key in keys:
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code == 200:
            print(f"{label:<20} | \033[92mOK\033[0m         | Success")
        else:
            print(f"{label:<20} | \033[91mFAIL\033[0m       | HTTP {response.status_code}: {response.text[:50]}...")
    except Exception as e:
        print(f"{label:<20} | \033[91mERROR\033[0m      | {str(e)}")

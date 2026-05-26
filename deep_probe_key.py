import requests

key = "sk-d459d8f6953699e7-4e5509-375fb584"
providers = [
    ("SiliconFlow", "https://api.siliconflow.cn/v1/chat/completions", "vendor/llama-3.1-8b-instruct"),
    ("Together", "https://api.together.xyz/v1/chat/completions", "meta-llama/Llama-3-8b-chat-hf"),
    ("DeepInfra", "https://api.deepinfra.com/v1/openai/chat/completions", "meta-llama/Llama-3-8b-instruct"),
    ("Perplexity", "https://api.perplexity.ai/chat/completions", "llama-3-8b-instruct"),
    ("Groq", "https://api.groq.com/openai/v1/chat/completions", "llama3-8b-8192"),
    ("Mistral", "https://api.mistral.ai/v1/chat/completions", "mistral-tiny"),
    ("Novita AI", "https://api.novita.ai/v1/chat/completions", "meta-llama/llama-3-8b-instruct"),
    ("Gitee AI", "https://ai.gitee.com/v1/chat/completions", "llama-3-8b-instruct"),
]

payload = {
    "model": "",
    "messages": [{"role": "user", "content": "ping"}],
    "max_tokens": 1
}

print(f"Deep probing key: {key[:10]}...\n")

for name, url, model in providers:
    p = payload.copy()
    p["model"] = model
    try:
        response = requests.post(url, headers={"Authorization": f"Bearer {key}"}, json=p, timeout=10)
        if response.status_code == 200:
            print(f"[!] SUCCESS: {name} accepted the key!")
            print(f"    Model used: {model}")
            exit(0)
        else:
            print(f"[-] {name}: HTTP {response.status_code}")
    except Exception as e:
        print(f"[!] {name}: Error {str(e)}")

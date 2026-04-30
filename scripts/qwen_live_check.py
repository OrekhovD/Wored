from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_SMOKE_MODELS = [
    "qwen3.6-flash",
    "qwen3.5-flash",
    "qwen-flash",
    "qwen3.6-plus",
    "qwen3.5-plus",
    "qwen3.6-max-preview",
    "qwq-plus",
    "qwen3.6-35b-a3b",
    "qwen3.6-27b",
]
EXCLUDED_MODEL_MARKERS = (
    "image",
    "vl",
    "tts",
    "asr",
    "translate",
    "livetranslate",
    "omni",
    "character",
    "ocr",
    "mt",
    "realtime",
    "audio",
    "wan",
    "embedding",
    "qvq",
)


def request_json(path: str, api_key: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any], dict[str, str]]:
    body = None
    method = "GET"
    headers = {"Authorization": f"Bearer {api_key}"}
    if payload is not None:
        method = "POST"
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(f"{BASE_URL}{path}", data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw_headers = {key.lower(): value for key, value in response.headers.items()}
            raw_body = response.read().decode("utf-8")
            parsed = json.loads(raw_body) if raw_body else {}
            return response.status, parsed, raw_headers
    except urllib.error.HTTPError as exc:
        raw_headers = {key.lower(): value for key, value in exc.headers.items()}
        raw_body = exc.read().decode("utf-8")
        parsed = json.loads(raw_body) if raw_body else {"error": {"message": ""}}
        return exc.code, parsed, raw_headers


def filter_text_models(model_ids: list[str]) -> list[str]:
    filtered: list[str] = []
    for model_id in model_ids:
        normalized = model_id.lower()
        if not (normalized.startswith("qwen") or normalized.startswith("qwq")):
            continue
        if any(marker in normalized for marker in EXCLUDED_MODEL_MARKERS):
            continue
        filtered.append(model_id)
    return filtered


def run_smoke(api_key: str, model_id: str) -> dict[str, Any]:
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": "Reply with plain text only and keep it under 12 words."},
            {"role": "user", "content": "Health check for forecast pipeline."},
        ],
        "max_tokens": 40,
        "temperature": 0.1,
    }
    status, body, headers = request_json("/chat/completions", api_key, payload)
    message = (((body.get("choices") or [{}])[0]).get("message") or {}) if isinstance(body, dict) else {}
    usage = body.get("usage") if isinstance(body, dict) else None
    rate_headers = {
        key: value
        for key, value in headers.items()
        if key.startswith("x-ratelimit") or key.startswith("ratelimit")
    }
    return {
        "model": model_id,
        "status": status,
        "response_model": body.get("model") if isinstance(body, dict) else None,
        "content": message.get("content"),
        "reasoning_content": message.get("reasoning_content"),
        "usage": usage,
        "rate_headers": rate_headers,
        "error": body.get("error") if isinstance(body, dict) else body,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="List live DashScope Qwen models and run smoke checks.")
    parser.add_argument("--api-key", default=os.getenv("DASHSCOPE_API_KEY", "").strip())
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    parser.add_argument("--smoke-model", action="append", dest="smoke_models", default=[])
    args = parser.parse_args()

    if not args.api_key:
        print("DASHSCOPE_API_KEY is not set", file=sys.stderr)
        return 2

    status, body, _headers = request_json("/models", args.api_key)
    model_ids = [item["id"] for item in body.get("data", [])] if isinstance(body, dict) else []
    text_models = filter_text_models(model_ids)
    smoke_models = args.smoke_models or DEFAULT_SMOKE_MODELS
    smoke_results = [run_smoke(args.api_key, model_id) for model_id in smoke_models]

    payload = {
        "models_status": status,
        "text_model_count": len(text_models),
        "text_models": text_models,
        "smoke_results": smoke_results,
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 0

    print(f"Live text models: {len(text_models)}")
    for model_id in text_models:
        print(f"- {model_id}")

    print("\nSmoke results:")
    for result in smoke_results:
        detail = result["content"] or result["reasoning_content"] or result["error"] or ""
        print(f"- {result['model']}: {result['status']} {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

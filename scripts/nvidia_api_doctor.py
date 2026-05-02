#!/usr/bin/env python3

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional

# --- CONFIG ---
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "minimaxai/minimax-m2.7"

# --- UTILS ---
def load_env_vars(env_file: str) -> Dict[str, str]:
    """Load NVIDIA_* vars from .env file, mask secrets."""
    env_vars = {}
    try:
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    if key.startswith("NVIDIA_API_KEY"):
                        # Store key name only, never value
                        env_vars[key] = "[REDACTED]"
                    else:
                        env_vars[key] = val.strip()
    except FileNotFoundError:
        pass
    return env_vars

def get_active_key(env_vars: Dict[str, str]) -> Optional[str]:
    """Get active key value safely — returns None if missing or invalid."""
    active_idx = env_vars.get("NVIDIA_API_KEY_ACTIVE", "").strip()
    if not active_idx.isdigit():
        return None
    key_name = f"NVIDIA_API_KEY_{active_idx}"
    return env_vars.get(key_name)

def check_key_presence(env_vars: Dict[str, str]) -> Dict[str, Any]:
    """Check presence of keys and active key."""
    single_key = env_vars.get("NVIDIA_API_KEY") == "[REDACTED]"
    pool_keys = [k for k in env_vars.keys() if k.startswith("NVIDIA_API_KEY_") and k != "NVIDIA_API_KEY_ACTIVE"]
    active_idx = env_vars.get("NVIDIA_API_KEY_ACTIVE", "").strip()
    active_present = False
    if active_idx.isdigit() and f"NVIDIA_API_KEY_{active_idx}" in env_vars:
        active_present = True

    return {
        "ok": True,
        "mode": "presence",
        "keys": {
            "NVIDIA_API_KEY": single_key,
            **{k: True for k in pool_keys},
            "NVIDIA_API_KEY_ACTIVE": active_idx if active_idx else None,
        },
        "active_key_present": active_present,
        "secrets_printed": False,
    }

def test_models_endpoint(base_url: str, timeout: int, env_vars: Dict[str, str]) -> Dict[str, Any]:
    """Test /v1/models endpoint."""
    try:
        import httpx
    except ImportError:
        return {
            "ok": False,
            "mode": "models",
            "error": "httpx not installed",
            "secrets_printed": False,
        }

    url = f"{base_url}/models"
    headers = {"Content-Type": "application/json"}
    api_key = get_active_key(env_vars)
    if not api_key:
        return {
            "ok": False,
            "mode": "models",
            "error": "NVIDIA API key missing",
            "secrets_printed": False,
        }
    headers["Authorization"] = f"Bearer {api_key}"

    try:
        start = time.time()
        response = httpx.get(url, headers=headers, timeout=timeout)
        latency_ms = int((time.time() - start) * 1000)
        if response.status_code == 200:
            data = response.json()
            model_count = len(data.get("data", []))
            target_model = os.getenv("NVIDIA_MODEL", DEFAULT_MODEL)
            target_found = any(m.get("id") == target_model for m in data.get("data", []))
            return {
                "ok": True,
                "mode": "models",
                "http_status": response.status_code,
                "model_count": model_count,
                "target_model": target_model,
                "target_model_found": target_found,
                "latency_ms": latency_ms,
                "secrets_printed": False,
            }
        else:
            return {
                "ok": False,
                "mode": "models",
                "http_status": response.status_code,
                "error": f"HTTP {response.status_code}",
                "secrets_printed": False,
            }
    except Exception as exc:
        return {
            "ok": False,
            "mode": "models",
            "error": str(exc),
            "secrets_printed": False,
        }

def test_chat_endpoint(base_url: str, model: str, timeout: int, env_vars: Dict[str, str]) -> Dict[str, Any]:
    """Test /v1/chat/completions endpoint."""
    try:
        import httpx
    except ImportError:
        return {
            "ok": False,
            "mode": "chat",
            "error": "httpx not installed",
            "secrets_printed": False,
        }

    url = f"{base_url}/chat/completions"
    headers = {"Content-Type": "application/json"}
    api_key = get_active_key(env_vars)
    if not api_key:
        return {
            "ok": False,
            "mode": "chat",
            "error": "NVIDIA API key missing",
            "key_present": False,
            "secrets_printed": False,
        }
    headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: NVIDIA_API_OK"}],
        "temperature": 0.2,
        "max_tokens": 32,
        "stream": False,
    }

    try:
        start = time.time()
        response = httpx.post(url, headers=headers, json=payload, timeout=timeout)
        latency_ms = int((time.time() - start) * 1000)
        if response.status_code == 200:
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            preview = content[:32]
            return {
                "ok": True,
                "mode": "chat",
                "provider": "nvidia",
                "base_url": base_url,
                "model": model,
                "key_present": True,
                "http_status": response.status_code,
                "latency_ms": latency_ms,
                "content_preview": preview,
                "secrets_printed": False,
            }
        elif response.status_code in [401, 403]:
            return {
                "ok": False,
                "mode": "chat",
                "http_status": response.status_code,
                "error": "Unauthorized or invalid NVIDIA API key",
                "secrets_printed": False,
            }
        elif response.status_code == 404:
            return {
                "ok": False,
                "mode": "chat",
                "http_status": response.status_code,
                "error": "Model not available for this account or endpoint",
                "model": model,
                "secrets_printed": False,
            }
        else:
            return {
                "ok": False,
                "mode": "chat",
                "http_status": response.status_code,
                "error": f"HTTP {response.status_code}",
                "secrets_printed": False,
            }
    except Exception as exc:
        return {
            "ok": False,
            "mode": "chat",
            "error": str(exc),
            "secrets_printed": False,
        }

# --- MAIN ---
def main():
    parser = argparse.ArgumentParser(description="NVIDIA API Doctor")
    parser.add_argument("--env-file", default=os.path.expanduser("~/.hermes/.env"), help="Path to .env file")
    parser.add_argument("--base-url", default=NVIDIA_BASE_URL, help="NVIDIA API base URL")
    parser.add_argument("--model", default=os.getenv("NVIDIA_MODEL", DEFAULT_MODEL), help="Model ID")
    parser.add_argument("--mode", choices=["presence", "models", "chat"], default="presence", help="Mode")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    env_vars = load_env_vars(args.env_file)

    if args.mode == "presence":
        result = check_key_presence(env_vars)
    elif args.mode == "models":
        result = test_models_endpoint(args.base_url, args.timeout, env_vars)
    elif args.mode == "chat":
        result = test_chat_endpoint(args.base_url, args.model, args.timeout, env_vars)
    else:
        result = {"ok": False, "error": "Unknown mode", "secrets_printed": False}

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        # Human-readable output (not required by spec, but helpful for debug)
        print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()

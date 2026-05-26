#!/usr/bin/env python3
"""Safe NVIDIA NIM diagnostics for WORED/Hermes.

The script can read many keys and try them in order without printing any key
material. Supported sources:

- process environment: NVIDIA_API_KEY and NVIDIA_API_KEY_N
- ~/.hermes/.env style files
- ~/.hermes/secrets/nvidia_keys.txt with one key per line

Output is JSON-safe for logs and Telegram snippets.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "minimaxai/minimax-m2.7"
DEFAULT_ENV_FILE = Path.home() / ".hermes" / ".env"
DEFAULT_KEYS_FILE = Path.home() / ".hermes" / "secrets" / "nvidia_keys.txt"
KEY_TOKEN_RE = re.compile(r"nvapi-[A-Za-z0-9_.-]+")
NUMBERED_KEY_RE = re.compile(r"^NVIDIA_API_KEY_(\d+)$")
SECRET_PATTERNS = [
    re.compile(r"nvapi-[A-Za-z0-9_.-]+"),
    re.compile(r"Bearer\s+[A-Za-z0-9_.-]+", re.IGNORECASE),
]


@dataclass(frozen=True)
class KeyCandidate:
    source: str
    name: str
    value: str


@dataclass(frozen=True)
class SimpleResponse:
    status_code: int
    payload: Any

    def json(self) -> Any:
        return self.payload


def sanitize(value: object) -> str:
    text = str(value)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def read_text_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except FileNotFoundError:
        return []


def strip_dotenv_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def parse_dotenv(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw_line in read_text_lines(path):
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        data[key] = strip_dotenv_value(value)
    return data


def extract_key_tokens(path: Path) -> list[str]:
    keys: list[str] = []
    for raw_line in read_text_lines(path):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        keys.extend(match.group(0).strip() for match in KEY_TOKEN_RE.finditer(line))
    return keys


def add_candidate(
    candidates: list[KeyCandidate],
    seen: set[str],
    source: str,
    name: str,
    value: str | None,
) -> None:
    key = (value or "").strip()
    if not key or not KEY_TOKEN_RE.fullmatch(key) or key in seen:
        return
    seen.add(key)
    candidates.append(KeyCandidate(source=source, name=name, value=key))


def numbered_key_sort(item: tuple[str, str]) -> tuple[int, str]:
    match = NUMBERED_KEY_RE.match(item[0])
    if match:
        return (int(match.group(1)), item[0])
    return (10**9, item[0])


def load_key_candidates(env_file: Path, keys_file: Path) -> tuple[list[KeyCandidate], dict[str, Any]]:
    candidates: list[KeyCandidate] = []
    seen: set[str] = set()
    env_data = parse_dotenv(env_file)

    add_candidate(candidates, seen, "process_env", "NVIDIA_API_KEY", os.getenv("NVIDIA_API_KEY"))
    for name, value in sorted(os.environ.items(), key=numbered_key_sort):
        if NUMBERED_KEY_RE.match(name):
            add_candidate(candidates, seen, "process_env", name, value)

    add_candidate(candidates, seen, "env_file", "NVIDIA_API_KEY", env_data.get("NVIDIA_API_KEY"))
    numbered_env = [(k, v) for k, v in env_data.items() if NUMBERED_KEY_RE.match(k)]
    for name, value in sorted(numbered_env, key=numbered_key_sort):
        add_candidate(candidates, seen, "env_file", name, value)

    file_keys = extract_key_tokens(keys_file)
    for index, key in enumerate(file_keys, start=1):
        add_candidate(candidates, seen, "keys_file", f"nvidia_keys.txt:{index}", key)

    active_index = env_data.get("NVIDIA_API_KEY_ACTIVE") or os.getenv("NVIDIA_API_KEY_ACTIVE", "")
    active_name = f"NVIDIA_API_KEY_{active_index}" if active_index.isdigit() else None
    active_value = (env_data.get(active_name or "") or os.getenv(active_name or "") or "").strip()
    if active_name:
        candidates.sort(key=lambda item: 0 if item.name == active_name else 1)

    sources: dict[str, int] = {}
    for item in candidates:
        sources[item.source] = sources.get(item.source, 0) + 1

    meta = {
        "env_file": str(env_file),
        "keys_file": str(keys_file),
        "env_file_exists": env_file.exists(),
        "keys_file_exists": keys_file.exists(),
        "active_index": active_index if active_index else None,
        "active_key_present": bool(active_name and KEY_TOKEN_RE.fullmatch(active_value)),
        "total_keys": len(candidates),
        "sources": sources,
        "duplicates_removed": max(
            0,
            len(file_keys) + len(numbered_env) + int(bool(env_data.get("NVIDIA_API_KEY"))) + int(bool(os.getenv("NVIDIA_API_KEY"))) - len(candidates),
        ),
    }
    return candidates, meta


def presence_result(meta: dict[str, Any], candidates: list[KeyCandidate]) -> dict[str, Any]:
    return {
        "ok": bool(candidates),
        "mode": "presence",
        "key_pool": meta,
        "keys": [
            {
                "index": index,
                "source": item.source,
                "name": item.name,
                "present": True,
            }
            for index, item in enumerate(candidates, start=1)
        ],
        "secrets_printed": False,
    }


def request_json(method: str, url: str, headers: dict[str, str], timeout: int, payload: dict[str, Any] | None = None) -> SimpleResponse:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url=url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
            return SimpleResponse(status_code=response.status, payload=data)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"error": sanitize(raw[:200])}
        return SimpleResponse(status_code=exc.code, payload=data)


def attempt_models(base_url: str, model: str, timeout: int, key: KeyCandidate, index: int) -> dict[str, Any]:
    start = time.time()
    response = request_json(
        "GET",
        f"{base_url.rstrip('/')}/models",
        {"Authorization": f"Bearer {key.value}", "Content-Type": "application/json"},
        timeout=timeout,
    )
    latency_ms = int((time.time() - start) * 1000)
    item: dict[str, Any] = {
        "key_index": index,
        "source": key.source,
        "name": key.name,
        "http_status": response.status_code,
        "latency_ms": latency_ms,
        "ok": response.status_code == 200,
    }
    if response.status_code == 200:
        data = response.json()
        models = data.get("data", [])
        item.update(
            {
                "model_count": len(models),
                "target_model": model,
                "target_model_found": any(entry.get("id") == model for entry in models),
            }
        )
    else:
        item["error"] = classify_http_error(response.status_code)
    return item


def attempt_chat(base_url: str, model: str, timeout: int, key: KeyCandidate, index: int) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: NVIDIA_API_OK"}],
        "temperature": 0.2,
        "max_tokens": 32,
        "stream": False,
    }
    start = time.time()
    response = request_json(
        "POST",
        f"{base_url.rstrip('/')}/chat/completions",
        {"Authorization": f"Bearer {key.value}", "Content-Type": "application/json"},
        timeout=timeout,
        payload=payload,
    )
    latency_ms = int((time.time() - start) * 1000)
    item: dict[str, Any] = {
        "key_index": index,
        "source": key.source,
        "name": key.name,
        "http_status": response.status_code,
        "latency_ms": latency_ms,
        "ok": response.status_code == 200,
    }
    if response.status_code == 200:
        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        item["content_preview"] = sanitize(content[:64])
    else:
        item["error"] = classify_http_error(response.status_code)
    return item


def classify_http_error(status_code: int) -> str:
    if status_code == 401:
        return "unauthorized_or_invalid_key"
    if status_code == 403:
        return "forbidden_for_key_or_account"
    if status_code == 404:
        return "model_or_endpoint_not_found"
    if status_code == 429:
        return "rate_limited_or_quota_exhausted"
    if 500 <= status_code <= 599:
        return "upstream_server_error"
    return f"http_{status_code}"


def run_network_mode(
    mode: str,
    base_url: str,
    model: str,
    timeout: int,
    candidates: list[KeyCandidate],
    meta: dict[str, Any],
    exhaustive: bool,
) -> dict[str, Any]:
    if not candidates:
        return {
            "ok": False,
            "mode": mode,
            "error": "NVIDIA API key missing",
            "key_pool": meta,
            "attempts": [],
            "secrets_printed": False,
        }

    attempts: list[dict[str, Any]] = []
    for index, key in enumerate(candidates, start=1):
        try:
            if mode == "models":
                attempt = attempt_models(base_url, model, timeout, key, index)
            else:
                attempt = attempt_chat(base_url, model, timeout, key, index)
        except Exception as exc:  # noqa: BLE001 - diagnostics must report safe failures
            attempt = {
                "key_index": index,
                "source": key.source,
                "name": key.name,
                "ok": False,
                "error": sanitize(exc),
            }
        attempts.append(attempt)
        if attempt.get("ok") and not exhaustive:
            break

    successful = [item for item in attempts if item.get("ok")]
    return {
        "ok": bool(successful),
        "mode": mode,
        "provider": "nvidia",
        "base_url": base_url,
        "model": model,
        "key_pool": meta,
        "attempts": attempts,
        "working_key_index": successful[0]["key_index"] if successful else None,
        "exhaustive": exhaustive,
        "secrets_printed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safe NVIDIA API doctor")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Hermes .env path")
    parser.add_argument("--keys-file", default=os.getenv("NVIDIA_KEYS_FILE", str(DEFAULT_KEYS_FILE)), help="NVIDIA key pool file")
    parser.add_argument("--base-url", default=os.getenv("NVIDIA_BASE_URL", NVIDIA_BASE_URL), help="NVIDIA API base URL")
    parser.add_argument("--model", default=os.getenv("NVIDIA_MODEL", DEFAULT_MODEL), help="Model ID")
    parser.add_argument("--mode", choices=["presence", "models", "chat"], default="presence", help="Diagnostic mode")
    parser.add_argument("--timeout", type=int, default=int(os.getenv("NVIDIA_PROBE_TIMEOUT", "30")), help="HTTP timeout in seconds")
    parser.add_argument("--exhaustive", action="store_true", help="Try every key even after the first success")
    parser.add_argument("--json", action="store_true", help="Print JSON")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    candidates, meta = load_key_candidates(Path(args.env_file).expanduser(), Path(args.keys_file).expanduser())

    if args.mode == "presence":
        result = presence_result(meta, candidates)
    else:
        result = run_network_mode(
            mode=args.mode,
            base_url=args.base_url,
            model=args.model,
            timeout=args.timeout,
            candidates=candidates,
            meta=meta,
            exhaustive=args.exhaustive,
        )

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

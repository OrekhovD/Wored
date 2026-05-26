#!/usr/bin/env python3
"""WORED/Hermes model inventory and routing audit.

Reads provider credentials from ~/.hermes/secrets/model_keys.yaml and reports
only masked metadata. Raw keys never appear in JSON, Markdown, logs, or docs.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml


DEFAULT_SECRET_FILE = Path.home() / ".hermes" / "secrets" / "model_keys.yaml"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
PROMPT_MESSAGES = [
    {"role": "system", "content": "Return exactly OK."},
    {"role": "user", "content": "healthcheck"},
]


def mask_key(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("nvapi-"):
        return "nvapi-***"
    if value.startswith("sk-"):
        return "sk-***"
    return f"{value[:5]}***"


def load_secret_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {}
    return data


def classify_http_error(status_code: int) -> str:
    if status_code == 401:
        return "invalid_or_expired_key"
    if status_code == 403:
        return "forbidden_model_or_permission"
    if status_code == 404:
        return "model_not_found"
    if status_code == 429:
        return "quota_or_rate_limit"
    if status_code == 402:
        return "billing_or_quota"
    return "provider_error"


def probe_chat_completion(base_url: str, key: str, model: str, timeout: int) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": PROMPT_MESSAGES,
        "temperature": 0,
        "max_tokens": 8,
        "stream": False,
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            latency_ms = int((time.monotonic() - started) * 1000)
            body = response.read().decode("utf-8", errors="replace")
            data = json.loads(body) if body else {}
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            ok = "OK" in content.upper()
            return {
                "model": model,
                "status": "working" if ok else "failed",
                "latency_ms": latency_ms,
                "error_class": None if ok else "bad_response_format",
            }
    except urllib.error.HTTPError as exc:
        return {
            "model": model,
            "status": "failed",
            "latency_ms": None,
            "error_class": classify_http_error(exc.code),
        }
    except (TimeoutError, socket.timeout):
        return {
            "model": model,
            "status": "failed",
            "latency_ms": None,
            "error_class": "timeout",
        }
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", "")
        error_class = "timeout" if "timed out" in str(reason).lower() else "provider_error"
        return {
            "model": model,
            "status": "failed",
            "latency_ms": None,
            "error_class": error_class,
        }
    except Exception:
        return {
            "model": model,
            "status": "failed",
            "latency_ms": None,
            "error_class": "provider_error",
        }


def model_result(entry: dict[str, Any], model: str, skip_network: bool, timeout: int, base_url: str) -> dict[str, Any]:
    key = entry.get("key")
    if not key:
        return {"model": model, "status": "failed", "latency_ms": None, "error_class": "not_configured"}
    if skip_network:
        return {"model": model, "status": "not_tested", "latency_ms": None, "error_class": None}
    return probe_chat_completion(base_url, key, model, timeout)


def summarize_provider(
    provider: str,
    key_entries: list[dict[str, Any]],
    models: list[str],
    working_models: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    role: str | None,
    mode: str,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "mode": mode,
        "key_count": len([entry for entry in key_entries if entry.get("key")]),
        "key_sources": sorted({entry.get("id", provider) for entry in key_entries}),
        "masked_prefixes": sorted({mask_key(entry.get("key")) for entry in key_entries if entry.get("key")}),
        "models_tested": models,
        "working_models": working_models,
        "errors": errors,
        "recommended_role": role,
    }


def probe_nvidia(config: dict[str, Any], skip_network: bool, timeout: int) -> dict[str, Any]:
    entries = config.get("nvidia_model_bound") or []
    working: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    models: list[str] = []
    for entry in entries:
        model = entry.get("model")
        if not model:
            continue
        models.append(model)
        result = model_result(entry, model, skip_network, timeout, NVIDIA_BASE_URL)
        result["id"] = entry.get("id")
        result["role"] = entry.get("role")
        if result["status"] == "working":
            working.append({k: result[k] for k in ("id", "model", "latency_ms", "role")})
        elif result["status"] != "not_tested":
            errors.append({k: result.get(k) for k in ("id", "model", "error_class", "role")})
    return summarize_provider(
        provider="nvidia",
        key_entries=entries,
        models=models,
        working_models=working,
        errors=errors,
        role="model_bound_reviewer_architect_bug_hunt",
        mode="model_bound_key_per_model",
    )


def probe_multi_model_group(
    provider_name: str,
    entries: list[dict[str, Any]],
    skip_network: bool,
    timeout: int,
    role: str,
    mode: str = "single_key_multi_model",
) -> dict[str, Any]:
    working: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    models: list[str] = []
    for entry in entries:
        base_url = entry.get("base_url")
        for model in entry.get("models") or []:
            models.append(model)
            if not base_url:
                result = {"model": model, "status": "failed", "latency_ms": None, "error_class": "not_configured"}
            else:
                result = model_result(entry, model, skip_network, timeout, base_url)
            result["id"] = entry.get("id")
            result["role"] = entry.get("role")
            if result["status"] == "working":
                working.append({k: result[k] for k in ("id", "model", "latency_ms", "role")})
            elif result["status"] != "not_tested":
                errors.append({k: result.get(k) for k in ("id", "model", "error_class", "role")})
    return summarize_provider(provider_name, entries, models, working, errors, role, mode)


def build_inventory(secret_file: Path, skip_network: bool, timeout: int) -> dict[str, Any]:
    config = load_secret_config(secret_file)
    providers: list[dict[str, Any]] = []
    warnings: list[str] = []

    if not config:
        return {
            "status": "no_keys",
            "secret_file": str(secret_file),
            "providers": [],
            "recommended_routing": {},
            "warnings": ["model_keys.yaml missing or empty"],
        }

    providers.append(probe_nvidia(config, skip_network, timeout))
    providers.append(
        probe_multi_model_group(
            "deepseek_official",
            config.get("deepseek_official") or [],
            skip_network,
            timeout,
            "cross_provider_fallback",
        )
    )

    china_entries = config.get("china_multi_model_providers") or []
    for provider_name in ("qwen", "glm", "google_ai_studio"):
        entries = [entry for entry in china_entries if entry.get("provider") == provider_name]
        role = {
            "qwen": "main_coding_driver_and_fast_fallback",
            "glm": "russian_reasoning_fallback",
            "google_ai_studio": "cheap_fast_fallback",
        }.get(provider_name)
        providers.append(probe_multi_model_group(provider_name, entries, skip_network, timeout, role or "fallback"))

    minimax_entries = config.get("minimax_official") or []
    providers.append(
        probe_multi_model_group(
            "minimax_official",
            minimax_entries,
            skip_network,
            timeout,
            "reviewer_fallback",
        )
    )

    working_count = sum(len(provider["working_models"]) for provider in providers)
    key_count = sum(provider["key_count"] for provider in providers)
    if key_count == 0:
        status = "no_keys"
    elif skip_network:
        status = "partial"
        warnings.append("network probes skipped")
    elif working_count > 0:
        status = "ok"
    else:
        status = "partial"
        warnings.append("keys present but no tested model returned OK")

    return {
        "status": status,
        "secret_file": str(secret_file),
        "providers": providers,
        "recommended_routing": recommended_routing(providers),
        "warnings": warnings,
    }


def first_working(
    providers: list[dict[str, Any]],
    provider_name: str,
    preferred: list[str],
    *,
    strict: bool = False,
) -> dict[str, Any] | None:
    provider = next((item for item in providers if item["provider"] == provider_name), None)
    if not provider:
        return None
    working = provider.get("working_models") or []
    for model in preferred:
        found = next((item for item in working if item.get("model") == model), None)
        if found:
            return found
    if strict:
        return None
    return working[0] if working else None


def recommended_routing(providers: list[dict[str, Any]]) -> dict[str, Any]:
    qwen = first_working(providers, "qwen", ["qwen3-coder-plus", "qwen-plus", "qwen-flash"])
    nvidia_m2 = first_working(providers, "nvidia", ["minimaxai/minimax-m2.7"], strict=True)
    nvidia_qwen = first_working(providers, "nvidia", ["qwen/qwen3-coder-480b-a35b-instruct"], strict=True)
    nvidia_deepseek = first_working(
        providers,
        "nvidia",
        ["deepseek-ai/deepseek-v4-pro", "deepseek-ai/deepseek-v3.2"],
        strict=True,
    )
    glm = first_working(providers, "glm", ["glm-4-flash", "glm-4", "glm-4.7"])
    google = first_working(providers, "google_ai_studio", ["gemini-3-flash-preview", "gemini-2.5-flash"])
    deepseek = first_working(providers, "deepseek_official", ["deepseek-chat", "deepseek-reasoner"])
    default = (
        {"provider": "alibaba", "model": qwen.get("model"), "reason": "Qwen is the preferred daily coding driver"}
        if qwen
        else {"provider": "zai", "model": glm.get("model"), "reason": "Qwen probe failed; GLM is the first confirmed fallback"}
        if glm
        else {"provider": "google_ai_studio", "model": google.get("model"), "reason": "Only Google AI Studio confirmed among multi-model providers"}
        if google
        else {"provider": "nvidia", "model": nvidia_qwen.get("model"), "reason": "NVIDIA model-bound Qwen key confirmed"}
        if nvidia_qwen
        else {"provider": "nvidia", "model": nvidia_deepseek.get("model"), "reason": "NVIDIA model-bound DeepSeek key confirmed"}
        if nvidia_deepseek
        else {"provider": None, "model": None, "reason": "No working model confirmed"}
    )

    return {
        "default": default,
        "fallback_chain": [
            item
            for item in [
                {"provider": "nvidia", "model": nvidia_m2.get("model"), "role": "architecture_reviewer"} if nvidia_m2 else None,
                {"provider": "nvidia", "model": nvidia_qwen.get("model"), "role": "coding_reviewer"} if nvidia_qwen else None,
                {"provider": "nvidia", "model": nvidia_deepseek.get("model"), "role": "reasoning_reviewer"} if nvidia_deepseek else None,
                {"provider": "zai", "model": glm.get("model"), "role": "russian_reasoning_fallback"} if glm else None,
                {"provider": "google_ai_studio", "model": google.get("model"), "role": "fast_low_cost_fallback"} if google else None,
                {"provider": "deepseek_official", "model": deepseek.get("model"), "role": "cross_provider_fallback"} if deepseek else None,
            ]
            if item and not (item["provider"] == default.get("provider") and item["model"] == default.get("model"))
        ],
        "credential_strategy": {
            "nvidia": "model_bound_do_not_cross_test_keys",
            "qwen": "single_key_multi_model_fill_first",
            "glm": "single_key_multi_model_fill_first",
            "google_ai_studio": "single_key_multi_model_fill_first",
            "deepseek_official": "single_key_multi_model_fill_first",
        },
        "config_applyable": bool(default.get("model")),
    }


def render_markdown(inventory: dict[str, Any]) -> str:
    lines = [
        "# WORED/Hermes Model Inventory",
        "",
        f"Status: `{inventory['status']}`",
        "",
        "| Provider | Keys | Models tested | Working models | Errors | Role |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for provider in inventory["providers"]:
        lines.append(
            "| {provider} | {keys} | {tested} | {working} | {errors} | {role} |".format(
                provider=provider["provider"],
                keys=provider["key_count"],
                tested=len(provider["models_tested"]),
                working=len(provider["working_models"]),
                errors=len(provider["errors"]),
                role=provider.get("recommended_role") or "",
            )
        )
    routing = inventory.get("recommended_routing") or {}
    lines.extend(
        [
            "",
            "## Recommended Routing",
            "",
            f"- Default provider: `{(routing.get('default') or {}).get('provider')}`",
            f"- Default model: `{(routing.get('default') or {}).get('model')}`",
            "- Fallback chain: "
            + ", ".join(
                f"{item['provider']}:{item['model']} ({item['role']})"
                for item in routing.get("fallback_chain", [])
            ),
            "",
            "## Safety Rules",
            "",
            "- NVIDIA `nvapi-*` keys are model-bound; do not try a key against unrelated models.",
            "- Qwen, GLM, Google AI Studio, and DeepSeek official keys are single-key multi-model providers.",
            "- Models provide advisory text; Python scripts perform deterministic risk, scoring, and trade math.",
            "- Do not store raw keys in git, docs, scripts, Hermes config, reports, or Telegram output.",
        ]
    )
    if inventory.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in inventory["warnings"])
    return "\n".join(lines) + "\n"


def validate_inventory_contract(inventory: dict[str, Any]) -> None:
    required = {"status", "providers", "recommended_routing", "warnings"}
    missing = required - set(inventory)
    if missing:
        raise ValueError(f"missing top-level fields: {sorted(missing)}")
    provider_required = {"provider", "key_count", "models_tested", "working_models", "errors"}
    for provider in inventory["providers"]:
        provider_missing = provider_required - set(provider)
        if provider_missing:
            name = provider.get("provider", "<unknown>")
            raise ValueError(f"{name}: missing provider fields: {sorted(provider_missing)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="WORED/Hermes model inventory")
    parser.add_argument("--format", choices=["json", "markdown"], default="json")
    parser.add_argument("--secret-file", default=str(DEFAULT_SECRET_FILE))
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--skip-network", action="store_true")
    parser.add_argument("--output", help="Write output to this file instead of stdout")
    parser.add_argument("--validate-json", action="store_true", help="Validate the JSON contract before writing output")
    args = parser.parse_args()

    inventory = build_inventory(Path(args.secret_file).expanduser(), args.skip_network, args.timeout)
    if args.validate_json:
        validate_inventory_contract(inventory)
    if args.format == "json":
        rendered = json.dumps(inventory, indent=2, ensure_ascii=False) + "\n"
    else:
        rendered = render_markdown(inventory)
    if args.output:
        output_path = Path(args.output).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
        print(f"MODEL_INVENTORY_WRITTEN path={output_path} status={inventory['status']}")
        if args.validate_json:
            print("MODEL_INVENTORY_SCHEMA_OK")
    else:
        print(rendered, end="")
    return 0 if inventory["status"] in {"ok", "partial", "no_keys"} else 1


if __name__ == "__main__":
    sys.exit(main())

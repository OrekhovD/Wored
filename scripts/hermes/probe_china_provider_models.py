#!/usr/bin/env python3
"""Probe Qwen/GLM/Google multi-model provider entries from model_keys.yaml."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from model_inventory import DEFAULT_SECRET_FILE, load_secret_config, probe_multi_model_group


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Chinese-compatible multi-model providers")
    parser.add_argument("--secret-file", default=str(DEFAULT_SECRET_FILE))
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--skip-network", action="store_true")
    args = parser.parse_args()

    config = load_secret_config(Path(args.secret_file).expanduser())
    entries = config.get("china_multi_model_providers") or []
    results = []
    for provider_name, role in {
        "qwen": "main_coding_driver_and_fast_fallback",
        "glm": "russian_reasoning_fallback",
        "google_ai_studio": "cheap_fast_fallback",
    }.items():
        provider_entries = [entry for entry in entries if entry.get("provider") == provider_name]
        results.append(probe_multi_model_group(provider_name, provider_entries, args.skip_network, args.timeout, role))
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

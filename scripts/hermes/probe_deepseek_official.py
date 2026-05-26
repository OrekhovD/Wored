#!/usr/bin/env python3
"""Probe DeepSeek official multi-model provider entries from model_keys.yaml."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from model_inventory import DEFAULT_SECRET_FILE, load_secret_config, probe_multi_model_group


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe DeepSeek official entries")
    parser.add_argument("--secret-file", default=str(DEFAULT_SECRET_FILE))
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--skip-network", action="store_true")
    args = parser.parse_args()

    config = load_secret_config(Path(args.secret_file).expanduser())
    result = probe_multi_model_group(
        "deepseek_official",
        config.get("deepseek_official") or [],
        args.skip_network,
        args.timeout,
        "cross_provider_fallback",
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

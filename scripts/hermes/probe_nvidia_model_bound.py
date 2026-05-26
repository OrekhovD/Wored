#!/usr/bin/env python3
"""Probe only NVIDIA model-bound key/model pairs from model_keys.yaml."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from model_inventory import DEFAULT_SECRET_FILE, load_secret_config, probe_nvidia


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe NVIDIA model-bound entries")
    parser.add_argument("--secret-file", default=str(DEFAULT_SECRET_FILE))
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--skip-network", action="store_true")
    args = parser.parse_args()

    config = load_secret_config(Path(args.secret_file).expanduser())
    print(json.dumps(probe_nvidia(config, args.skip_network, args.timeout), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

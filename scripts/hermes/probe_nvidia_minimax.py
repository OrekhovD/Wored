#!/usr/bin/env python3
"""Safe NVIDIA NIM MiniMax M2.7 probe for WORED/Hermes.

This wrapper keeps the Hermes quick command stable while delegating key-pool
loading and sequential fallback to scripts/nvidia_api_doctor.py.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCTOR = PROJECT_ROOT / "scripts" / "nvidia_api_doctor.py"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe NVIDIA MiniMax M2.7 through the safe key pool")
    parser.add_argument("--dry-run", action="store_true", help="Only verify that keys are discoverable")
    parser.add_argument("--exhaustive", action="store_true", help="Try every key even after the first success")
    parser.add_argument("--timeout", type=int, default=45, help="HTTP timeout in seconds")
    parser.add_argument("--model", default="minimaxai/minimax-m2.7", help="NVIDIA model id")
    parser.add_argument("--keys-file", default=None, help="Optional key pool file override")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    command = [
        sys.executable,
        str(DOCTOR),
        "--mode",
        "presence" if args.dry_run else "chat",
        "--model",
        args.model,
        "--timeout",
        str(args.timeout),
        "--json",
    ]
    if args.keys_file:
        command.extend(["--keys-file", args.keys_file])
    if args.exhaustive:
        command.append("--exhaustive")

    completed = subprocess.run(command, cwd=str(PROJECT_ROOT), text=True, capture_output=True)
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()

    if stdout:
        try:
            print(json.dumps(json.loads(stdout), indent=2, ensure_ascii=False))
        except json.JSONDecodeError:
            print(json.dumps({"ok": False, "error": "doctor returned non-json output"}, indent=2))
            return 1
    if stderr:
        print(json.dumps({"warning": "stderr suppressed to avoid leaking diagnostics", "secrets_printed": False}, indent=2))
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())

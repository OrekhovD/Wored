#!/usr/bin/env python3

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

# --- CONFIG ---
HERMES_ENV_PATH = os.path.expanduser("~/.hermes/.env")

# --- UTILS ---
def load_env_lines(env_path: str) -> List[str]:
    """Read .env file lines preserving comments and empty lines."""
    try:
        with open(env_path, "r") as f:
            return [line.rstrip("\n") for line in f.readlines()]
    except FileNotFoundError:
        return []

def save_env_lines(env_path: str, lines: List[str]):
    """Write .env file with backup."""
    # Create backup
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{env_path}.bak.{timestamp}"
    if os.path.exists(env_path):
        shutil.copy2(env_path, backup_path)
    # Write new
    with open(env_path, "w") as f:
        f.write("\n".join(lines) + "\n")

def parse_env_lines(lines: List[str]) -> Dict[str, str]:
    """Parse key-value pairs, skip comments and malformed lines."""
    env = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):  # skip comments & empty
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            if key.startswith("NVIDIA_API_KEY"):
                env[key] = val.strip()
    return env

def update_active_key(lines: List[str], new_index: str) -> List[str]:
    """Update NVIDIA_API_KEY_ACTIVE line. Add if missing."""
    new_lines = []
    found = False
    for line in lines:
        if line.strip().startswith("NVIDIA_API_KEY_ACTIVE="):
            new_lines.append(f"NVIDIA_API_KEY_ACTIVE={new_index}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"NVIDIA_API_KEY_ACTIVE={new_index}")
    return new_lines

def get_pool_status(env_dict: Dict[str, str]) -> List[Dict[str, Any]]:
    """Get status of all NVIDIA_API_KEY_* entries."""
    pool = []
    active_idx = env_dict.get("NVIDIA_API_KEY_ACTIVE", "").strip()
    for key in sorted(env_dict.keys()):
        if key.startswith("NVIDIA_API_KEY_") and key != "NVIDIA_API_KEY_ACTIVE":
            present = key in env_dict
            pool.append({"name": key, "present": present})
    return pool

def get_status(env_path: str) -> Dict[str, Any]:
    """Return full status dict."""
    lines = load_env_lines(env_path)
    env_dict = parse_env_lines(lines)
    pool = get_pool_status(env_dict)
    active_idx = env_dict.get("NVIDIA_API_KEY_ACTIVE", "").strip()
    active_present = False
    if active_idx.isdigit():
        active_key_name = f"NVIDIA_API_KEY_{active_idx}"
        active_present = active_key_name in env_dict

    return {
        "env_file": env_path,
        "single_key_present": "NVIDIA_API_KEY" in env_dict,
        "active_index": active_idx if active_idx else None,
        "active_key_present": active_present,
        "pool": pool,
        "secrets_printed": False,
    }

# --- MAIN ---
def main():
    parser = argparse.ArgumentParser(description="NVIDIA Key Manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    status_parser = subparsers.add_parser("status", help="Show key status")

    # list (alias for status)
    list_parser = subparsers.add_parser("list", help="Alias for status")

    # set-active
    set_parser = subparsers.add_parser("set-active", help="Set active key index")
    set_parser.add_argument("index", type=str, help="Index (e.g., 1, 2, 3)")

    args = parser.parse_args()

    if args.command in ["status", "list"]:
        result = get_status(HERMES_ENV_PATH)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.command == "set-active":
        lines = load_env_lines(HERMES_ENV_PATH)
        new_lines = update_active_key(lines, args.index)
        save_env_lines(HERMES_ENV_PATH, new_lines)
        # Reload to confirm
        result = get_status(HERMES_ENV_PATH)
        result["ok"] = True
        result["active_index"] = args.index
        result["active_key_present"] = result.get("active_key_present", False)
        result["secrets_printed"] = False
        print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()

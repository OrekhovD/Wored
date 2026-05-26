#!/usr/bin/env python3
"""Manage WORED/Hermes NVIDIA key pools without printing secrets."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


HERMES_ENV_PATH = Path.home() / ".hermes" / ".env"
HERMES_KEYS_PATH = Path.home() / ".hermes" / "secrets" / "nvidia_keys.txt"
KEY_TOKEN_RE = re.compile(r"nvapi-[A-Za-z0-9_.-]+")
NUMBERED_KEY_RE = re.compile(r"^NVIDIA_API_KEY_(\d+)$")


def read_text_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except FileNotFoundError:
        return []


def load_env_lines(env_path: Path) -> list[str]:
    return read_text_lines(env_path)


def parse_env_lines(lines: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in lines:
        item = line.strip()
        if not item or item.startswith("#") or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        env[key] = value.strip().strip("'\"")
    return env


def extract_keys_from_text(path: Path) -> list[str]:
    keys: list[str] = []
    for line in read_text_lines(path):
        if line.strip().startswith("#"):
            continue
        keys.extend(match.group(0) for match in KEY_TOKEN_RE.finditer(line))
    return keys


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def ensure_private_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass


def write_private_file(path: Path, lines: list[str]) -> None:
    ensure_private_parent(path)
    if path.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = path.with_name(f"{path.name}.bak.{timestamp}")
        shutil.copy2(path, backup_path)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def update_active_key(lines: list[str], new_index: str) -> list[str]:
    new_lines: list[str] = []
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


def numbered_sort_key(name: str) -> tuple[int, str]:
    match = NUMBERED_KEY_RE.match(name)
    if match:
        return (int(match.group(1)), name)
    return (10**9, name)


def get_status(env_path: Path, keys_path: Path) -> dict[str, Any]:
    env = parse_env_lines(load_env_lines(env_path))
    numbered_keys = sorted([name for name in env if NUMBERED_KEY_RE.match(name)], key=numbered_sort_key)
    file_keys = extract_keys_from_text(keys_path)
    active_idx = env.get("NVIDIA_API_KEY_ACTIVE", "").strip()
    active_key_name = f"NVIDIA_API_KEY_{active_idx}" if active_idx.isdigit() else None

    return {
        "ok": True,
        "env_file": str(env_path),
        "keys_file": str(keys_path),
        "env_file_exists": env_path.exists(),
        "keys_file_exists": keys_path.exists(),
        "single_key_present": bool(env.get("NVIDIA_API_KEY")),
        "numbered_env_keys": [{"name": name, "present": True} for name in numbered_keys],
        "keys_file_count": len(ordered_unique(file_keys)),
        "active_index": active_idx if active_idx else None,
        "active_key_present": bool(active_key_name and env.get(active_key_name)),
        "total_known_keys": len(
            ordered_unique(
                ([env["NVIDIA_API_KEY"]] if env.get("NVIDIA_API_KEY") else [])
                + [env[name] for name in numbered_keys if env.get(name)]
                + file_keys
            )
        ),
        "secrets_printed": False,
    }


def import_file(source: Path, keys_path: Path, replace: bool) -> dict[str, Any]:
    source_keys = extract_keys_from_text(source)
    if not source.exists():
        return {
            "ok": False,
            "error": "source file not found",
            "source_file": str(source),
            "secrets_printed": False,
        }
    current_keys = [] if replace else extract_keys_from_text(keys_path)
    merged = ordered_unique(current_keys + source_keys)
    write_private_file(keys_path, merged)
    return {
        "ok": True,
        "source_file": str(source),
        "keys_file": str(keys_path),
        "source_keys_found": len(source_keys),
        "keys_written": len(merged),
        "duplicates_removed": len(current_keys) + len(source_keys) - len(merged),
        "replace": replace,
        "secrets_printed": False,
    }


def set_active(env_path: Path, keys_path: Path, index: str) -> dict[str, Any]:
    if not index.isdigit() or int(index) < 1:
        return {"ok": False, "error": "index must be a positive integer", "secrets_printed": False}
    lines = update_active_key(load_env_lines(env_path), index)
    write_private_file(env_path, lines)
    status = get_status(env_path, keys_path)
    status["active_index"] = index
    return status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NVIDIA key pool manager")
    parser.add_argument("--env-file", default=str(HERMES_ENV_PATH), help="Hermes .env path")
    parser.add_argument("--keys-file", default=os.getenv("NVIDIA_KEYS_FILE", str(HERMES_KEYS_PATH)), help="NVIDIA key pool path")
    parser.add_argument("--json", action="store_true", help="Print JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Show safe key pool status")
    status_parser.add_argument("--json", action="store_true", help="Print JSON")

    list_parser = subparsers.add_parser("list", help="Alias for status")
    list_parser.add_argument("--json", action="store_true", help="Print JSON")

    set_parser = subparsers.add_parser("set-active", help="Set NVIDIA_API_KEY_ACTIVE in .env")
    set_parser.add_argument("index", help="Positive key index")
    set_parser.add_argument("--json", action="store_true", help="Print JSON")

    import_parser = subparsers.add_parser("import-file", help="Extract nvapi-* tokens from a source file into the key pool")
    import_parser.add_argument("source_file", help="Source text file with one or many nvapi-* tokens")
    import_parser.add_argument("--replace", action="store_true", help="Replace existing key pool instead of appending")
    import_parser.add_argument("--json", action="store_true", help="Print JSON")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    env_path = Path(args.env_file).expanduser()
    keys_path = Path(args.keys_file).expanduser()

    if args.command in {"status", "list"}:
        result = get_status(env_path, keys_path)
    elif args.command == "set-active":
        result = set_active(env_path, keys_path, args.index)
    elif args.command == "import-file":
        result = import_file(Path(args.source_file).expanduser(), keys_path, args.replace)
    else:
        result = {"ok": False, "error": "unknown command", "secrets_printed": False}

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

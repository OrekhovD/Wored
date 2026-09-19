"""Validate the specification package, not the WORED application. Stdlib only."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    seen: set[str] = set()
    for entry in manifest["files"]:
        name = entry["path"]
        candidate = (root / name).resolve()
        if name in seen or not candidate.is_relative_to(root) or candidate == root:
            errors.append(f"Unsafe or duplicate manifest path: {name}")
            continue
        seen.add(name)
        if not candidate.is_file():
            errors.append(f"Missing file: {name}")
            continue
        raw = candidate.read_bytes()
        if len(raw) != entry["bytes"]:
            errors.append(f"Size mismatch: {name}")
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            errors.append(f"SHA-256 mismatch: {name}")
    actual = {
        item.relative_to(root).as_posix()
        for item in root.rglob("*")
        if item.is_file() and "__pycache__" not in item.parts
        and item != root / "MANIFEST.json"
    }
    if actual != seen:
        errors.append(f"Inventory mismatch: {sorted(actual ^ seen)}")
    tasks = json.loads((root / "TASKS.json").read_text(encoding="utf-8"))
    expected_requirements = {f"REQ-{n:02d}" for n in range(1, 15)}
    expected_cases = {f"AC-{n:02d}" for n in range(1, 29)}
    readme = (root / "README.md").read_text(encoding="utf-8")
    acceptance = (root / "ACCEPTANCE.md").read_text(encoding="utf-8")
    if set(re.findall(r"REQ-\d{2}", readme)) != expected_requirements:
        errors.append("README requirement inventory is incomplete")
    rows = [line for line in acceptance.splitlines() if re.match(r"\| AC-\d{2} \|", line)]
    if {re.search(r"AC-\d{2}", row)[0] for row in rows} != expected_cases:
        errors.append("Acceptance case inventory is incomplete")
    if len(rows) != len(expected_cases):
        errors.append("Duplicate acceptance case")
    covered = {req for row in rows for req in re.findall(r"REQ-\d{2}", row)}
    if covered != expected_requirements:
        errors.append("Acceptance requirements coverage is incomplete")
    ids: set[str] = set()
    task_requirements: set[str] = set()
    task_cases: set[str] = set()
    for task in tasks["tasks"]:
        if task["id"] in ids or not set(task["depends_on"]).issubset(ids):
            errors.append(f"Duplicate task or invalid dependency order: {task['id']}")
        ids.add(task["id"])
        task_requirements.update(task["requirements"])
        task_cases.update(task["acceptance_cases"])
        if task["status"] != "not_started":
            errors.append(f"Specification must not claim implementation: {task['id']}")
    if task_requirements != expected_requirements or task_cases != expected_cases:
        errors.append("Tasks do not cover all requirements/cases")
    for name in ("BASELINE.json", "TASKS.json"):
        json.loads((root / name).read_text(encoding="utf-8"))
    if errors:
        print("FAIL: specification package")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"PASS: {len(seen)} files; 14 requirements; 28 cases; {len(ids)} tasks")
    print("Checks: inventory, SHA-256, JSON, requirement coverage, dependency order")
    print("Application implementation and runtime were NOT tested by this validator.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

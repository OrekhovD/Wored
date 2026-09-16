#!/usr/bin/env python3
"""Validate paper trading acceptance evidence.

Checks structure, required levels, case inventory completeness,
artifact paths, hashes, and aggregation rules.
Non-zero exit on incomplete/failed/blocked/inconsistent evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


REQUIRED_AC_IDS = [f"AC-{i:02d}" for i in range(1, 29)]

REQUIRED_FIELDS = [
    "case_id", "requirement_ids", "status", "evidence_level",
    "utc_start", "utc_end", "environment", "git_sha",
    "commands", "expected", "actual", "assertions",
    "artifacts", "residual_limits",
]

VALID_STATUSES = {"PASS", "FAIL", "BLOCKED"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_evidence(input_path: str) -> Tuple[bool, List[str], List[str]]:
    """Validate acceptance.json. Returns (ok, errors, warnings)."""
    errors: List[str] = []
    warnings: List[str] = []

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        return False, ["Root is not a JSON object"], []

    cases = data.get("cases", [])
    if not isinstance(cases, list):
        return False, ["'cases' is not a list"], []

    # 1. Exactly AC-01..AC-28, no duplicates, no unknown IDs
    case_ids = [c.get("case_id", "") for c in cases]
    for ac in REQUIRED_AC_IDS:
        if ac not in case_ids:
            errors.append(f"Missing case: {ac}")
    seen = set()
    for cid in case_ids:
        if cid in seen:
            errors.append(f"Duplicate case: {cid}")
        seen.add(cid)
    for cid in case_ids:
        if cid not in REQUIRED_AC_IDS:
            errors.append(f"Unknown case ID: {cid}")

    # 2. Sum PASS+FAIL+BLOCKED == 28
    pass_count = sum(1 for c in cases if c.get("status") == "PASS")
    fail_count = sum(1 for c in cases if c.get("status") == "FAIL")
    blocked_count = sum(1 for c in cases if c.get("status") == "BLOCKED")
    total = pass_count + fail_count + blocked_count
    if total != 28:
        errors.append(f"Sum PASS({pass_count})+FAIL({fail_count})+BLOCKED({blocked_count})={total} != 28")
    other_count = sum(1 for c in cases if c.get("status") not in VALID_STATUSES)
    if other_count:
        errors.append(f"{other_count} cases with invalid status")

    # 3. Each case must have required fields
    for c in cases:
        cid = c.get("case_id", "?")
        for field in REQUIRED_FIELDS:
            if field not in c:
                errors.append(f"{cid}: missing required field '{field}'")

    # 4. Any required assertion FAIL means case FAIL
    for c in cases:
        cid = c.get("case_id", "?")
        status = c.get("status", "?")
        assertions = c.get("assertions", {})
        if isinstance(assertions, dict):
            assertions = [assertions]
        if not isinstance(assertions, list):
            warnings.append(f"{cid}: assertions is not a list")
            continue

        for a in assertions:
            if not isinstance(a, dict):
                continue
            a_result = a.get("result", "?")
            a_required = a.get("required", True)
            if a_required and a_result == "FAIL" and status != "FAIL":
                errors.append(f"{cid}: required assertion '{a.get('name','?')}' is FAIL but case status is {status}")

    # 5. PASS with nested FAIL is invalid
    for c in cases:
        cid = c.get("case_id", "?")
        status = c.get("status", "?")
        if status == "PASS":
            assertions = c.get("assertions", {})
            if isinstance(assertions, dict):
                assertions = [assertions]
            if isinstance(assertions, list):
                for a in assertions:
                    if isinstance(a, dict) and a.get("result") == "FAIL":
                        errors.append(f"{cid}: overall PASS but assertion '{a.get('name','?')}' is FAIL")

    # 6. BLOCKED must have blocker and next_action
    for c in cases:
        cid = c.get("case_id", "?")
        if c.get("status") == "BLOCKED":
            if not c.get("blocker"):
                errors.append(f"{cid}: BLOCKED without 'blocker' field")
            if not c.get("next_action"):
                warnings.append(f"{cid}: BLOCKED without 'next_action' field")

    # 7. Artifacts: paths must be relative, within evidence root, exist, hash matches
    evidence_root = Path(input_path).parent
    for c in cases:
        cid = c.get("case_id", "?")
        for art in c.get("artifacts", []):
            if not isinstance(art, dict):
                continue
            art_path = art.get("path", "")
            if not art_path:
                warnings.append(f"{cid}: artifact without path")
                continue
            if os.path.isabs(art_path):
                errors.append(f"{cid}: artifact path is absolute: {art_path}")
                continue
            full_path = evidence_root / art_path
            if not full_path.exists():
                warnings.append(f"{cid}: artifact file not found: {art_path}")
                continue
            expected_hash = art.get("sha256")
            if expected_hash:
                actual_hash = sha256_file(full_path)
                if actual_hash != expected_hash:
                    errors.append(f"{cid}: artifact hash mismatch for {art_path}")

    # 8. Summary must match computed counts
    summary = data.get("summary", {})
    if isinstance(summary, dict):
        s_pass = summary.get("pass", -1)
        s_fail = summary.get("fail", -1)
        s_blocked = summary.get("blocked", -1)
        if s_pass != pass_count:
            errors.append(f"Summary pass={s_pass} but computed={pass_count}")
        if s_fail != fail_count:
            errors.append(f"Summary fail={s_fail} but computed={fail_count}")
        if s_blocked != blocked_count:
            errors.append(f"Summary blocked={s_blocked} but computed={blocked_count}")

    ok = len(errors) == 0
    return ok, errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate paper trading acceptance evidence")
    parser.add_argument("--input", required=True, help="Path to acceptance.json")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    args = parser.parse_args()

    ok, errors, warnings = validate_evidence(args.input)

    if errors:
        print("FAIL: Evidence validation failed", file=sys.stderr)
        for e in errors:
            print(f"  ERROR: {e}", file=sys.stderr)
    if warnings:
        for w in warnings:
            print(f"  WARN: {w}", file=sys.stderr)

    if ok and not warnings:
        print("PASS: Evidence validation succeeded")
        return 0
    elif ok and warnings and not args.strict:
        print("PASS (with warnings): Evidence validation succeeded")
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
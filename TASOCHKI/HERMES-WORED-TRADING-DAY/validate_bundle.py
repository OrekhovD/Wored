"""Validate this specification bundle, not WORED implementation or runtime."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal as D
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read_json(root: Path, name: str):
    return json.loads((root / name).read_text(encoding="utf-8"))


def main() -> None:
    root = Path(__file__).resolve().parent
    manifest = read_json(root, "MANIFEST.json")
    require(manifest["scope"] == "specification_only", "Wrong manifest scope")
    for entry in manifest["files"]:
        path = (root / entry["path"]).resolve()
        require(root in path.parents, "Manifest path escapes bundle")
        require(path.is_file(), "Missing file: " + entry["path"])
        require(hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"],
                "Hash mismatch: " + entry["path"])

    acceptance = read_json(root, "acceptance.json")
    criteria = acceptance["criteria"]
    ids = [item["id"] for item in criteria]
    require(len(ids) == len(set(ids)), "Duplicate acceptance IDs")
    require(ids == ["A%02d" % i for i in range(1, len(ids) + 1)], "Nonsequential IDs")
    status = read_json(root, "STATUS.template.json")
    stages = {stage["id"] for stage in status["stages"]}
    require(len(stages) == 11, "Expected TD-00 through TD-10")
    require(set(status["acceptance"]) == set(ids), "Status/acceptance mismatch")
    for item in criteria:
        require(item["stage"] in stages, "Unknown stage: " + item["stage"])
        require(item["mandatory"] is True, "Unexpected optional criterion")
        require(item["status"] == "not_run", "Authored criteria must not claim pass")
        require(bool(item["steps"]) and bool(item["expected"]) and bool(item["evidence"]),
                "Incomplete criterion: " + item["id"])
        require(status["acceptance"][item["id"]]["status"] == "not_run", "Bad initial status")

    fixtures = read_json(root, "accounting-fixtures.json")["cases"]
    fixture_ids = {case["id"] for case in fixtures}
    require(len(fixture_ids) == len(fixtures), "Duplicate fixture ID")
    for item in criteria:
        require(set(item.get("fixture_ids", [])) <= fixture_ids, "Missing fixture reference")
    for case in fixtures:
        require(case["side"] in {"long", "short"}, "Invalid side")
        qty = D(case["entry_quantity"])
        entry = D(case["entry_price"])
        sign = D(1) if case["side"] == "long" else D(-1)
        require(sum((D(x["quantity"]) for x in case["exits"]), D(0)) == qty,
                "Exit quantities do not conserve entry: " + case["id"])
        gross = sum((sign * D(x["quantity"]) * (D(x["price"]) - entry)
                     for x in case["exits"]), D(0))
        entry_fee = qty * entry * D(case["entry_fee_rate"])
        exit_fees = sum((D(x["quantity"]) * D(x["price"]) * D(x["fee_rate"])
                         for x in case["exits"]), D(0))
        funding = sum((D(x) for x in case["funding_cashflows"]), D(0))
        net = gross - entry_fee - exit_fees + funding
        actual = dict(gross=gross, entry_fee=entry_fee, exit_fees=exit_fees,
                      funding=funding, net=net, closing_cash=D(case["opening_cash"]) + net)
        require(set(actual) == set(case["expected"]), "Expected result fields differ")
        for key, value in actual.items():
            require(value == D(case["expected"][key]), case["id"] + " wrong " + key)
        if "expected_slippage_impact" in case:
            reference = sign * qty * (D(case["exit_reference_price"]) - D(case["entry_reference_price"]))
            require(reference - gross == D(case["expected_slippage_impact"]), "Wrong slippage impact")

    for name in ("README.md", "START-HERMES.md"):
        content = (root / name).read_text(encoding="utf-8")
        require(not any(line.rstrip() != line for line in content.splitlines()), "Trailing whitespace")
    for stage in stages:
        require(stage in (root / "README.md").read_text(encoding="utf-8"), "Stage missing from README")
    print("PASS: bundle; %d hashed files; %d criteria; %d stages; %d Decimal fixtures" %
          (len(manifest["files"]), len(criteria), len(stages), len(fixtures)))
    print("WORED application tests, browser acceptance, Telegram and production: NOT RUN")


if __name__ == "__main__":
    main()

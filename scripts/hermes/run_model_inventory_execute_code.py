#!/usr/bin/env python3
"""Execute Code wrapper for WORED/Hermes model inventory.

Hermes Execute Code accepts Python source, not shell commands. Paste this into
Execute Code when Terminal is unavailable:

exec(open("/mnt/d/WORED/scripts/hermes/run_model_inventory_execute_code.py", encoding="utf-8").read())
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path


OUTPUT_PATH = Path("/tmp/wored_model_inventory.json")
SCRIPT_PATH = "/mnt/d/WORED/scripts/hermes/model_inventory.py"


def main() -> int:
    sys.argv = [
        "model_inventory.py",
        "--format",
        "json",
        "--timeout",
        "8",
        "--output",
        str(OUTPUT_PATH),
        "--validate-json",
    ]
    try:
        runpy.run_path(SCRIPT_PATH, run_name="__main__")
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise

    with OUTPUT_PATH.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    default = (payload.get("recommended_routing") or {}).get("default") or {}
    print("EXECUTE_CODE_SNIPPET_OK")
    print(f"status={payload.get('status')}")
    print(f"default={default.get('provider')}/{default.get('model')}")
    print(f"output={OUTPUT_PATH}")
    return 0


main()

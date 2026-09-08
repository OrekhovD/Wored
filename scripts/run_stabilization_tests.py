"""Container acceptance gate: missing dependencies must not produce green QA."""
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[1]
suite = unittest.defaultTestLoader.discover(str(root / "tests" / "stabilization"))
result = unittest.TextTestRunner(verbosity=2).run(suite)
if result.skipped:
    print("QA failed: all stabilization tests must execute; skipped:", result.skipped)
raise SystemExit(0 if result.wasSuccessful() and not result.skipped else 1)

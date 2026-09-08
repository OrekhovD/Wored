"""Fail-fast checks for the stabilization batch; separate component import roots."""
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.chdir(root)
commands = [
    [sys.executable, "scripts/run_stabilization_tests.py"],
    [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "webui/tests"],
    [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "collector/tests/test_predictions.py"],
    [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "chatbot/tests/test_resilience.py"],
    [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "chatbot/tests/test_predictions_handler.py"],
    ["ruff", "check", "--no-cache", "--select", "E9,F821,F822,F823", "webui", "collector", "chatbot"],
    ["mypy", "--follow-imports=skip", "--ignore-missing-imports", "webui/access_control.py", "webui/forecast_queue.py", "webui/forecast_input.py",
     "chatbot/services/market_data.py", "chatbot/services/sim_math.py", "collector/predictions/scoring.py"],
]
if not os.getenv("WORED_TEST_DATABASE_URL"):
    raise SystemExit("Full QA requires the dedicated PostgreSQL database; use docker-compose.qa.yml")
for command in commands:
    print("RUN", " ".join(command), flush=True)
    subprocess.run(command, check=True)

#!/usr/bin/env python3
"""UI-11 scripts/run_ui_acceptance.py — CLI runner for UI acceptance tests.

Starts a fixture server, waits for health, runs pytest, kills in finally.
Handles port-in-use conflicts with UI_QA_PORT_IN_USE exit code.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS_UI = ROOT / "tests" / "ui"
SCRIPTS = ROOT / "scripts"

# Exit codes
EXIT_OK = 0
EXIT_PORT_IN_USE = 78
EXIT_HEALTH_TIMEOUT = 79
EXIT_PYTEST_FAIL = 80


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def wait_for_health(host: str, port: int, timeout: int = 30) -> bool:
    url = f"http://{host}:{port}/__qa__/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def start_server(host: str, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["UI_QA_HOST"] = host
    env["UI_QA_PORT"] = str(port)
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "tests.ui.fixture_app:app",
            "--host", host,
            "--port", str(port),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc


def run_pytest(host: str, port: str, browser: str, artifacts: str) -> int:
    args = [
        sys.executable, "-m", "pytest",
        str(TESTS_UI),
        "-v",
        "--tb=short",
    ]
    env = os.environ.copy()
    env["UI_QA_HOST"] = host
    env["UI_QA_PORT"] = port
    if browser:
        env["UI_QA_BROWSER"] = browser
    if artifacts:
        art_dir = Path(artifacts)
        art_dir.mkdir(parents=True, exist_ok=True)
        env["UI_QA_ARTIFACTS"] = str(art_dir)
        args.extend(["--html=" + str(art_dir / "report.html"),
                       "--junitxml=" + str(art_dir / "junit.xml")])

    proc = subprocess.run(args, cwd=str(ROOT), env=env)
    return proc.returncode


def main():
    parser = argparse.ArgumentParser(description="Run WORED UI acceptance tests")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Server host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=18080,
                        help="Server port (default: 18080)")
    parser.add_argument("--browser", default="chromium",
                        help="Playwright browser (default: chromium)")
    parser.add_argument("--artifacts", default="artifacts/ui-qa",
                        help="Artifacts directory (default: artifacts/ui-qa)")
    parser.add_argument("--no-server", action="store_true",
                        help="Skip server start (use external server)")
    args = parser.parse_args()

    # Check port availability
    if not args.no_server:
        if port_in_use(args.host, args.port):
            # Check if it's already our health endpoint
            if wait_for_health(args.host, args.port, timeout=3):
                print(f"Server already running on {args.host}:{args.port}")
            else:
                print(f"ERROR: Port {args.port} is in use by another process")
                sys.exit(EXIT_PORT_IN_USE)

    server_proc = None
    try:
        if not args.no_server:
            print(f"Starting fixture server on {args.host}:{args.port}...")
            server_proc = start_server(args.host, args.port)
            if not wait_for_health(args.host, args.port, timeout=30):
                print("ERROR: Server health check timed out")
                sys.exit(EXIT_HEALTH_TIMEOUT)
            print(f"Server healthy at http://{args.host}:{args.port}")

        print("Running pytest...")
        rc = run_pytest(args.host, str(args.port), args.browser, args.artifacts)
        if rc != 0:
            sys.exit(EXIT_PYTEST_FAIL)
        sys.exit(EXIT_OK)
    finally:
        if server_proc is not None:
            print("Shutting down fixture server...")
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()
                server_proc.wait()


if __name__ == "__main__":
    main()
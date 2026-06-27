#!/usr/bin/env python3
"""
Hermes Task Worker for WORED
Listens to Redis task queue on the host and executes whitelisted commands safely.
"""

import os
import sys
import json
import time
import re
import subprocess
import logging
import argparse
from pathlib import Path
import redis

# --- LOGGING SETUP ---
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "hermes_task_worker.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("hermes_worker")

# --- CONFIG ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
QUEUE_NAME = "hermes:tasks"
RESULT_KEY_PREFIX = "hermes:result:"
CWD = Path(__file__).resolve().parent.parent

WHITELISTED_COMMANDS = {
    "status": ["docker", "compose", "ps"],
    "git-status": ["git", "status", "--short"],
    "webui-check": [sys.executable, "scripts/smoke_webui.sh"], # fallback to direct run or python wrapper
    "runtime-snapshot": [sys.executable, "scripts/runtime_snapshot.sh"],
    "brief": [
        sys.executable, "scripts/intelligence_brief.py", 
        "--mode", "hourly", 
        "--symbols", "BTCUSDT,ETHUSDT", 
        "--format", "markdown"
    ],
    "risk-position": [
        sys.executable, "scripts/risk_position.py"
    ],
    "signal-explainer": [
        sys.executable, "scripts/signal_explainer.py", 
        "--symbol", "BTCUSDT", 
        "--period", "60min", 
        "--lookback-days", "7", 
        "--format", "markdown"
    ],
    "help": []
}

# --- SECURITY ---
def mask_secrets(text: str) -> str:
    """Mask sensitive tokens, passwords, and keys from logs/outputs."""
    # Mask nvapi keys
    text = re.sub(r"nvapi-[a-zA-Z0-9_-]+", "nvapi-***MASKED***", text)
    # Mask Bearer tokens
    text = re.sub(r"Bearer\s+[a-zA-Z0-9_\-\.]+", "Bearer ***MASKED***", text)
    # Mask inline env secrets
    text = re.sub(
        r"(API_KEY|TOKEN|SECRET|PASSWORD|TELEGRAM_BOT_TOKEN|DASHSCOPE_API_KEY|DEEPSEEK_API_KEY|NVIDIA_API_KEY|AI_GATEWAY_API_KEY)\s*=\s*[^\s\n]+",
        r"\1=***MASKED***",
        text,
        flags=re.IGNORECASE
    )
    return text

def execute_command(command_name: str, args: list = None) -> dict:
    """Safely executes a whitelisted command on the host."""
    if command_name not in WHITELISTED_COMMANDS:
        logger.warning(f"Rejected unallowed command attempt: {command_name}")
        return {
            "status": "REJECTED",
            "command": command_name,
            "result": f"Command '{command_name}' is not in the whitelist.",
            "return_code": -1
        }

    if command_name == "help":
        help_text = "Доступные команды Hermes:\n" + "\n".join(f"- {cmd}" for cmd in WHITELISTED_COMMANDS.keys())
        return {
            "status": "OK",
            "command": "help",
            "result": help_text,
            "return_code": 0
        }

    # Resolve baseline command
    cmd_base = WHITELISTED_COMMANDS[command_name].copy()
    
    # Handle .sh files on Windows
    if len(cmd_base) > 1 and cmd_base[1].endswith(".sh") and os.name == "nt":
        # If it's a shell script and we are on Windows, try running it via bash if available,
        # or convert execution. For smoke_webui.sh we can run curl or python equivalent.
        # Let's check if bash is available
        import shutil
        if shutil.which("bash"):
            cmd_base = ["bash"] + [cmd_base[1]]
        elif shutil.which("git") and Path("C:/Program Files/Git/bin/bash.exe").exists():
            cmd_base = ["C:/Program Files/Git/bin/bash.exe"] + [cmd_base[1]]
        else:
            # Fallback: convert smoke_webui.sh to simple python test
            if "smoke_webui.sh" in cmd_base[1]:
                cmd_base = [sys.executable, "-c", "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/api/health', timeout=5).read().decode('utf-8'))"]
            elif "runtime_snapshot.sh" in cmd_base[1]:
                logger.warning("runtime_snapshot.sh is a shell script, skipping on Windows")
                return {
                    "status": "ERROR",
                    "command": command_name,
                    "result": "Shell script execution not supported on Windows host without Bash.",
                    "return_code": -1
                }

    # Handle argument appending for specific commands that require user input
    cmd_to_run = cmd_base.copy()
    if args:
        # Only append arguments for commands that are designed to accept them
        if command_name in ["risk-position", "signal-explainer", "brief"]:
            cmd_to_run.extend(args)
            logger.info(f"Appending user-provided arguments: {args}")
        else:
            logger.info(f"Arguments provided but ignored for security: {args}")
    else:
        logger.info("No additional arguments provided.")

    logger.info(f"Executing: {' '.join(cmd_to_run)} in {CWD}")
    
    try:
        result = subprocess.run(
            cmd_to_run,
            capture_output=True,
            text=True,
            timeout=90,
            cwd=str(CWD)
        )
        
        output = result.stdout.strip()
        err_output = result.stderr.strip()
        
        combined_output = output
        if err_output:
            combined_output += f"\n--- STDERR ---\n{err_output}"
            
        # Truncate if too long (Telegram limit ~4096, we keep 3500)
        if len(combined_output) > 3500:
            combined_output = combined_output[:3490] + "\n... (output truncated)"

        masked_output = mask_secrets(combined_output)
        
        return {
            "status": "OK" if result.returncode == 0 else "ERROR",
            "command": command_name,
            "result": masked_output,
            "return_code": result.returncode
        }
        
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out: {command_name}")
        return {
            "status": "TIMEOUT",
            "command": command_name,
            "result": "Command execution timed out after 90 seconds.",
            "return_code": -1
        }
    except Exception as exc:
        logger.error(f"Execution failed: {exc}")
        return {
            "status": "ERROR",
            "command": command_name,
            "result": f"Execution failed: {str(exc)}",
            "return_code": -1
        }

# --- WORKER RUNTIME ---
def run_worker(once=False):
    logger.info("Hermes Task Worker started on host.")
    logger.info(f"CWD: {CWD}")
    logger.info(f"Connecting to Redis: {REDIS_URL}")
    
    try:
        r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        r.ping()
        logger.info("Successfully connected to Redis.")
    except Exception as e:
        logger.critical(f"Failed to connect to Redis: {e}")
        sys.exit(1)
        
    while True:
        try:
            logger.info("Waiting for tasks...")
            # brpop returns a tuple: (queue_name, item)
            task_data = r.brpop(QUEUE_NAME, timeout=10)
            
            if task_data:
                _, raw_item = task_data
                logger.info(f"Received task raw item: {raw_item}")
                
                try:
                    task = json.loads(raw_item)
                except Exception as json_err:
                    logger.error(f"Failed to parse task JSON: {json_err}")
                    continue
                
                task_id = task.get("task_id")
                command = task.get("command")
                args = task.get("args", [])
                
                if not task_id or not command:
                    logger.error(f"Invalid task format (missing task_id or command): {task}")
                    continue
                
                logger.info(f"Processing Task ID: {task_id}, Command: {command}")
                
                # Execute Whitelisted Command
                result = execute_command(command, args)
                
                # Save result back to Redis
                result_key = f"{RESULT_KEY_PREFIX}{task_id}"
                r.setex(result_key, 600, json.dumps(result, ensure_ascii=False))
                logger.info(f"Published result to Redis key: {result_key}")
                
            if once:
                logger.info("Once flag set. Stopping worker.")
                break
                
        except redis.ConnectionError:
            logger.error("Redis connection lost. Retrying in 5 seconds...")
            time.sleep(5)
        except KeyboardInterrupt:
            logger.info("Worker stopped by user.")
            break
        except Exception as exc:
            logger.error(f"Unexpected error in worker loop: {exc}")
            time.sleep(2)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hermes Task Worker for WORED")
    parser.add_argument("--once", action="store_true", help="Run once and exit (for testing)")
    parser.add_argument("--test-task", type=str, help="Simulate a task execution on the spot (e.g. status)")
    
    args = parser.parse_args()
    
    if args.test_task:
        logger.info(f"Running Dry Test for task: {args.test_task}")
        res = execute_command(args.test_task)
        print("\n=== DRY TEST RESULT ===")
        print(json.dumps(res, indent=2, ensure_ascii=False))
        sys.exit(0)
        
    run_worker(once=args.once)

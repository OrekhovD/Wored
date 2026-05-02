# hermes_bridge.py

from typing import Dict, Any, Optional
import subprocess
import json
import os
import re
import shutil
import time
from pathlib import Path

# --- CONFIG ---
WHITELISTED_COMMANDS = [
    "status",
    "brief",
    "risk-position",
    "signal-explainer",
    "webui-check",
    "runtime-snapshot",
    "git-status",
    "help",
]

# --- UTILS ---
def mask_secrets(text: str) -> str:
    """Mask secrets before sending to Telegram."""
    text = re.sub(r"nvapi-[a-zA-Z0-9_-]+", "***MASKED***", text)
    text = re.sub(r"Bearer [a-zA-Z0-9_-]+", "Bearer ***MASKED***", text)
    text = re.sub(r"(API_KEY|TOKEN|SECRET|PASSWORD|TELEGRAM_BOT_TOKEN)=.*?($|\s|\n)", r"\1=***MASKED***\2", text)
    return text

# --- COMMAND MAP ---
INTENT_TO_COMMAND = {
    "статус": "status",
    "проверь систему": "status",
    "дай brief": "brief",
    "сводка": "brief",
    "расчёт риска": "risk-position",
    "проверь webui": "webui-check",
    "что изменено": "git-status",
    "помощь": "help",
    "help": "help",
    "запрос": "help",
}

# --- MAIN ---
def route_hermes_intent(intent: str) -> Dict[str, Any]:
    """Map natural language intent to Hermes command."""
    intent = intent.strip().lower()

    # Exact match
    if intent in INTENT_TO_COMMAND:
        return {"command": INTENT_TO_COMMAND[intent], "args": []}

    # Fallback: /task parsing
    if intent.startswith("/task "):
        task = intent[6:].strip()
        if task == "status" or "статус" in task:
            return {"command": "status", "args": []}
        if "brief" in task or "сводка" in task:
            return {"command": "brief", "args": []}
        if "риск" in task or "risk" in task:
            return {"command": "risk-position", "args": []}
        if "webui" in task or "веб" in task:
            return {"command": "webui-check", "args": []}
        if "git" in task or "изменено" in task:
            return {"command": "git-status", "args": []}
        if "snapshot" in task or "снэпшот" in task:
            return {"command": "runtime-snapshot", "args": []}
        if "помощь" in task or "help" in task:
            return {"command": "help", "args": []}

    # Default fallback
    return {"command": "help", "args": []}

# --- EXECUTE ---
def execute_command(command_name: str, args: list = None) -> Dict[str, Any]:
    """Execute whitelisted Hermes command safely."""
    if not args:
        args = []

    # Whitelist check
    if command_name not in WHITELISTED_COMMANDS:
        return {
            "status": "REJECTED",
            "reason": f"command '{command_name}' not allowed",
            "command": command_name,
            "result": "",
        }

    hermes_cli = os.getenv("HERMES_CLI", "hermes")
    hermes_workdir = Path(os.getenv("HERMES_WORKDIR", "/mnt/d/WORED"))
    if not hermes_workdir.exists():
        return {
            "status": "ERROR",
            "command": command_name,
            "result": f"Hermes workdir is unavailable: {hermes_workdir}",
            "return_code": -1,
        }
    if shutil.which(hermes_cli) is None:
        return {
            "status": "ERROR",
            "command": command_name,
            "result": f"Hermes CLI is unavailable on PATH: {hermes_cli}",
            "return_code": -1,
        }

    try:
        # Build full command
        cmd = [hermes_cli, "quick", command_name] + args
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(hermes_workdir)
        )

        output = result.stdout.strip() or result.stderr.strip()
        if len(output) > 3500:
            output = output[:3490] + "... (truncated)"

        return {
            "status": "OK" if result.returncode == 0 else "ERROR",
            "command": command_name,
            "result": mask_secrets(output),
            "return_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "TIMEOUT",
            "command": command_name,
            "result": "Command timed out after 120s",
            "return_code": -1,
        }
    except Exception as exc:
        return {
            "status": "ERROR",
            "command": command_name,
            "result": f"Execution failed: {str(exc)}",
            "return_code": -1,
        }

# --- FORMAT RESPONSE FOR TELEGRAM ---
def format_telegram_response(task_id: str, data: Dict[str, Any]) -> str:
    """Format response for Telegram with #HERMES_TASK header."""
    lines = [
        f"#HERMES_TASK_{task_id}",
        f"Status: {data['status']}",
        f"Command: {data.get('command', 'N/A')}",
    ]
    if "reason" in data:
        lines.append(f"Reason: {data['reason']}")
    if "result" in data and data["result"]:
        lines.append("Result:")
        lines.append(data["result"])
    else:
        lines.append("Result: (no output)")
    return "\n".join(lines)

if __name__ == "__main__":
    # For testing only
    print(format_telegram_response(
        "20260501_174500",
        execute_command("brief")
    ))

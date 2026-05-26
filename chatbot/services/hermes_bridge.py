# hermes_bridge.py

from typing import Dict, Any, Optional
import json
import os
import re
import time
import asyncio
from pathlib import Path
from storage.redis_client import get_redis

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

# --- EXECUTE (REDIS QUEUE BRIDGE) ---
async def execute_command(command_name: str, args: list = None) -> Dict[str, Any]:
    """Sends command to Redis queue and waits for worker on host to complete it."""
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

    # Generate task_id
    task_id = f"task_{int(time.time())}_{os.urandom(3).hex()}"
    task = {
        "task_id": task_id,
        "command": command_name,
        "args": args,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    try:
        r = get_redis()
        
        # Enqueue task
        await r.lpush("hermes:tasks", json.dumps(task, ensure_ascii=False))
        
        # Poll for result
        result_key = f"hermes:result:{task_id}"
        timeout = 60  # 60 seconds
        poll_interval = 0.5
        elapsed = 0.0
        
        while elapsed < timeout:
            raw_result = await r.get(result_key)
            if raw_result:
                result_data = json.loads(raw_result)
                # Cleanup result key
                await r.delete(result_key)
                return result_data
            
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            
        # Timeout reached
        return {
            "status": "TIMEOUT",
            "command": command_name,
            "result": "Превышено время ожидания ответа от хост-воркера (timeout 60s). Убедитесь, что воркер запущен на хосте.",
            "return_code": -1,
        }
        
    except Exception as exc:
        return {
            "status": "ERROR",
            "command": command_name,
            "result": f"Ошибка взаимодействия с Redis: {str(exc)}",
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

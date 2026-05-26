from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command
import os
import time
import logging

log = logging.getLogger(__name__)

# --- CONFIG ---
ADMIN_TELEGRAM_ID = int(os.getenv("HERMES_ADMIN_TELEGRAM_ID") or os.getenv("TELEGRAM_ADMIN_ID", "0"))

HERMES_RESERVED_COMMANDS = {
    "hermes", "hermes_on", "hermes_off", "hermes_status", "hermes_help", "task"
}

# --- ROUTER ---
router = Router(name="hermes_admin")

@router.message(F.from_user.id == ADMIN_TELEGRAM_ID, Command(*HERMES_RESERVED_COMMANDS))
async def handle_hermes_command(message: Message):
    # Route via bridge
    try:
        from services.hermes_bridge import route_hermes_intent, execute_command, format_telegram_response

        intent = message.text.strip()
        cmd_data = route_hermes_intent(intent)

        result = await execute_command(cmd_data["command"], cmd_data.get("args", []))

        task_id = f"{time.strftime('%Y%m%d_%H%M%S')}_" + str(hash(intent))[:6].replace('-', '')
        response = format_telegram_response(task_id, result)

        await message.answer(response)
    except Exception as exc:
        log.error(f"Hermes error: {exc}")
        await message.answer(f"Hermes error: {str(exc)}")

@router.message(F.from_user.id == ADMIN_TELEGRAM_ID, ~F.text.startswith('/'))
async def handle_admin_text(message: Message):
    # Admin text routes to Hermes.
    try:
        from services.hermes_bridge import route_hermes_intent, execute_command, format_telegram_response

        intent = message.text.strip()
        cmd_data = route_hermes_intent(intent)

        result = await execute_command(cmd_data["command"], cmd_data.get("args", []))

        task_id = f"{time.strftime('%Y%m%d_%H%M%S')}_" + str(hash(intent))[:6].replace('-', '')
        response = format_telegram_response(task_id, result)

        await message.answer(response)
    except Exception as exc:
        log.error(f"Hermes error: {exc}")
        await message.answer(f"Hermes error: {str(exc)}")

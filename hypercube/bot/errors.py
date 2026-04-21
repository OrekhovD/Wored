"""Bot error formatting."""
from __future__ import annotations


def format_bot_error(error: Exception, is_admin: bool = False) -> str:
    if is_admin:
        return f"❌ **Error (admin view):**\n\n```\n{str(error)[:1000]}\n```"
    return "❌ Произошла ошибка при обработке запроса. Попробуйте позже."

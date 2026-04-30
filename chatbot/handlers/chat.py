import re
import logging
from aiogram import Router, F
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest
from ai.router import route_request

log = logging.getLogger(__name__)
router = Router()

def sanitize_html(text: str) -> str:
    """Fix common broken HTML from AI responses for Telegram."""
    # Allowed Telegram HTML tags
    allowed = {'b', 'i', 'u', 's', 'code', 'pre', 'a', 'blockquote'}
    
    # Find all opening tags
    for tag in allowed:
        # Count opens and closes
        opens = len(re.findall(rf'<{tag}(?:\s[^>]*)?>', text, re.IGNORECASE))
        closes = len(re.findall(rf'</{tag}>', text, re.IGNORECASE))
        # Add missing close tags
        for _ in range(opens - closes):
            text += f'</{tag}>'
    
    return text

def strip_html(text: str) -> str:
    """Remove all HTML tags as a last resort."""
    return re.sub(r'<[^>]+>', '', text)

@router.message(F.text)
async def cmd_chat(message: Message):
    wait_msg = await message.answer("🤔 <i>Анализирую данные...</i>")
    try:
        reply = await route_request(message.text)
        
        # Try sending with sanitized HTML first
        try:
            await wait_msg.edit_text(sanitize_html(reply))
        except TelegramBadRequest:
            # If HTML is still broken, strip all tags and send plain
            log.warning("HTML sanitize failed, sending as plain text")
            await wait_msg.edit_text(strip_html(reply), parse_mode=None)
            
    except Exception as e:
        log.error(f"AI call error: {e}")
        await wait_msg.edit_text(f"❌ Ошибка вызова AI: {e}", parse_mode=None)


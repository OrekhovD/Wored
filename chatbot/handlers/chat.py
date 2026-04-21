from aiogram import Router, F
from aiogram.types import Message
from ai.router import route_request

router = Router()

@router.message(F.text)
async def cmd_chat(message: Message):
    wait_msg = await message.answer("🤔 <i>Анализирую данные...</i>")
    try:
        reply = await route_request(message.text)
        await wait_msg.edit_text(reply)
    except Exception as e:
        await wait_msg.edit_text(f"❌ Ошибка вызова AI: {e}")

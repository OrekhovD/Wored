import os
import pytest
import json
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram.types import Message, Chat, User
from handlers.hermes_admin import handle_hermes_command, handle_admin_text

# --- FIXTURES ---
@pytest.fixture(autouse=True)
def setup_env():
    # Set mock Admin ID for testing
    with patch.dict(os.environ, {"TELEGRAM_ADMIN_ID": "12345", "HERMES_CHATBOT_GATEWAY_ENABLED": "true"}):
        yield

@pytest.mark.asyncio
async def test_admin_command_routes_to_redis_bridge():
    """Тест проверяет, что команда /task status от админа отправляется в Redis-мост и возвращает ответ."""
    chat = Chat(id=12345, type="private")
    user = User(id=12345, is_bot=False, first_name="Admin")
    message = Message(
        message_id=1,
        date=datetime.now(),
        chat=chat,
        from_user=user,
        text="/task status"
    )

    # Mock Redis responses to simulate host worker completing the task
    mock_redis = AsyncMock()
    mock_redis.lpush = AsyncMock()
    mock_result = {
        "status": "OK",
        "command": "status",
        "result": "htx_trading_bot_chatbot Up\nhtx_trading_bot_redis Up",
        "return_code": 0
    }
    mock_redis.get = AsyncMock(return_value=json.dumps(mock_result))
    mock_redis.delete = AsyncMock()

    # PATCH the imported get_redis reference inside services.hermes_bridge!
    with patch("services.hermes_bridge.get_redis", return_value=mock_redis), \
         patch.object(Message, "answer", new_callable=AsyncMock) as mock_answer:
         
        await handle_hermes_command(message)
        
        # Verify message.answer (mock_answer) was called
        assert mock_answer.called
        response_text = mock_answer.call_args[0][0]
        
        # Verify format of Telegram response
        assert "#HERMES_TASK" in response_text
        assert "Status: OK" in response_text
        assert "Command: status" in response_text
        assert "Result:" in response_text
        assert "htx_trading_bot_chatbot Up" in response_text

@pytest.mark.asyncio
async def test_admin_text_routes_to_redis_bridge():
    """Тест проверяет, что обычный текст от админа (например, 'сводка') перехватывается и ведет к запуску команды brief."""
    chat = Chat(id=12345, type="private")
    user = User(id=12345, is_bot=False, first_name="Admin")
    message = Message(
        message_id=2,
        date=datetime.now(),
        chat=chat,
        from_user=user,
        text="сводка"
    )
    
    mock_redis = AsyncMock()
    mock_redis.lpush = AsyncMock()
    mock_result = {
        "status": "OK",
        "command": "brief",
        "result": "Рыночная сводка: BTC 100k, ETH 4k",
        "return_code": 0
    }
    mock_redis.get = AsyncMock(return_value=json.dumps(mock_result))
    mock_redis.delete = AsyncMock()

    # PATCH the imported get_redis reference inside services.hermes_bridge!
    with patch("services.hermes_bridge.get_redis", return_value=mock_redis), \
         patch.object(Message, "answer", new_callable=AsyncMock) as mock_answer:
         
        await handle_admin_text(message)
        
        assert mock_answer.called
        response_text = mock_answer.call_args[0][0]
        
        assert "#HERMES_TASK" in response_text
        assert "Command: brief" in response_text
        assert "Рыночная сводка:" in response_text

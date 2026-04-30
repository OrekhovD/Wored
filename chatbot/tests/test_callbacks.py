"""Тесты callback handlers для длинных AI-операций."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_cb_analytics_acknowledges_callback_before_ai_call():
    from handlers.callbacks import cb_analytics

    message = SimpleNamespace(edit_text=AsyncMock())
    call = SimpleNamespace(
        data="analytics:btcusdt",
        answer=AsyncMock(),
        message=message,
    )
    redis = AsyncMock()
    redis.get.return_value = '{"price": 100000.5, "change_pct": 2.5, "volume": 1000}'

    async def fake_route_request(prompt, context=None):
        assert call.answer.await_count == 1
        return "<b>AI ответ</b>"

    with patch("handlers.callbacks.get_redis", return_value=redis), \
         patch("handlers.callbacks.route_request", side_effect=fake_route_request):
        await cb_analytics(call)

    call.answer.assert_awaited_once()
    assert message.edit_text.await_count >= 2


@pytest.mark.asyncio
async def test_cb_second_opinion_acknowledges_callback_before_minimax_call():
    from handlers.callbacks import cb_second_opinion

    wait_message = SimpleNamespace(edit_text=AsyncMock())
    message = SimpleNamespace(
        html_text="<b>Исходный анализ</b>",
        answer=AsyncMock(return_value=wait_message),
    )
    call = SimpleNamespace(
        data="second_opinion:btcusdt",
        answer=AsyncMock(),
        message=message,
    )

    async def fake_second_opinion(preferred, prompt_skill, prompt, context):
        assert preferred == "minimax"
        assert call.answer.await_count == 1
        return "<b>Второе мнение</b>"

    with patch("ai.router._call_with_fallback", side_effect=fake_second_opinion):
        await cb_second_opinion(call)

    call.answer.assert_awaited_once()
    message.answer.assert_awaited_once()
    wait_message.edit_text.assert_awaited()

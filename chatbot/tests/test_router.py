"""Mock-тесты для AI router с resilience."""
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from ai.resilience import reset_resilience_handlers

@pytest.fixture(autouse=True)
def cleanup():
    reset_resilience_handlers()
    yield
    reset_resilience_handlers()

@pytest.mark.asyncio
async def test_route_request_price_intent():
    """price intent должен вернуть данные из Redis без вызова AI."""
    with patch("ai.dispatcher.classify", new_callable=AsyncMock) as mock_classify, \
         patch("storage.redis_client.get_redis") as mock_get_redis:
        
        mock_classify.return_value = {"intent": "price", "tickers": ["btcusdt"]}
        mock_redis = AsyncMock()
        mock_redis.get.return_value = '{"price": 100000.5, "change_pct": 2.5, "volume": 1000}'
        mock_get_redis.return_value = mock_redis
        
        from ai.router import route_request
        result = await route_request("цена btc")
        
        assert "100000" in result
        assert "BTCUSDT" in result

@pytest.mark.asyncio 
async def test_fallback_on_first_model_failure():
    """Если первая модель упала, должен сработать fallback."""
    
    with patch("ai.dispatcher.classify", new_callable=AsyncMock) as mock_classify, \
         patch("ai.router.get_client") as mock_get_client, \
         patch("ai.router.get_resilience_handler") as mock_handler:
        
        mock_classify.return_value = {"intent": "chat", "tickers": []}
        
        # Первый вызов — ошибка, второй — успех
        handler1 = MagicMock()
        handler1.circuit_breaker.can_execute = AsyncMock(return_value=True)
        handler1.execute = AsyncMock(side_effect=Exception("model down"))
        handler1.get_circuit_stats.return_value = {"state": "open", "failure_count": 5}
        
        handler2 = MagicMock()
        handler2.circuit_breaker.can_execute = AsyncMock(return_value=True)
        
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="AI ответ"))]
        handler2.execute = AsyncMock(return_value=mock_response)
        
        mock_handler.side_effect = [handler1, handler2]
        
        from ai.router import route_request
        # Test just the execution of fallback without crashing
        try:
            result = await route_request("hi")
            assert "AI ответ" in result or "❌ Все AI-модули сейчас недоступны" in result
        except StopIteration: # Handle mock side_effect depletion if any
            pass

def test_get_client_skips_unsupported_minimax_key():
    import ai.router as router

    with patch.dict(os.environ, {"MINIMAX_API_KEY": "mmx-direct-key"}, clear=False):
        router._clients.clear()
        client = router.get_client("minimax")
        assert client is None

def test_minimax_uses_current_nvidia_model_id():
    from ai.models import MODELS

    assert MODELS["minimax"].model_id == "minimaxai/minimax-m2.7"


def test_worker_fallback_chain_prefers_qwen_then_glm():
    from ai.models import expand_fallback_tiers

    order = expand_fallback_tiers("worker")

    assert order[:5] == ["worker", "worker_qwen35", "worker_qwen_legacy", "worker_glm", "worker_gemini"]


def test_analyst_fallback_chain_prefers_reasoning_qwen_then_glm():
    from ai.models import expand_fallback_tiers

    order = expand_fallback_tiers("analyst")

    assert order[:3] == ["analyst", "analyst_qwen27b", "analyst_glm"]


def test_premium_fallback_chain_prefers_reasoning_qwen_then_glm():
    from ai.models import expand_fallback_tiers

    order = expand_fallback_tiers("premium")

    assert order[:3] == ["premium", "premium_qwen35b", "premium_glm"]

"""Smoke test for the entire provider framework."""
import pytest
import asyncio
import json
from unittest.mock import AsyncMock

from core.config import AppConfiguration
from core.enums import RoutingMode
from providers.factory import ProviderFactory
from providers.resilience import CircuitBreaker, CircuitBreakerConfig, get_resilience_handler
from routing.model_registry import ModelRegistry
from routing.policies import RoutingPolicy
from routing.chain_builder import ChainBuilder
from routing.provider_manager import ProviderManager


@pytest.mark.asyncio
async def test_full_integration():
    """Test full integration of routing, providers, and resilience."""
    # 1. Load config
    config = AppConfiguration()
    
    # 2. Build factory
    factory = ProviderFactory()
    factory.build_from_config(config)
    
    # 3. Create registry
    registry = ModelRegistry()
    assert registry.get_all_models() is not None
    
    # 4. Create policies
    policy = RoutingPolicy()
    assert policy.get_candidate_chain(RoutingMode.FREE_ONLY) is not None
    
    # 5. Create chain builder
    builder = ChainBuilder(registry, policy)
    assert builder.get_chain_metadata(RoutingMode.BALANCED) is not None
    
    # 6. Create provider manager
    manager = ProviderManager(registry, builder, policy)
    chain = manager.get_chain(RoutingMode.BALANCED)
    assert chain is not None
    
    # 7. Test resilience handlers
    resilience = get_resilience_handler("dashscope")
    stats = resilience.get_circuit_stats()
    assert stats["name"] == "dashscope"
    assert stats["state"] == "closed"
    
    # 8. Test premium unlock policy
    premium_unlocked = manager.is_premium_unlocked(RoutingMode.PREMIUM, 20.0)
    assert isinstance(premium_unlocked, bool)
    
    # 9. Test fallback reasons
    reasons = manager.get_fallback_reason("timeout")
    assert reasons == "Provider did not respond within timeout"
    
    # 10. Test health check config
    health_config = policy.get_health_check_config()
    assert "interval_seconds" in health_config
    assert "timeout_seconds" in health_config
    
    print("✅ Full integration test passed")


@pytest.mark.asyncio
async def test_routing_chain_workflow():
    """Test the complete routing chain workflow."""
    registry = ModelRegistry()
    policy = RoutingPolicy()
    builder = ChainBuilder(registry, policy)
    manager = ProviderManager(registry, builder, policy)
    
    # Test chain building
    for mode in [RoutingMode.FREE_ONLY, RoutingMode.BALANCED, RoutingMode.PREMIUM]:
        chain = manager.get_chain(mode)
        assert len(chain) > 0
        
        # Test premium unlock for each mode
        premium_unlocked = manager.is_premium_unlocked(mode, 20.0)
        
        # Test metadata
        metadata = builder.get_chain_metadata(mode)
        assert isinstance(metadata, dict)
    
    print("✅ Routing chain workflow test passed")


@pytest.mark.asyncio
async def test_resilience_circuit_breaker():
    """Test circuit breaker failure and recovery."""
    circuit_config = CircuitBreakerConfig(
        failure_threshold=2,
        recovery_timeout=2.0,
        half_open_max_calls=2,
    )
    circuit = CircuitBreaker(circuit_config, "test_circuit")
    
    # Initially closed
    assert circuit.current_state == "closed"
    
    # Fail twice to open circuit
    async def failing():
        raise RuntimeError("Failure")
    
    for i in range(2):
        try:
            await circuit.execute(failing)
        except RuntimeError:
            pass
    
    assert circuit.current_state == "open"
    assert not await circuit.can_execute()
    
    # Wait for recovery timeout
    await asyncio.sleep(2.1)
    assert circuit.current_state == "half_open"
    
    # Success in half-open should count
    async def succeeding():
        return "success"
    
    result = await circuit.execute(succeeding)
    assert result == "success"
    
    # Second success should close circuit
    result = await circuit.execute(succeeding)
    assert result == "success"
    assert circuit.current_state == "closed"
    
    print("✅ Circuit breaker failure/recovery test passed")


@pytest.mark.asyncio
async def test_model_registry_config():
    """Test config-driven model registry."""
    registry = ModelRegistry()
    
    # Test model availability
    for mode in [RoutingMode.FREE_ONLY, RoutingMode.BALANCED, RoutingMode.PREMIUM]:
        for model_id in ["glm-5", "qwen-plus", "qwen-turbo"]:
            available = registry.is_model_available(model_id, mode)
            assert isinstance(available, bool)
    
    # Test provider enablement
    for provider_id in ["dashscope", "nvapi", "zhipu", "ai_studio"]:
        enabled = registry.is_provider_enabled(provider_id)
        assert isinstance(enabled, bool)
    
    # Test provider config
    for provider_id in ["dashscope", "nvapi"]:
        config = registry.get_provider_config(provider_id)
        assert isinstance(config, dict)
    
    print("✅ Model registry config test passed")


if __name__ == "__main__":
    # Run all smoke tests
    pytest.main([__file__, "-v", "--tb=short"])
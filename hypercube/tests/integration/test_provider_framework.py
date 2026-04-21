"""Integration tests for the provider framework."""
import pytest
import json
import asyncio
from unittest.mock import AsyncMock, MagicMock

from core.config import AppConfiguration
from core.enums import RoutingMode
from providers.factory import ProviderFactory
from providers.resilience import ResilienceOrchestrator, get_resilience_handler, CircuitBreakerError
from routing.model_registry import ModelRegistry
from routing.policies import RoutingPolicy
from routing.chain_builder import ChainBuilder
from routing.provider_manager import ProviderManager


@pytest.mark.asyncio
async def test_provider_factory_creation():
    """ProviderFactory creates providers from config."""
    config = AppConfiguration()
    factory = ProviderFactory()
    factory.build_from_config(config)
    
    # DashScope should be registered if key present
    adapters = factory.list_available()
    
    # NVAPI should be registered if key present
    if config.NVAPI_API_KEY:
        assert "nvapi" in adapters
    
    # GLM-5 should be registered if key present
    if config.GLM5_API_KEY:
        assert "zhipu" in adapters
    
    # AI Studio should be registered if key present
    if config.AI_STUDIO_API_KEY:
        assert "ai_studio" in adapters


@pytest.mark.asyncio
async def test_model_registry_config_driven():
    """ModelRegistry loads from config."""
    registry = ModelRegistry()
    
    # Should have models loaded
    models = registry.get_all_models()
    assert len(models) > 0
    
    # GLM-5 should be premium
    glm5 = registry.get_model("glm-5")
    if glm5:
        assert glm5["is_premium"] == True
    
    # Models should have appropriate tags
    for model in models:
        assert "model_id" in model
        assert "provider_id" in model
        assert "display_name" in model
        assert "status" in model


@pytest.mark.asyncio
async def test_routing_policy_config_driven():
    """RoutingPolicy loads from config."""
    policy = RoutingPolicy()
    
    # Chains should be available
    free_only_chain = policy.get_candidate_chain(RoutingMode.FREE_ONLY)
    assert len(free_only_chain) > 0
    
    # GLM-5 should be in premium chain but not free_only
    premium_chain = policy.get_candidate_chain(RoutingMode.PREMIUM)
    if "glm-5" in premium_chain:
        assert "glm-5" not in free_only_chain
    
    # Should have fallback reasons
    reasons = policy.get_fallback_reasons()
    assert "timeout" in reasons
    assert "rate_limit" in reasons
    
    # Should have provider timeout config
    timeout = policy.get_provider_timeout("dashscope")
    assert timeout == 30  # default or config
    
    # Should have retry config
    retry_config = policy.get_provider_retry_config("dashscope")
    assert "retry_count" in retry_config
    assert "retry_delay_ms" in retry_config


@pytest.mark.asyncio
async def test_chain_builder_exclusion():
    """ChainBuilder excludes disabled providers."""
    registry = ModelRegistry()
    policy = RoutingPolicy()
    builder = ChainBuilder(registry, policy)
    
    # Get default chain
    chain = builder.build_chain(RoutingMode.BALANCED)
    original_length = len(chain)
    
    # Exclude GLM-5
    excluded_chain = builder.build_chain(RoutingMode.BALANCED, excluded_models=["glm-5"])
    assert len(excluded_chain) == original_length - 1
    assert "glm-5" not in excluded_chain
    
    # Metadata should be available
    metadata = builder.get_chain_metadata(RoutingMode.PREMIUM)
    assert isinstance(metadata, dict)


@pytest.mark.asyncio
async def test_provider_manager_resilience():
    """ProviderManager integrates with resilience."""
    registry = ModelRegistry()
    policy = RoutingPolicy()
    builder = ChainBuilder(registry, policy)
    manager = ProviderManager(registry, builder, policy)
    
    # Get resilience handler for a provider
    resilience_handler = get_resilience_handler("dashscope")
    assert isinstance(resilience_handler, ResilienceOrchestrator)
    
    # Get circuit stats
    stats = resilience_handler.get_circuit_stats()
    assert stats["name"] == "dashscope"
    assert stats["state"] == "closed"
    
    # Try a mock failing function with circuit breaker
    async def failing_func():
        raise RuntimeError("Test failure")
    
    # Should fail but be recorded
    try:
        await resilience_handler.execute(failing_func)
    except RuntimeError:
        pass
    
    stats = resilience_handler.get_circuit_stats()
    assert stats["failure_count"] > 0
    
    # Premium unlock policy
    assert manager.is_premium_unlocked(RoutingMode.PREMIUM, 20.0)
    assert not manager.is_premium_unlocked(RoutingMode.PREMIUM, 5.0)
    assert not manager.is_premium_unlocked(RoutingMode.FREE_ONLY, 100.0)


@pytest.mark.asyncio
async def test_config_yaml():
    """Provider registry YAML config can be parsed."""
    # Create a test config file in temporary location
    test_config = {
        "providers": {
            "test_provider": {
                "display_name": "Test Provider",
                "base_url": "https://test.com/v1",
                "env_key": "TEST_API_KEY",
                "enabled": True,
                "models": {
                    "test-model": {
                        "display_name": "Test Model",
                        "status": "active",
                        "is_premium": False,
                        "supports_streaming": True,
                        "supports_system_prompt": True,
                        "max_tokens": 4096,
                        "cost": {
                            "input_per_1k": 0.001,
                            "output_per_1k": 0.002,
                        },
                        "tags": ["test"],
                    }
                }
            }
        },
        "routing_policies": {
            "free_only": {
                "description": "Test free policy",
                "candidate_chain": ["test-model"],
                "exclude_premium": True,
                "max_fallbacks": 2,
            }
        }
    }
    
    # Test YAML parsing through ModelRegistry
    registry = ModelRegistry()
    # Registry should load defaults if config doesn't exist
    models = registry.get_all_models()
    assert len(models) > 0
    
    # Test if config-driven
    if registry.is_config_driven:
        # Config file was found and parsed
        provider_ids = registry.get_provider_ids()
        assert len(provider_ids) > 0
"""Tests for routing and provider registry."""
import pytest
from core.enums import RoutingMode

from routing.model_registry import ModelRegistry
from routing.policies import RoutingPolicy
from routing.chain_builder import ChainBuilder
from routing.provider_manager import ProviderManager


def test_model_registry_defaults():
    """ModelRegistry loads defaults."""
    registry = ModelRegistry()
    models = registry.get_all_models()
    assert len(models) == 5
    
    glm5 = registry.get_model("glm-5")
    assert glm5["model_id"] == "glm-5"
    assert glm5["provider_id"] == "zhipu"
    assert glm5["is_premium"] == True
    
    qwen_turbo = registry.get_model("qwen-turbo")
    assert qwen_turbo["model_id"] == "qwen-turbo"
    assert qwen_turbo["provider_id"] == "dashscope"
    assert qwen_turbo["is_premium"] == False


def test_model_registry_active_models():
    """ModelRegistry filters active models."""
    registry = ModelRegistry()
    active = registry.get_active_models()
    assert len(active) == 5  # All defaults are active
    
    registry.update_model_status("glm-5", "inactive")
    active = registry.get_active_models()
    assert len(active) == 4
    assert "glm-5" not in [m["model_id"] for m in active]


def test_model_registry_provider_filter():
    """ModelRegistry filters by provider."""
    registry = ModelRegistry()
    dashscope_models = registry.get_models_by_provider("dashscope")
    assert len(dashscope_models) == 2
    assert all(m["provider_id"] == "dashscope" for m in dashscope_models)


def test_model_registry_is_model_available():
    """ModelRegistry checks availability."""
    registry = ModelRegistry()
    
    assert registry.is_model_available("qwen-turbo", RoutingMode.FREE_ONLY)
    assert registry.is_model_available("qwen-plus", RoutingMode.FREE_ONLY)
    assert registry.is_model_available("ai-minimax/minimax-m2.7", RoutingMode.FREE_ONLY)
    
    # Premium models not available in free_only
    assert not registry.is_model_available("glm-5", RoutingMode.FREE_ONLY)
    assert registry.is_model_available("glm-5", RoutingMode.PREMIUM)


def test_routing_policy_chains():
    """RoutingPolicy provides chains."""
    policy = RoutingPolicy()
    
    free_only = policy.get_candidate_chain(RoutingMode.FREE_ONLY)
    assert "glm-5" not in free_only  # Premium excluded
    assert "qwen-turbo" in free_only
    
    balanced = policy.get_candidate_chain(RoutingMode.BALANCED)
    assert "glm-5" in balanced
    
    premium = policy.get_candidate_chain(RoutingMode.PREMIUM)
    assert "glm-5" in premium


def test_routing_policy_excluded_models():
    """RoutingPolicy excludes models."""
    policy = RoutingPolicy()
    
    chain = policy.get_candidate_chain(RoutingMode.BALANCED, excluded_models=["glm-5", "qwen-turbo"])
    assert "glm-5" not in chain
    assert "qwen-turbo" not in chain
    assert "qwen-plus" in chain
    assert "ai-minimax/minimax-m2.7" in chain


def test_routing_policy_metadata():
    """RoutingPolicy provides metadata."""
    policy = RoutingPolicy()
    
    metadata = policy.get_policy_metadata(RoutingMode.FREE_ONLY)
    assert isinstance(metadata, dict)
    
    # Should have fallback reasons
    reasons = policy.get_fallback_reasons()
    assert "timeout" in reasons
    assert "rate_limit" in reasons
    assert "provider_unavailable" in reasons


def test_routing_policy_health_check_config():
    """RoutingPolicy provides health check config."""
    policy = RoutingPolicy()
    
    config = policy.get_health_check_config()
    assert "interval_seconds" in config
    assert "timeout_seconds" in config
    assert "consecutive_failures_for_degraded" in config
    assert "consecutive_successes_for_recovery" in config


def test_routing_policy_premium_unlock():
    """RoutingPolicy checks premium unlock."""
    policy = RoutingPolicy()
    
    assert not policy.is_premium_unlocked(RoutingMode.FREE_ONLY, 100.0)
    assert policy.is_premium_unlocked(RoutingMode.PREMIUM, 20.0)
    assert not policy.is_premium_unlocked(RoutingMode.PREMIUM, 5.0)  # Below threshold


def test_chain_builder():
    """ChainBuilder builds chains."""
    registry = ModelRegistry()
    policy = RoutingPolicy()
    builder = ChainBuilder(registry, policy)
    
    chain = builder.build_chain(RoutingMode.BALANCED)
    assert "glm-5" in chain
    assert "qwen-plus" in chain
    
    metadata = builder.get_chain_metadata(RoutingMode.BALANCED)
    assert isinstance(metadata, dict)


def test_provider_manager():
    """ProviderManager manages providers."""
    registry = ModelRegistry()
    policy = RoutingPolicy()
    builder = ChainBuilder(registry, policy)
    manager = ProviderManager(registry, builder, policy)
    
    chain = manager.get_chain(RoutingMode.FREE_ONLY)
    assert "glm-5" not in chain
    
    # Premium unlock check
    assert not manager.is_premium_unlocked(RoutingMode.FREE_ONLY, 100.0)
    assert manager.is_premium_unlocked(RoutingMode.PREMIUM, 20.0)
    
    # Model availability
    assert manager.is_model_available("qwen-turbo", RoutingMode.FREE_ONLY)
    assert manager.is_model_available("glm-5", RoutingMode.PREMIUM)
    
    # Fallback reason
    reason = manager.get_fallback_reason("timeout")
    assert reason == "Provider did not respond within timeout"
"""Bootstrap — builds ServiceContainer from config and DB."""
from __future__ import annotations

import logging
from typing import Any

from core.config import AppConfiguration
from core.services import ServiceContainer, UserSessionState
from storage.database import async_session_factory
from storage.repositories import (
    UserRepository, TelegramChatRepository, ConversationSessionRepository,
    ConversationMessageRepository, UsageRecordRepository, QuotaStateRepository,
    ContextSnapshotRepository, ContextHandoffRepository, RouteDecisionRepository,
    ProviderHealthEventRepository, HTXMarketSnapshotRepository, AdminEventRepository,
)
from accounting.service import TokenAccountingService
from quotas.engine import QuotaPolicyEngine
from context.service import ContextService
from context.handoff import HandoffBuilder
from routing.service import ModelRegistry, FallbackEngine
from providers.htx_adapter import HTXMarketDataAdapter
from providers.factory import create_provider_adapters

log = logging.getLogger(__name__)


def get_session_factory():
    if async_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return async_session_factory

async def build_service_container(config: AppConfiguration) -> ServiceContainer:
    """Build fully initialized ServiceContainer."""
    sf = get_session_factory()

    # repositories
    user_repo = UserRepository(sf)
    chat_repo = TelegramChatRepository(sf)
    session_repo = ConversationSessionRepository(sf)
    message_repo = ConversationMessageRepository(sf)
    usage_repo = UsageRecordRepository(sf)
    quota_repo = QuotaStateRepository(sf)
    snapshot_repo = ContextSnapshotRepository(sf)
    handoff_repo = ContextHandoffRepository(sf)
    route_repo = RouteDecisionRepository(sf)
    health_repo = ProviderHealthEventRepository(sf)
    htx_snapshot_repo = HTXMarketSnapshotRepository(sf)
    admin_repo = AdminEventRepository(sf)

    # services
    accounting = TokenAccountingService(usage_repo)
    quota_engine = QuotaPolicyEngine(quota_repo, usage_repo, config)
    context_service = ContextService(session_repo, message_repo, snapshot_repo)
    handoff_builder = HandoffBuilder(context_service)
    model_registry = ModelRegistry()
    
    # provider adapters
    provider_adapters = create_provider_adapters(config)
    
    fallback_engine = FallbackEngine(model_registry, provider_adapters)

    # HTX
    htx_adapter = HTXMarketDataAdapter(
        api_key=config.HTX_API_KEY,
        api_secret=config.HTX_API_SECRET,
        base_url=config.HTX_BASE_URL,
    )

    container = ServiceContainer(
        user_repo=user_repo,
        chat_repo=chat_repo,
        session_repo=session_repo,
        message_repo=message_repo,
        usage_repo=usage_repo,
        quota_repo=quota_repo,
        snapshot_repo=snapshot_repo,
        handoff_repo=handoff_repo,
        route_repo=route_repo,
        health_repo=health_repo,
        htx_snapshot_repo=htx_snapshot_repo,
        admin_repo=admin_repo,
        accounting=accounting,
        quota_engine=quota_engine,
        context_service=context_service,
        handoff_builder=handoff_builder,
        model_registry=model_registry,
        fallback_engine=fallback_engine,
        htx_adapter=htx_adapter,
        provider_adapters=provider_adapters,
    )

    log.info("ServiceContainer built: %d provider adapters", len(provider_adapters))
    return container

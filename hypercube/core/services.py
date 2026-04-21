"""Service container — initializes and holds all service instances."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from accounting.service import TokenAccountingService
from context.service import ContextService
from context.handoff import HandoffBuilder
from quotas.engine import QuotaPolicyEngine
from routing.service import FallbackEngine, ModelRegistry
from providers.htx_adapter import HTXMarketDataAdapter
from storage.repositories import (
    UsageRecordRepository,
    QuotaStateRepository,
    ConversationSessionRepository,
    ConversationMessageRepository,
    ContextSnapshotRepository,
    ContextHandoffRepository,
    RouteDecisionRepository,
    UserRepository,
    TelegramChatRepository,
    ProviderHealthEventRepository,
    HTXMarketSnapshotRepository,
    AdminEventRepository,
)


@dataclass
class ServiceContainer:
    """Holds all initialized services."""
    # repositories
    user_repo: UserRepository = field(default=None)
    chat_repo: TelegramChatRepository = field(default=None)
    session_repo: ConversationSessionRepository = field(default=None)
    message_repo: ConversationMessageRepository = field(default=None)
    usage_repo: UsageRecordRepository = field(default=None)
    quota_repo: QuotaStateRepository = field(default=None)
    snapshot_repo: ContextSnapshotRepository = field(default=None)
    handoff_repo: ContextHandoffRepository = field(default=None)
    route_repo: RouteDecisionRepository = field(default=None)
    health_repo: ProviderHealthEventRepository = field(default=None)
    htx_snapshot_repo: HTXMarketSnapshotRepository = field(default=None)
    admin_repo: AdminEventRepository = field(default=None)

    # services
    accounting: TokenAccountingService = field(default=None)
    quota_engine: QuotaPolicyEngine = field(default=None)
    context_service: ContextService = field(default=None)
    handoff_builder: HandoffBuilder = field(default=None)
    model_registry: ModelRegistry = field(default=None)
    fallback_engine: FallbackEngine = field(default=None)
    htx_adapter: HTXMarketDataAdapter = field(default=None)

    # provider adapters
    provider_adapters: dict[str, Any] = field(default_factory=dict)

    # user session state (in-memory, per telegram_user_id)
    user_sessions: dict[int, "UserSessionState"] = field(default_factory=dict)


@dataclass
class UserSessionState:
    """Per-user in-memory session state."""
    telegram_user_id: int
    current_mode: str = "free_only"
    current_model: str = ""
    session_id: str = ""
    conversation_id: str = ""

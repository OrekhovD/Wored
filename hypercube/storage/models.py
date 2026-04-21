"""SQLAlchemy ORM models for the Hytergram gateway."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(100), default=None)
    first_name: Mapped[str | None] = mapped_column(String(100), default=None)
    last_name: Mapped[str | None] = mapped_column(String(100), default=None)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    conversations = relationship("ConversationSession", back_populates="user")
    chats = relationship("TelegramChat", back_populates="user")


class TelegramChat(Base):
    __tablename__ = "telegram_chats"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    chat_type: Mapped[str] = mapped_column(String(20))
    title: Mapped[str | None] = mapped_column(String(200), default=None)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="chats")


class ConversationSession(Base):
    __tablename__ = "conversation_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("telegram_chats.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    mode: Mapped[str] = mapped_column(String(20), default="free_only")
    active_model: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user = relationship("User", back_populates="conversations")
    messages = relationship("ConversationMessage", back_populates="session")
    snapshots = relationship("ContextSnapshot", back_populates="session")
    chat = relationship("TelegramChat", back_populates="sessions")


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_sessions.session_id"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session = relationship("ConversationSession", back_populates="messages")
    __table_args__ = (Index("ix_session_created", "session_id", "created_at"),)


class ContextSnapshot(Base):
    __tablename__ = "context_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_sessions.session_id"), index=True
    )
    version: Mapped[str] = mapped_column(String(10))
    summary_text: Mapped[str] = mapped_column(Text)
    last_market_facts: Mapped[str] = mapped_column(Text, default="")
    active_mode: Mapped[str] = mapped_column(String(20))
    active_model: Mapped[str] = mapped_column(String(100))
    token_budget_state: Mapped[str] = mapped_column(String(200), default="")
    compression_method: Mapped[str] = mapped_column(String(20), default="none")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session = relationship("ConversationSession", back_populates="snapshots")


class ContextHandoff(Base):
    __tablename__ = "context_handoffs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    from_session_id: Mapped[str] = mapped_column(String(64))
    to_session_id: Mapped[str] = mapped_column(String(64))
    from_model: Mapped[str] = mapped_column(String(100))
    to_model: Mapped[str] = mapped_column(String(100))
    handoff_summary: Mapped[str] = mapped_column(Text)
    handoff_version: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProviderRegistry(Base):
    __tablename__ = "provider_registry"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str] = mapped_column(String(500))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    models_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ModelRegistry(Base):
    __tablename__ = "model_registry"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("provider_registry.provider_id"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="active")
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_streaming: Mapped[bool] = mapped_column(Boolean, default=False)
    supports_system_prompt: Mapped[bool] = mapped_column(Boolean, default=False)
    input_token_cost_per_1k: Mapped[float] = mapped_column(Float, default=0.0)
    output_token_cost_per_1k: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ProviderHealthEvent(Base):
    __tablename__ = "provider_health_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("provider_registry.provider_id"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(50))
    is_healthy: Mapped[bool] = mapped_column(Boolean)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, index=True)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("provider_registry.provider_id"), index=True
    )
    model_id: Mapped[str] = mapped_column(
        ForeignKey("model_registry.model_id"), index=True
    )
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    cached_tokens: Mapped[int | None] = mapped_column(Integer, default=None)
    latency_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    cost_estimate: Mapped[float] = mapped_column(Float, default=0.0)
    cost_currency: Mapped[str] = mapped_column(String(10), default="USD")
    quota_scope: Mapped[str | None] = mapped_column(String(50), nullable=True)
    warning_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    context_handoff_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    uncertain_usage: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )


class QuotaState(Base):
    __tablename__ = "quota_states"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("provider_registry.provider_id"), index=True
    )
    model_id: Mapped[str] = mapped_column(
        ForeignKey("model_registry.model_id"), index=True
    )
    period: Mapped[str] = mapped_column(String(20), default="monthly")
    used_tokens: Mapped[int] = mapped_column(Integer, default=0)
    limit_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    warning_pct: Mapped[float] = mapped_column(Float, default=20.0)
    critical_pct: Mapped[float] = mapped_column(Float, default=10.0)
    hard_stop_pct: Mapped[float] = mapped_column(Float, default=3.0)
    remaining_pct: Mapped[float] = mapped_column(Float, default=100.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class RouteDecision(Base):
    __tablename__ = "route_decisions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    request_id: Mapped[str | None] = mapped_column(
        ForeignKey("usage_records.request_id"), nullable=True, index=True
    )
    request_type: Mapped[str] = mapped_column(String(50))
    mode: Mapped[str] = mapped_column(String(20))
    current_model: Mapped[str] = mapped_column(String(100))
    candidate_chain: Mapped[str] = mapped_column(Text)
    selected_model: Mapped[str] = mapped_column(String(100))
    handoff_required: Mapped[bool] = mapped_column(Boolean, default=False)
    handoff_summary_version: Mapped[str] = mapped_column(String(10))
    reason_current_model_excluded: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class HTXMarketSnapshot(Base):
    __tablename__ = "htx_market_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(50), index=True)
    data_type: Mapped[str] = mapped_column(String(30))
    raw_data: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )


class AdminEvent(Base):
    __tablename__ = "admin_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(100))
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

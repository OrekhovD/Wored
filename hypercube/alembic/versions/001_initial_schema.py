"""Initial schema creation."""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # users table
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=True),
        sa.Column("first_name", sa.String(length=100), nullable=True),
        sa.Column("last_name", sa.String(length=100), nullable=True),
        sa.Column("is_admin", sa.Boolean(), nullable=False, default=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_id"),
    )
    op.create_index(op.f("ix_users_telegram_id"), "users", ["telegram_id"])

    # telegram_chats table
    op.create_table(
        "telegram_chats",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("chat_type", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("chat_id"),
    )
    op.create_index(op.f("ix_telegram_chats_chat_id"), "telegram_chats", ["chat_id"])

    # conversation_sessions table
    op.create_table(
        "conversation_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False, default="free_only"),
        sa.Column("active_model", sa.String(length=100), nullable=False, default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["chat_id"], ["telegram_chats.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index(op.f("ix_conversation_sessions_session_id"), "conversation_sessions", ["session_id"])
    op.create_index(op.f("ix_conversation_sessions_chat_id"), "conversation_sessions", ["chat_id"])
    op.create_index(op.f("ix_conversation_sessions_user_id"), "conversation_sessions", ["user_id"])

    # conversation_messages table
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["conversation_sessions.session_id"]),
    )
    op.create_index(op.f("ix_conversation_messages_session_id"), "conversation_messages", ["session_id"])
    op.create_index("ix_session_created", "conversation_messages", ["session_id", "created_at"])

    # context_snapshots table
    op.create_table(
        "context_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=10), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("last_market_facts", sa.Text(), nullable=False),
        sa.Column("active_mode", sa.String(length=20), nullable=False),
        sa.Column("active_model", sa.String(length=100), nullable=False),
        sa.Column("token_budget_state", sa.String(length=200), nullable=False),
        sa.Column("compression_method", sa.String(length=20), nullable=False, default="none"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["conversation_sessions.session_id"]),
    )
    op.create_index(op.f("ix_context_snapshots_session_id"), "context_snapshots", ["session_id"])

    # context_handoffs table
    op.create_table(
        "context_handoffs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("from_session_id", sa.String(length=64), nullable=False),
        sa.Column("to_session_id", sa.String(length=64), nullable=False),
        sa.Column("from_model", sa.String(length=100), nullable=False),
        sa.Column("to_model", sa.String(length=100), nullable=False),
        sa.Column("handoff_summary", sa.Text(), nullable=False),
        sa.Column("handoff_version", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # provider_registry table
    op.create_table(
        "provider_registry",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, default=True),
        sa.Column("models_json", sa.Text(), nullable=False, default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id"),
    )
    op.create_index(op.f("ix_provider_registry_provider_id"), "provider_registry", ["provider_id"])

    # model_registry table
    op.create_table(
        "model_registry",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=False),
        sa.Column("provider_id", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, default="active"),
        sa.Column("is_premium", sa.Boolean(), nullable=False, default=False),
        sa.Column("supports_streaming", sa.Boolean(), nullable=False, default=False),
        sa.Column("supports_system_prompt", sa.Boolean(), nullable=False, default=False),
        sa.Column("input_token_cost_per_1k", sa.Float(), nullable=False, default=0.0),
        sa.Column("output_token_cost_per_1k", sa.Float(), nullable=False, default=0.0),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["provider_id"], ["provider_registry.provider_id"]),
        sa.UniqueConstraint("model_id"),
    )
    op.create_index(op.f("ix_model_registry_model_id"), "model_registry", ["model_id"])
    op.create_index(op.f("ix_model_registry_provider_id"), "model_registry", ["provider_id"])

    # provider_health_events table
    op.create_table(
        "provider_health_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.String(length=50), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("is_healthy", sa.Boolean(), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("checked_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["provider_id"], ["provider_registry.provider_id"]),
    )
    op.create_index(op.f("ix_provider_health_events_provider_id"), "provider_health_events", ["provider_id"])
    op.create_index(op.f("ix_provider_health_events_checked_at"), "provider_health_events", ["checked_at"])

    # usage_records table
    op.create_table(
        "usage_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=True),
        sa.Column("telegram_user_id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.String(length=50), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("cost_estimate", sa.Float(), nullable=False, default=0.0),
        sa.Column("cost_currency", sa.String(length=10), nullable=False, default="USD"),
        sa.Column("quota_scope", sa.String(length=50), nullable=True),
        sa.Column("warning_triggered", sa.Boolean(), nullable=False, default=False),
        sa.Column("fallback_triggered", sa.Boolean(), nullable=False, default=False),
        sa.Column("context_handoff_triggered", sa.Boolean(), nullable=False, default=False),
        sa.Column("uncertain_usage", sa.Boolean(), nullable=False, default=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
        sa.ForeignKeyConstraint(["model_id"], ["model_registry.model_id"]),
        sa.ForeignKeyConstraint(["provider_id"], ["provider_registry.provider_id"]),
    )
    op.create_index(op.f("ix_usage_records_conversation_id"), "usage_records", ["conversation_id"])
    op.create_index(op.f("ix_usage_records_telegram_user_id"), "usage_records", ["telegram_user_id"])
    op.create_index(op.f("ix_usage_records_provider_id"), "usage_records", ["provider_id"])
    op.create_index(op.f("ix_usage_records_model_id"), "usage_records", ["model_id"])
    op.create_index(op.f("ix_usage_records_created_at"), "usage_records", ["created_at"])

    # quota_states table
    op.create_table(
        "quota_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider_id", sa.String(length=50), nullable=False),
        sa.Column("model_id", sa.String(length=100), nullable=False),
        sa.Column("period", sa.String(length=20), nullable=False, default="monthly"),
        sa.Column("used_tokens", sa.Integer(), nullable=False, default=0),
        sa.Column("limit_tokens", sa.Integer(), nullable=True),
        sa.Column("warning_pct", sa.Float(), nullable=False, default=20.0),
        sa.Column("critical_pct", sa.Float(), nullable=False, default=10.0),
        sa.Column("hard_stop_pct", sa.Float(), nullable=False, default=3.0),
        sa.Column("remaining_pct", sa.Float(), nullable=False, default=100.0),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["model_id"], ["model_registry.model_id"]),
        sa.ForeignKeyConstraint(["provider_id"], ["provider_registry.provider_id"]),
    )
    op.create_index(op.f("ix_quota_states_provider_id"), "quota_states", ["provider_id"])
    op.create_index(op.f("ix_quota_states_model_id"), "quota_states", ["model_id"])

    # route_decisions table
    op.create_table(
        "route_decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("request_type", sa.String(length=50), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("current_model", sa.String(length=100), nullable=False),
        sa.Column("candidate_chain", sa.Text(), nullable=False),
        sa.Column("selected_model", sa.String(length=100), nullable=False),
        sa.Column("handoff_required", sa.Boolean(), nullable=False, default=False),
        sa.Column("handoff_summary_version", sa.String(length=10), nullable=False),
        sa.Column("reason_current_model_excluded", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["request_id"], ["usage_records.request_id"]),
    )
    op.create_index(op.f("ix_route_decisions_request_id"), "route_decisions", ["request_id"])

    # htx_market_snapshots table
    op.create_table(
        "htx_market_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=50), nullable=False),
        sa.Column("data_type", sa.String(length=30), nullable=False),
        sa.Column("raw_data", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_htx_market_snapshots_symbol"), "htx_market_snapshots", ["symbol"])
    op.create_index(op.f("ix_htx_market_snapshots_fetched_at"), "htx_market_snapshots", ["fetched_at"])

    # admin_events table
    op.create_table(
        "admin_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("admin_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["admin_id"], ["users.id"]),
    )


def downgrade() -> None:
    op.drop_table("admin_events")
    op.drop_table("htx_market_snapshots")
    op.drop_table("route_decisions")
    op.drop_table("quota_states")
    op.drop_table("usage_records")
    op.drop_table("provider_health_events")
    op.drop_table("model_registry")
    op.drop_table("provider_registry")
    op.drop_table("context_handoffs")
    op.drop_table("context_snapshots")
    op.drop_table("conversation_messages")
    op.drop_table("conversation_sessions")
    op.drop_table("telegram_chats")
    op.drop_table("users")

"""
Daily Pipeline WORED v2 — DB schema for 8 new tables.
ТЗ: https:///mnt/d/WORED/TASOCHKI/ТЗ/ТЗ_daily_pipeline_WORED_v2.md

Таблицы:
  trading_sessions, session_plans, session_revisions,
  planned_entries, executed_trades, execution_events,
  session_metrics, daily_reviews

Существующие таблицы (forecast, ai_journal, sim_positions, historical_data) НЕ ТРОГАЮТСЯ.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

PIPELINE_TABLES_SQL = """
-- 12.1 trading_sessions
CREATE TABLE IF NOT EXISTS trading_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL DEFAULT 'HTX',
    session_start TIMESTAMPTZ NOT NULL,
    session_end TIMESTAMPTZ NOT NULL,
    forecast_horizon_hours INT NOT NULL DEFAULT 8,
    initial_budget_usdt NUMERIC(20,8) NOT NULL,
    risk_mode TEXT NOT NULL DEFAULT 'balanced',
    status TEXT NOT NULL DEFAULT 'idle',
    active_plan_version INT NOT NULL DEFAULT 1,
    final_status_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_trading_sessions_user ON trading_sessions (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trading_sessions_status ON trading_sessions (status) WHERE status IN ('idle','armed','in_position','cooldown','paused','stopped');

-- 12.2 session_plans
CREATE TABLE IF NOT EXISTS session_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES trading_sessions(id) ON DELETE CASCADE,
    version INT NOT NULL,
    plan_type TEXT NOT NULL DEFAULT 'initial',
    plan_json JSONB NOT NULL,
    created_by_role TEXT NOT NULL DEFAULT 'analyst',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(session_id, version)
);

CREATE INDEX IF NOT EXISTS idx_session_plans_session ON session_plans (session_id, version DESC);

-- 12.3 session_revisions
CREATE TABLE IF NOT EXISTS session_revisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES trading_sessions(id) ON DELETE CASCADE,
    base_version INT NOT NULL,
    new_version INT NOT NULL,
    execution_command TEXT NOT NULL DEFAULT 'continue',
    revision_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_session_revisions_session ON session_revisions (session_id, created_at DESC);

-- 12.4 planned_entries
CREATE TABLE IF NOT EXISTS planned_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES trading_sessions(id) ON DELETE CASCADE,
    plan_version INT NOT NULL,
    side TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    entry_zone_from NUMERIC(20,8) NOT NULL,
    entry_zone_to NUMERIC(20,8) NOT NULL,
    invalidation_price NUMERIC(20,8) NOT NULL,
    stop_loss NUMERIC(20,8) NOT NULL,
    take_profit_json JSONB NOT NULL,
    recommended_leverage INT NOT NULL,
    budget_share_pct NUMERIC(10,4) NOT NULL,
    margin_mode TEXT NOT NULL DEFAULT 'isolated',
    confirmation_rule TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_planned_entries_session ON planned_entries (session_id, plan_version);
CREATE INDEX IF NOT EXISTS idx_planned_entries_status ON planned_entries (status) WHERE status = 'planned';

-- 12.5 executed_trades
CREATE TABLE IF NOT EXISTS executed_trades (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES trading_sessions(id) ON DELETE CASCADE,
    entry_id UUID REFERENCES planned_entries(id),
    side TEXT NOT NULL,
    margin_mode TEXT NOT NULL,
    leverage INT NOT NULL,
    opened_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ,
    entry_price NUMERIC(20,8) NOT NULL,
    exit_price NUMERIC(20,8),
    mark_exit_price NUMERIC(20,8),
    position_qty NUMERIC(30,12) NOT NULL,
    position_notional_usdt NUMERIC(20,8) NOT NULL,
    margin_used_usdt NUMERIC(20,8) NOT NULL,
    open_fee_usdt NUMERIC(20,8) NOT NULL,
    close_fee_usdt NUMERIC(20,8),
    realised_pnl_usdt NUMERIC(20,8),
    close_reason TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_executed_trades_session ON executed_trades (session_id, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_executed_trades_status ON executed_trades (status) WHERE status = 'open';

-- 12.6 execution_events
CREATE TABLE IF NOT EXISTS execution_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES trading_sessions(id) ON DELETE CASCADE,
    trade_id UUID,
    entry_id UUID,
    event_type TEXT NOT NULL,
    state_before TEXT,
    state_after TEXT,
    event_payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_execution_events_session ON execution_events (session_id, created_at DESC);

-- 12.7 session_metrics
CREATE TABLE IF NOT EXISTS session_metrics (
    session_id UUID PRIMARY KEY REFERENCES trading_sessions(id) ON DELETE CASCADE,
    trade_count INT NOT NULL DEFAULT 0,
    win_count INT NOT NULL DEFAULT 0,
    loss_count INT NOT NULL DEFAULT 0,
    liquidation_count INT NOT NULL DEFAULT 0,
    total_pnl_usdt NUMERIC(20,8) NOT NULL DEFAULT 0,
    total_pnl_pct NUMERIC(20,8) NOT NULL DEFAULT 0,
    max_drawdown_pct NUMERIC(20,8) NOT NULL DEFAULT 0,
    profit_factor NUMERIC(20,8),
    avg_win_usdt NUMERIC(20,8),
    avg_loss_usdt NUMERIC(20,8),
    time_in_market_pct NUMERIC(20,8),
    idle_time_pct NUMERIC(20,8),
    max_win_streak INT,
    max_loss_streak INT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 12.8 daily_reviews
CREATE TABLE IF NOT EXISTS daily_reviews (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES trading_sessions(id) ON DELETE CASCADE,
    review_model TEXT NOT NULL,
    review_text TEXT NOT NULL,
    what_worked TEXT,
    what_failed TEXT,
    rule_changes JSONB,
    status TEXT NOT NULL DEFAULT 'completed',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_daily_reviews_session ON daily_reviews (session_id, created_at DESC);
"""


async def ensure_pipeline_tables():
    """Create daily pipeline tables if they don't exist."""
    from storage.postgres_client import get_pool
    pool = await get_pool()
    if not pool:
        log.warning("No DB pool — pipeline tables not created")
        return
    async with pool.acquire() as conn:
        await conn.execute(PIPELINE_TABLES_SQL)
    log.info("Pipeline tables ensured (8 tables)")
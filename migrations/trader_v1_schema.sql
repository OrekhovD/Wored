-- WORED Trader v0.1 additive schema.
-- Apply only through the migration rehearsal against wored_qa before production.
-- Financial execution remains in paper_v2_*; these tables store market,
-- forecast, plan, and review evidence only.

BEGIN;

CREATE TABLE IF NOT EXISTS trader_v1_perp_candles (
    candle_id UUID PRIMARY KEY,
    venue VARCHAR(24) NOT NULL DEFAULT 'htx',
    contract_code VARCHAR(32) NOT NULL,
    timeframe VARCHAR(12) NOT NULL,
    open_time TIMESTAMPTZ NOT NULL,
    close_time TIMESTAMPTZ NOT NULL,
    open NUMERIC(20,8) NOT NULL,
    high NUMERIC(20,8) NOT NULL,
    low NUMERIC(20,8) NOT NULL,
    close NUMERIC(20,8) NOT NULL,
    volume NUMERIC(28,8) NOT NULL,
    source VARCHAR(48) NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (venue, contract_code, timeframe, open_time),
    CHECK (high >= low),
    CHECK (open_time < close_time)
);
CREATE INDEX IF NOT EXISTS idx_trader_v1_candles_lookup
    ON trader_v1_perp_candles (contract_code, timeframe, open_time DESC);

CREATE TABLE IF NOT EXISTS trader_v1_forecast_runs (
    forecast_run_id UUID PRIMARY KEY,
    contract_code VARCHAR(32) NOT NULL,
    horizon_minutes INTEGER NOT NULL CHECK (horizon_minutes IN (15, 60, 240)),
    model_version VARCHAR(64) NOT NULL,
    data_cutoff TIMESTAMPTZ NOT NULL,
    status VARCHAR(16) NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'expired')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS trader_v1_forecast_candles (
    forecast_candle_id UUID PRIMARY KEY,
    forecast_run_id UUID NOT NULL REFERENCES trader_v1_forecast_runs (forecast_run_id) ON DELETE RESTRICT,
    open_time TIMESTAMPTZ NOT NULL,
    close_time TIMESTAMPTZ NOT NULL,
    predicted_open NUMERIC(20,8) NOT NULL,
    predicted_high NUMERIC(20,8) NOT NULL,
    predicted_low NUMERIC(20,8) NOT NULL,
    predicted_close NUMERIC(20,8) NOT NULL,
    confidence NUMERIC(8,6) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    UNIQUE (forecast_run_id, open_time),
    CHECK (predicted_high >= predicted_low),
    CHECK (open_time < close_time)
);

CREATE TABLE IF NOT EXISTS trader_v1_forecast_indicators (
    forecast_indicator_id UUID PRIMARY KEY,
    forecast_run_id UUID NOT NULL REFERENCES trader_v1_forecast_runs (forecast_run_id) ON DELETE RESTRICT,
    name VARCHAR(64) NOT NULL,
    value NUMERIC(28,10) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (forecast_run_id, name)
);

CREATE TABLE IF NOT EXISTS trader_v1_forecast_eval (
    forecast_eval_id UUID PRIMARY KEY,
    forecast_run_id UUID NOT NULL REFERENCES trader_v1_forecast_runs (forecast_run_id) ON DELETE RESTRICT,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    mae NUMERIC(20,8),
    directional_accuracy NUMERIC(8,6),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS trader_v1_trade_plans (
    trade_plan_id UUID PRIMARY KEY,
    account_id UUID NOT NULL REFERENCES paper_v2_accounts (account_id) ON DELETE RESTRICT,
    day_id UUID NOT NULL REFERENCES paper_v2_days (day_id) ON DELETE RESTRICT,
    contract_code VARCHAR(32) NOT NULL,
    plan_version VARCHAR(64) NOT NULL,
    state VARCHAR(16) NOT NULL CHECK (state IN ('draft', 'active', 'expired', 'rejected', 'consumed')),
    valid_from TIMESTAMPTZ NOT NULL,
    valid_until TIMESTAMPTZ NOT NULL,
    plan_json JSONB NOT NULL,
    created_by VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (valid_from < valid_until),
    UNIQUE (account_id, contract_code, plan_version)
);
CREATE INDEX IF NOT EXISTS idx_trader_v1_active_plans
    ON trader_v1_trade_plans (account_id, contract_code, valid_until DESC)
    WHERE state = 'active';

CREATE TABLE IF NOT EXISTS trader_v1_agent_runs (
    agent_run_id UUID PRIMARY KEY,
    role VARCHAR(32) NOT NULL,
    account_id UUID REFERENCES paper_v2_accounts (account_id) ON DELETE SET NULL,
    day_id UUID REFERENCES paper_v2_days (day_id) ON DELETE SET NULL,
    input_hash VARCHAR(64) NOT NULL,
    output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(16) NOT NULL CHECK (status IN ('completed', 'failed', 'budget_blocked', 'skipped')),
    provider VARCHAR(64),
    model VARCHAR(128),
    input_tokens INTEGER,
    output_tokens INTEGER,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS trader_v1_trade_reviews (
    trade_review_id UUID PRIMARY KEY,
    position_id UUID NOT NULL REFERENCES paper_v2_positions (position_id) ON DELETE RESTRICT,
    account_id UUID NOT NULL REFERENCES paper_v2_accounts (account_id) ON DELETE RESTRICT,
    review_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (position_id)
);

COMMIT;

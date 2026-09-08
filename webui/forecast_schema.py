"""Shared forecast DDL; importing it does not start the WebUI."""
PREDICTION_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS forecast_requests (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(15) NOT NULL,
    horizon_hours INT NOT NULL,
    base_timeframe VARCHAR(10) NOT NULL DEFAULT '60min',
    depth INT NOT NULL DEFAULT 3,
    base_price DECIMAL(20, 8) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    source VARCHAR(20) NOT NULL DEFAULT 'webui',
    requested_by VARCHAR(64),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_forecast_requests_created_at ON forecast_requests (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_forecast_requests_symbol_status ON forecast_requests (symbol, status);

CREATE TABLE IF NOT EXISTS forecast_model_runs (
    id SERIAL PRIMARY KEY,
    request_id INT NOT NULL REFERENCES forecast_requests(id) ON DELETE CASCADE,
    model_key VARCHAR(32) NOT NULL,
    model_name VARCHAR(128) NOT NULL,
    model_id VARCHAR(128) NOT NULL,
    agent_role VARCHAR(20) NOT NULL DEFAULT 'neutral',
    status VARCHAR(20) NOT NULL DEFAULT 'completed',
    summary TEXT,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_forecast_model_runs_request_id ON forecast_model_runs (request_id);
CREATE INDEX IF NOT EXISTS idx_forecast_model_runs_role ON forecast_model_runs (request_id, agent_role);

CREATE TABLE IF NOT EXISTS forecast_points (
    id SERIAL PRIMARY KEY,
    request_id INT NOT NULL REFERENCES forecast_requests(id) ON DELETE CASCADE,
    model_run_id INT NOT NULL REFERENCES forecast_model_runs(id) ON DELETE CASCADE,
    forecast_hour INT NOT NULL,
    target_time TIMESTAMP NOT NULL,
    predicted_price DECIMAL(20, 8) NOT NULL,
    predicted_change_pct DECIMAL(10, 4) NOT NULL,
    confidence DECIMAL(5, 2),
    rationale TEXT,
    actual_price DECIMAL(20, 8),
    actual_change_pct DECIMAL(10, 4),
    price_error_pct DECIMAL(10, 4),
    change_error_pct DECIMAL(10, 4),
    accuracy_score DECIMAL(6, 2),
    failure_score DECIMAL(6, 2),
    direction_match BOOLEAN,
    verdict TEXT,
    evaluated_at TIMESTAMP,
    UNIQUE (model_run_id, forecast_hour)
);

CREATE INDEX IF NOT EXISTS idx_forecast_points_target_time ON forecast_points (target_time, evaluated_at);
CREATE INDEX IF NOT EXISTS idx_forecast_points_request_id ON forecast_points (request_id, forecast_hour);

-- Additive migrations for Phase 2
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS base_timeframe VARCHAR(10) DEFAULT '60min';
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS depth INT DEFAULT 3;
ALTER TABLE forecast_model_runs ADD COLUMN IF NOT EXISTS agent_role VARCHAR(20) DEFAULT 'neutral';
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS metrics_version INT NOT NULL DEFAULT 1;
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS baseline_error_pct DECIMAL(12,6);
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS skill_vs_baseline DOUBLE PRECISION;
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS step_index INT DEFAULT 0;
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS predicted_low DECIMAL(20, 8);
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS predicted_high DECIMAL(20, 8);
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS in_range BOOLEAN DEFAULT NULL;
ALTER TABLE forecast_points ADD COLUMN IF NOT EXISTS pattern_match_score DECIMAL(5,2) DEFAULT NULL;

CREATE TABLE IF NOT EXISTS forecast_reports (
    id SERIAL PRIMARY KEY,
    request_id INT NOT NULL REFERENCES forecast_requests(id) ON DELETE CASCADE,
    model_run_id INT NOT NULL REFERENCES forecast_model_runs(id) ON DELETE CASCADE,
    agent_role VARCHAR(20) NOT NULL DEFAULT 'neutral',
    evaluated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    step_index INT NOT NULL,
    target_time TIMESTAMP NOT NULL,
    factual_price DECIMAL(20, 8),
    error_pct DECIMAL(10, 4),
    in_range BOOLEAN,
    confidence_before DECIMAL(5, 2),
    confidence_after DECIMAL(5, 2),
    reason_text TEXT,
    model_response TEXT,
    UNIQUE (model_run_id, step_index)
);

CREATE INDEX IF NOT EXISTS idx_forecast_reports_request_id ON forecast_reports (request_id);
CREATE INDEX IF NOT EXISTS idx_forecast_reports_evaluated ON forecast_reports (evaluated_at);
"""

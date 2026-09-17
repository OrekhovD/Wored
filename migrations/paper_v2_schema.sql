-- =========================================================================
-- paper_v2_schema.sql — DDL migration for paper_trading domain package
--
-- PostgreSQL source of truth for WORED paper trading.
-- All money columns: NUMERIC(20,8).  All timestamps: TIMESTAMPTZ.  All IDs: UUID.
-- Table prefix: paper_v2_
--
-- Invariants:
--   * Idempotency: UNIQUE(owner_id, idempotency_key) on commands.
--       same key + same request_hash  → return existing result
--       same key + different hash     → conflict
--   * Signal uniqueness: UNIQUE(account_id, strategy_version, instrument,
--       closed_bar_time, direction) — one signal cannot create two entries.
--   * Append-only journal: UNIQUE(source_type, source_ref, bucket) prevents
--       double-posting of the same financial event.
--   * At most one incomplete day per owner: partial unique index.
--   * UNIQUE(owner_id, kind) on accounts — one manual + one auto per owner.
--   * Execution serialised by PostgreSQL transaction + account/position lock.
--
-- Schema version tracked in paper_v2_schema_version.
-- =========================================================================

-- Enable UUID extension if not present (idempotent).
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ---------------------------------------------------------------------------
-- Schema version tracking
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_schema_version (
    version       INTEGER      NOT NULL PRIMARY KEY,
    applied_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    description   TEXT
);

-- ---------------------------------------------------------------------------
-- Owners & identities
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_owners (
    owner_id        UUID         NOT NULL PRIMARY KEY,
    display_name    VARCHAR(128) NOT NULL,
    telegram_id     BIGINT       UNIQUE,
    webui_identity  VARCHAR(128) UNIQUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    schema_version  INTEGER      NOT NULL DEFAULT 2
);

CREATE INDEX IF NOT EXISTS idx_owners_telegram
    ON paper_v2_owners (telegram_id);

CREATE INDEX IF NOT EXISTS idx_owners_webui
    ON paper_v2_owners (webui_identity);

-- ---------------------------------------------------------------------------
-- Accounts — one manual + one auto per owner
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_accounts (
    account_id      UUID         NOT NULL PRIMARY KEY,
    owner_id        UUID         NOT NULL REFERENCES paper_v2_owners (owner_id) ON DELETE RESTRICT,
    kind            VARCHAR(10)  NOT NULL CHECK (kind IN ('manual', 'auto')),
    currency        VARCHAR(10)  NOT NULL DEFAULT 'USDT',
    opening_deposit NUMERIC(20,8) NOT NULL DEFAULT 1000,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    schema_version  INTEGER      NOT NULL DEFAULT 2,
    UNIQUE (owner_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_accounts_owner
    ON paper_v2_accounts (owner_id, kind);

-- ---------------------------------------------------------------------------
-- Trading days — state machine: idle → starting → running → closing → closed
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_days (
    day_id            UUID         NOT NULL PRIMARY KEY,
    owner_id          UUID         NOT NULL REFERENCES paper_v2_owners (owner_id) ON DELETE RESTRICT,
    timezone          VARCHAR(64)  NOT NULL DEFAULT 'Asia/Bangkok',
    start_utc         TIMESTAMPTZ,
    end_utc           TIMESTAMPTZ,
    state             VARCHAR(24)  NOT NULL DEFAULT 'idle'
        CHECK (state IN ('idle', 'starting', 'running', 'closing', 'closed',
                         'recovery_required', 'settlement_pending')),
    strategy_version  VARCHAR(64)  NOT NULL DEFAULT 'baseline_v1',
    settings_snapshot JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    schema_version    INTEGER      NOT NULL DEFAULT 2
);

-- At most one incomplete (non-closed) day per owner.
CREATE UNIQUE INDEX IF NOT EXISTS uq_days_one_incomplete
    ON paper_v2_days (owner_id)
    WHERE state NOT IN ('closed');

CREATE INDEX IF NOT EXISTS idx_days_owner_state
    ON paper_v2_days (owner_id, state);

CREATE INDEX IF NOT EXISTS idx_days_start
    ON paper_v2_days (start_utc DESC);

-- ---------------------------------------------------------------------------
-- Commands — idempotency
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_commands (
    command_id        UUID         NOT NULL PRIMARY KEY,
    owner_id          UUID         NOT NULL REFERENCES paper_v2_owners (owner_id) ON DELETE RESTRICT,
    account_id        UUID         REFERENCES paper_v2_accounts (account_id) ON DELETE RESTRICT,
    day_id            UUID         REFERENCES paper_v2_days (day_id) ON DELETE RESTRICT,
    command_type      VARCHAR(32)  NOT NULL
        CHECK (command_type IN ('start_day', 'finish_day', 'set_automation',
                                 'pause_auto', 'resume_auto', 'submit_order',
                                 'close_position', 'close_all', 'cancel_order')),
    idempotency_key   VARCHAR(256) NOT NULL,
    request_hash      VARCHAR(64)  NOT NULL,  -- SHA-256 hex
    expected_revision INTEGER,
    status            VARCHAR(16)  NOT NULL DEFAULT 'accepted'
        CHECK (status IN ('accepted', 'processing', 'completed', 'failed', 'conflict')),
    result            JSONB,
    error             TEXT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    schema_version    INTEGER      NOT NULL DEFAULT 2,
    UNIQUE (owner_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_commands_owner_status
    ON paper_v2_commands (owner_id, status);

CREATE INDEX IF NOT EXISTS idx_commands_account
    ON paper_v2_commands (account_id)
    WHERE account_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_commands_day
    ON paper_v2_commands (day_id)
    WHERE day_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Signals — uniqueness prevents duplicate entries after restart
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_signals (
    signal_id         UUID         NOT NULL PRIMARY KEY,
    account_id        UUID         NOT NULL REFERENCES paper_v2_accounts (account_id) ON DELETE RESTRICT,
    strategy_version  VARCHAR(64)  NOT NULL DEFAULT 'baseline_v1',
    instrument        VARCHAR(32)  NOT NULL DEFAULT 'BTC-USDT',
    closed_bar_time   TIMESTAMPTZ  NOT NULL,
    direction         VARCHAR(10)  NOT NULL CHECK (direction IN ('long', 'short')),
    entry_ref_price   NUMERIC(20,8) NOT NULL DEFAULT 0,
    stop_loss         NUMERIC(20,8) NOT NULL DEFAULT 0,
    take_profit       NUMERIC(20,8) NOT NULL DEFAULT 0,
    atr_value         NUMERIC(20,8) NOT NULL DEFAULT 0,
    valid_until       TIMESTAMPTZ,
    rejected_reason   TEXT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    schema_version    INTEGER      NOT NULL DEFAULT 2,
    -- One signal per account/version/instrument/bar/direction
    UNIQUE (account_id, strategy_version, instrument, closed_bar_time, direction)
);

CREATE INDEX IF NOT EXISTS idx_signals_account_bar
    ON paper_v2_signals (account_id, closed_bar_time DESC);

CREATE INDEX IF NOT EXISTS idx_signals_valid
    ON paper_v2_signals (account_id, valid_until)
    WHERE rejected_reason IS NULL;

-- ---------------------------------------------------------------------------
-- Orders
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_orders (
    order_id                  UUID         NOT NULL PRIMARY KEY,
    account_id                UUID         NOT NULL REFERENCES paper_v2_accounts (account_id) ON DELETE RESTRICT,
    day_id                    UUID         NOT NULL REFERENCES paper_v2_days (day_id) ON DELETE RESTRICT,
    origin                    VARCHAR(10)  NOT NULL CHECK (origin IN ('user', 'auto')),
    actor                     VARCHAR(10)  NOT NULL CHECK (actor IN ('user', 'auto')),
    signal_id                 UUID         REFERENCES paper_v2_signals (signal_id) ON DELETE SET NULL,
    side                      VARCHAR(10)  NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type                VARCHAR(20)  NOT NULL DEFAULT 'market'
        CHECK (order_type IN ('market', 'stop_market', 'market_on_trigger')),
    instrument                VARCHAR(32)  NOT NULL DEFAULT 'BTC-USDT',
    qty                       NUMERIC(20,8) NOT NULL NOT NULL DEFAULT 0,
    price                     NUMERIC(20,8),  -- limit/stop reference
    stop_loss                 NUMERIC(20,8),
    take_profit               NUMERIC(20,8),
    state                     VARCHAR(20)  NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'submitted', 'partially_filled',
                         'filled', 'cancelled', 'rejected', 'expired')),
    filled_qty                NUMERIC(20,8) NOT NULL DEFAULT 0,
    execution_engine_version  VARCHAR(16)  NOT NULL DEFAULT '1',
    created_at                TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ  NOT NULL DEFAULT now(),
    schema_version            INTEGER      NOT NULL DEFAULT 2,
    CHECK (filled_qty >= 0),
    CHECK (qty >= 0)
);

CREATE INDEX IF NOT EXISTS idx_orders_account_state
    ON paper_v2_orders (account_id, state);

CREATE INDEX IF NOT EXISTS idx_orders_day
    ON paper_v2_orders (day_id, state);

CREATE INDEX IF NOT EXISTS idx_orders_signal
    ON paper_v2_orders (signal_id)
    WHERE signal_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Fills — repeat command does not create duplicate fill
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_fills (
    fill_id              UUID         NOT NULL PRIMARY KEY,
    order_id             UUID         NOT NULL REFERENCES paper_v2_orders (order_id) ON DELETE RESTRICT,
    account_id           UUID         NOT NULL REFERENCES paper_v2_accounts (account_id) ON DELETE RESTRICT,
    execution_quote_id   VARCHAR(128) NOT NULL,
    instrument           VARCHAR(32)  NOT NULL DEFAULT 'BTC-USDT',
    side                 VARCHAR(10)  NOT NULL CHECK (side IN ('buy', 'sell')),
    price                NUMERIC(20,8) NOT NULL,
    qty                  NUMERIC(20,8) NOT NULL,
    fee                  NUMERIC(20,8) NOT NULL DEFAULT 0,
    fee_rate             NUMERIC(20,8) NOT NULL DEFAULT 0.0006,
    slippage_bps         NUMERIC(10,4) NOT NULL DEFAULT 0,  -- informational
    is_close             BOOLEAN      NOT NULL DEFAULT FALSE,
    source_timestamp     TIMESTAMPTZ,
    receive_timestamp    TIMESTAMPTZ,
    execute_timestamp    TIMESTAMPTZ,
    schema_version       INTEGER      NOT NULL DEFAULT 2,
    -- Prevent duplicate fill for same quote
    UNIQUE (execution_quote_id, order_id)
);

CREATE INDEX IF NOT EXISTS idx_fills_order
    ON paper_v2_fills (order_id, execute_timestamp);

CREATE INDEX IF NOT EXISTS idx_fills_account_time
    ON paper_v2_fills (account_id, execute_timestamp DESC);

-- ---------------------------------------------------------------------------
-- Positions — lineage to fills, owner engine version prevents dual ownership
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_positions (
    position_id            UUID         NOT NULL PRIMARY KEY,
    account_id             UUID         NOT NULL REFERENCES paper_v2_accounts (account_id) ON DELETE RESTRICT,
    day_id                 UUID         NOT NULL REFERENCES paper_v2_days (day_id) ON DELETE RESTRICT,
    instrument             VARCHAR(32)  NOT NULL DEFAULT 'BTC-USDT',
    side                   VARCHAR(10)  NOT NULL CHECK (side IN ('long', 'short')),
    qty                    NUMERIC(20,8) NOT NULL DEFAULT 0,
    avg_entry_price        NUMERIC(20,8) NOT NULL DEFAULT 0,
    isolated_margin        NUMERIC(20,8) NOT NULL DEFAULT 0,
    stop_loss              NUMERIC(20,8),
    take_profit            NUMERIC(20,8),
    status                 VARCHAR(16)  NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'closed', 'liquidated')),
    owner_engine_version   VARCHAR(16)  NOT NULL DEFAULT '1',
    opened_at              TIMESTAMPTZ  NOT NULL DEFAULT now(),
    closed_at              TIMESTAMPTZ,
    close_price            NUMERIC(20,8),
    realized_gross_pnl     NUMERIC(20,8) NOT NULL DEFAULT 0,
    realized_net_pnl       NUMERIC(20,8) NOT NULL DEFAULT 0,
    entry_fee              NUMERIC(20,8) NOT NULL DEFAULT 0,
    exit_fee               NUMERIC(20,8) NOT NULL DEFAULT 0,
    funding_cashflow       NUMERIC(20,8) NOT NULL DEFAULT 0,
    schema_version         INTEGER      NOT NULL DEFAULT 2,
    CHECK (qty >= 0),
    CHECK (isolated_margin >= 0),
    -- Max one open position per instrument per account
    -- (enforced by partial unique index below)
    CHECK (status != 'closed' OR closed_at IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_positions_open_per_instrument
    ON paper_v2_positions (account_id, instrument)
    WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_positions_account_status
    ON paper_v2_positions (account_id, status);

CREATE INDEX IF NOT EXISTS idx_positions_day
    ON paper_v2_positions (day_id, status);

-- ---------------------------------------------------------------------------
-- Journal postings — append-only signed cashflow components
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_postings (
    posting_id      UUID         NOT NULL PRIMARY KEY,
    account_id      UUID         NOT NULL REFERENCES paper_v2_accounts (account_id) ON DELETE RESTRICT,
    day_id          UUID         REFERENCES paper_v2_days (day_id) ON DELETE SET NULL,
    event_id        UUID         NOT NULL,  -- groups postings in one event
    source_type     VARCHAR(24)  NOT NULL
        CHECK (source_type IN ('deposit', 'fill', 'funding', 'liquidation',
                               'closeout', 'reconciliation', 'manual_adjustment')),
    source_ref      VARCHAR(128),  -- fill_id / funding_event_id / etc.
    currency        VARCHAR(10)  NOT NULL DEFAULT 'USDT',
    bucket          VARCHAR(24)  NOT NULL
        CHECK (bucket IN ('cash', 'entry_fee', 'exit_fee', 'funding',
                          'realized_gross_pnl', 'realized_net_pnl',
                          'reserved_margin', 'released_margin',
                          'liquidation', 'deposit', 'withdrawal', 'adjustment')),
    amount          NUMERIC(20,8) NOT NULL,  -- signed
    occurred_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    schema_version  INTEGER      NOT NULL DEFAULT 2,
    -- Append-only: same financial source + type cannot post twice
    UNIQUE (source_type, source_ref, bucket)
);

CREATE INDEX IF NOT EXISTS idx_postings_account_time
    ON paper_v2_postings (account_id, created_at);

CREATE INDEX IF NOT EXISTS idx_postings_event
    ON paper_v2_postings (event_id);

CREATE INDEX IF NOT EXISTS idx_postings_day
    ON paper_v2_postings (day_id)
    WHERE day_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_postings_bucket
    ON paper_v2_postings (account_id, bucket);

-- ---------------------------------------------------------------------------
-- Decisions — long-lived engine decision history
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_decisions (
    decision_id      UUID         NOT NULL PRIMARY KEY,
    run_id           VARCHAR(128),
    account_id       UUID         REFERENCES paper_v2_accounts (account_id) ON DELETE SET NULL,
    day_id           UUID         REFERENCES paper_v2_days (day_id) ON DELETE SET NULL,
    reason_code      VARCHAR(48)  NOT NULL
        CHECK (reason_code IN ('waiting_regime', 'waiting_trigger', 'waiting_data',
                               'waiting_warmup', 'no_trade', 'cooldown',
                               'risk_blocked', 'data_stale', 'paused',
                               'day_ending', 'day_closed', 'recovery_pending',
                               'settlement_pending', 'engine_error', 'ai_timeout',
                               'ai_quota_exhausted', 'ai_invalid_response',
                               'expired_plan', 'insufficient_data', 'signal_expired')),
    reason_detail    TEXT,
    required_metrics JSONB        NOT NULL DEFAULT '{}'::jsonb,
    actual_metrics   JSONB        NOT NULL DEFAULT '{}'::jsonb,
    next_check       TIMESTAMPTZ,
    next_transition  VARCHAR(48),
    strategy_version VARCHAR(64)  NOT NULL DEFAULT 'baseline_v1',
    engine_version   VARCHAR(16)  NOT NULL DEFAULT '1',
    error            TEXT,
    decided_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    schema_version   INTEGER      NOT NULL DEFAULT 2
);

CREATE INDEX IF NOT EXISTS idx_decisions_account_time
    ON paper_v2_decisions (account_id, decided_at DESC);

CREATE INDEX IF NOT EXISTS idx_decisions_reason
    ON paper_v2_decisions (reason_code, decided_at DESC);

-- ---------------------------------------------------------------------------
-- Leases — fencing token for runner ownership
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_leases (
    lease_id         UUID         NOT NULL PRIMARY KEY,
    account_id       UUID         NOT NULL REFERENCES paper_v2_accounts (account_id) ON DELETE CASCADE,
    run_id           VARCHAR(128) NOT NULL,
    instance_id      VARCHAR(128) NOT NULL,
    fencing_token    BIGINT       NOT NULL,  -- monotonically increasing
    acquired_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ  NOT NULL,
    released_at      TIMESTAMPTZ,
    schema_version   INTEGER      NOT NULL DEFAULT 2
);

CREATE INDEX IF NOT EXISTS idx_leases_account_active
    ON paper_v2_leases (account_id, expires_at DESC)
    WHERE released_at IS NULL;

-- ---------------------------------------------------------------------------
-- Heartbeats — runtime health (age ≤15s healthy, 15–30 delayed, >30 unconfirmed)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_heartbeats (
    heartbeat_id          UUID         NOT NULL PRIMARY KEY,
    run_id                VARCHAR(128) NOT NULL,
    instance_id           VARCHAR(128) NOT NULL,
    account_id            UUID         REFERENCES paper_v2_accounts (account_id) ON DELETE SET NULL,
    lease_token           VARCHAR(128) NOT NULL,
    started_at            TIMESTAMPTZ,
    completed_at          TIMESTAMPTZ,
    last_success          TIMESTAMPTZ,
    last_error            TEXT,
    engine_version        VARCHAR(16)  NOT NULL DEFAULT '1',
    strategy_version      VARCHAR(64)  NOT NULL DEFAULT 'baseline_v1',
    data_freshness_seconds NUMERIC(10,3),
    processed_sequence    INTEGER      NOT NULL DEFAULT 0,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    schema_version        INTEGER      NOT NULL DEFAULT 2
);

CREATE INDEX IF NOT EXISTS idx_heartbeats_run_time
    ON paper_v2_heartbeats (run_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_heartbeats_account_time
    ON paper_v2_heartbeats (account_id, created_at DESC)
    WHERE account_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Reports — immutable financial report per day/account
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_reports (
    report_id            UUID         NOT NULL PRIMARY KEY,
    day_id               UUID         NOT NULL REFERENCES paper_v2_days (day_id) ON DELETE RESTRICT,
    account_id           UUID         NOT NULL REFERENCES paper_v2_accounts (account_id) ON DELETE RESTRICT,
    revision             INTEGER      NOT NULL DEFAULT 1,
    ledger_cutoff        TIMESTAMPTZ  NOT NULL,
    opening_equity       NUMERIC(20,8) NOT NULL,
    closing_equity       NUMERIC(20,8) NOT NULL,
    realized_gross       NUMERIC(20,8) NOT NULL DEFAULT 0,
    realized_net         NUMERIC(20,8) NOT NULL DEFAULT 0,
    unrealized           NUMERIC(20,8) NOT NULL DEFAULT 0,
    total_fees           NUMERIC(20,8) NOT NULL DEFAULT 0,
    total_funding        NUMERIC(20,8) NOT NULL DEFAULT 0,
    slippage_attribution NUMERIC(20,8) NOT NULL DEFAULT 0,
    max_drawdown         NUMERIC(20,8) NOT NULL DEFAULT 0,
    trades_count         INTEGER      NOT NULL DEFAULT 0,
    wins                 INTEGER      NOT NULL DEFAULT 0,
    losses               INTEGER      NOT NULL DEFAULT 0,
    breakeven            INTEGER      NOT NULL DEFAULT 0,
    liquidation_count    INTEGER      NOT NULL DEFAULT 0,
    rejected_entries     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    reconciliation_pass  BOOLEAN      NOT NULL DEFAULT FALSE,
    review_status        VARCHAR(20)  NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending', 'reviewed', 'approved', 'rejected')),
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    schema_version       INTEGER      NOT NULL DEFAULT 2,
    UNIQUE (day_id, account_id, revision)
);

CREATE INDEX IF NOT EXISTS idx_reports_day
    ON paper_v2_reports (day_id, account_id);

-- ---------------------------------------------------------------------------
-- Strategy versions — candidate/active tracking for learning
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_strategy_versions (
    version_id      UUID         NOT NULL PRIMARY KEY,
    strategy_version VARCHAR(64) NOT NULL UNIQUE,
    parent_version  VARCHAR(64),
    status          VARCHAR(20)  NOT NULL DEFAULT 'candidate'
        CHECK (status IN ('candidate', 'validating', 'approved', 'rejected',
                          'active', 'rolled_back')),
    parameters      JSONB        NOT NULL DEFAULT '{}'::jsonb,
    evaluation_evidence JSONB    NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    activated_at    TIMESTAMPTZ,
    schema_version  INTEGER      NOT NULL DEFAULT 2
);

CREATE INDEX IF NOT EXISTS idx_strategy_versions_status
    ON paper_v2_strategy_versions (status);

-- ---------------------------------------------------------------------------
-- Evaluations — candidate evaluation records
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_evaluations (
    evaluation_id   UUID         NOT NULL PRIMARY KEY,
    candidate_version VARCHAR(64) NOT NULL REFERENCES paper_v2_strategy_versions (strategy_version) ON DELETE CASCADE,
    baseline_version  VARCHAR(64) NOT NULL,
    split_config    JSONB        NOT NULL DEFAULT '{}'::jsonb,
    replay_metrics  JSONB        NOT NULL DEFAULT '{}'::jsonb,
    holdout_metrics JSONB        NOT NULL DEFAULT '{}'::jsonb,
    gate_replay_pass   BOOLEAN   NOT NULL DEFAULT FALSE,
    gate_holdout_pass  BOOLEAN   NOT NULL DEFAULT FALSE,
    insufficient_data  BOOLEAN   NOT NULL DEFAULT FALSE,
    deferred           BOOLEAN   NOT NULL DEFAULT FALSE,
    verdict         VARCHAR(20)  NOT NULL DEFAULT 'pending'
        CHECK (verdict IN ('pending', 'approved', 'rejected', 'insufficient_data', 'deferred')),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    schema_version  INTEGER      NOT NULL DEFAULT 2
);

CREATE INDEX IF NOT EXISTS idx_evaluations_candidate
    ON paper_v2_evaluations (candidate_version);

-- ---------------------------------------------------------------------------
-- Cutovers — migration cutover markers (additive, reversible)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_cutovers (
    cutover_id      UUID         NOT NULL PRIMARY KEY,
    owner_id        UUID         NOT NULL REFERENCES paper_v2_owners (owner_id) ON DELETE RESTRICT,
    marker          VARCHAR(64)  NOT NULL,  -- e.g. 'new_entries_enabled'
    enabled         BOOLEAN      NOT NULL DEFAULT FALSE,
    applied_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    rolled_back_at  TIMESTAMPTZ,
    schema_version  INTEGER      NOT NULL DEFAULT 2,
    UNIQUE (owner_id, marker)
);

CREATE INDEX IF NOT EXISTS idx_cutovers_owner
    ON paper_v2_cutovers (owner_id, enabled);

-- ---------------------------------------------------------------------------
-- Events — domain event log (optional audit trail)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS paper_v2_events (
    event_id        UUID         NOT NULL PRIMARY KEY,
    account_id      UUID         REFERENCES paper_v2_accounts (account_id) ON DELETE SET NULL,
    day_id          UUID         REFERENCES paper_v2_days (day_id) ON DELETE SET NULL,
    event_type      VARCHAR(48)  NOT NULL,
    payload         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    occurred_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    schema_version  INTEGER      NOT NULL DEFAULT 2
);

CREATE INDEX IF NOT EXISTS idx_events_account_time
    ON paper_v2_events (account_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_type
    ON paper_v2_events (event_type, occurred_at DESC);

-- ---------------------------------------------------------------------------
-- Record schema version
-- ---------------------------------------------------------------------------
INSERT INTO paper_v2_schema_version (version, description)
VALUES (2, 'paper_v2 initial DDL: owners, accounts, days, commands, signals, orders, fills, positions, postings, decisions, leases, heartbeats, reports, strategy_versions, evaluations, cutovers, events')
ON CONFLICT (version) DO NOTHING;

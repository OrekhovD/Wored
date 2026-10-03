-- V3 P3 — simulation session constructor (ТЗ V3 §5, §8: additive only)
--
-- New umbrella objects. Financial truth stays in paper_v2_* tables; these
-- tables bind days/orders to one session_id later (P5) and must not duplicate
-- orders/fills/postings.
--
-- Rollback plan: DROP TABLE simulation_sessions, simulation_plan_versions;
-- no existing table is altered, so backfill is not required. Already-saved
-- forecasts/days are unaffected.

CREATE TABLE IF NOT EXISTS simulation_plan_versions (
    id              UUID PRIMARY KEY,
    owner_id        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','approved','superseded','rejected')),
    plan            JSONB,                    -- normalized SimulationPlanV1 (Decimal strings, ISO-UTC)
    plan_hash       TEXT,                     -- sha256:... over the canonical plan; pinned at approve
    violations      JSONB NOT NULL DEFAULT '[]'::jsonb,
    version         INTEGER NOT NULL DEFAULT 1,
    parent_id       UUID REFERENCES simulation_plan_versions(id),
    consumed_by_sessions UUID[] NOT NULL DEFAULT ARRAY[]::uuid[],
    schema_version  INTEGER NOT NULL DEFAULT 1,
    approved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_plan_owner_status
    ON simulation_plan_versions (owner_id, status, created_at);
CREATE INDEX IF NOT EXISTS ix_plan_hash
    ON simulation_plan_versions (plan_hash);

CREATE TABLE IF NOT EXISTS simulation_sessions (
    id                   UUID PRIMARY KEY,
    owner_id             TEXT NOT NULL,
    plan_id              UUID NOT NULL REFERENCES simulation_plan_versions(id),
    plan_hash            TEXT NOT NULL,       -- frozen copy: the exact approved bytes
    mode                 TEXT NOT NULL CHECK (mode IN ('live_paper','historical_replay')),
    instrument_key       TEXT NOT NULL,
    start_at             TIMESTAMPTZ NOT NULL,
    end_at               TIMESTAMPTZ NOT NULL,
    timezone             TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'starting'
                         CHECK (status IN ('starting','running','closing',
                                           'settlement_pending','closed',
                                           'rejected','expired')),
    idempotency_key      TEXT NOT NULL,
    request_fingerprint  TEXT NOT NULL,       -- same key+payload replays; different payload 409s
    state_log            JSONB NOT NULL DEFAULT '[]'::jsonb,  -- [{from,to,reason,at}] every edge stamped
    schema_version       INTEGER NOT NULL DEFAULT 1,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_simulation_idempotency UNIQUE (owner_id, idempotency_key),
    CONSTRAINT ck_session_window CHECK (end_at > start_at)
);

CREATE INDEX IF NOT EXISTS ix_session_owner_status
    ON simulation_sessions (owner_id, status, created_at);

-- P4 — AI proposals (draft only; approval/start remain separate user acts)
CREATE TABLE IF NOT EXISTS simulation_plan_proposals (
    id              UUID PRIMARY KEY,
    owner_id        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','completed','failed')),
    questionnaire   JSONB NOT NULL,
    result          JSONB,   -- draft plan + rationale + sources + validator veto
    reason_code     TEXT,    -- llm_timeout|llm_quota|llm_invalid_response|llm_unavailable
    reason_message  TEXT,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_proposal_owner_status
    ON simulation_plan_proposals (owner_id, status, created_at);

-- V3 P6 — immutable session/day reports (ТЗ V3 §8; §9 MC-18, MC-19): additive only
--
-- Financial truth stays in paper_v2_*; this table stores the *frozen* report
-- snapshot (SessionReportV1) together with a canonical sha256 over its JSON.
-- Rows are insert-only: the repository never UPDATEs this table, so a later
-- session or a new ledger write can never mutate an already-saved report
-- (MC-19). The unique guard below is the DB-level backstop for that promise —
-- re-finalising the same (session, scope, day, schema) slot cannot overwrite
-- the first row; it must use a fresh id.
--
-- Rollback plan: DROP TABLE simulation_reports; no existing table is altered
-- and no column is added to a live table, so no backfill is required. The
-- paper_v2_* ledger and the simulation_* session tables are untouched; only
-- already-materialised report snapshots would be lost if this table were
-- intentionally dropped, and they can be regenerated from the ledger.

CREATE TABLE IF NOT EXISTS simulation_reports (
    id                   UUID PRIMARY KEY,
    owner_id             TEXT NOT NULL,
    session_id           TEXT NOT NULL,
    day_id               TEXT,                     -- NULL for a whole-session report
    scope                TEXT NOT NULL CHECK (scope IN ('day','session')),
    report               JSONB NOT NULL,           -- canonical SessionReportV1.to_dict()
    report_hash          TEXT NOT NULL,            -- sha256:... over the canonical JSON
    is_final             BOOLEAN NOT NULL DEFAULT FALSE,
    schema_version       INTEGER NOT NULL DEFAULT 1,
    engine_version       TEXT,
    simulator_version    TEXT,
    plan_hash            TEXT,
    replay_data_hash     TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- DB-level immutability guard: one slot may hold exactly one report. day_id is
-- NULL for the session scope, so COALESCE folds it into a stable key; a second
-- insert for the same slot is rejected (the store uses ON CONFLICT DO NOTHING
-- to keep the original bytes rather than mutate history).
CREATE UNIQUE INDEX IF NOT EXISTS uq_report_slot
    ON simulation_reports (session_id, scope, COALESCE(day_id, ''), schema_version);

CREATE INDEX IF NOT EXISTS ix_reports_owner_session
    ON simulation_reports (owner_id, session_id, created_at);
CREATE INDEX IF NOT EXISTS ix_reports_hash
    ON simulation_reports (report_hash);

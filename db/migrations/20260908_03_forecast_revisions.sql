-- R06: History, revisions, and metrics
-- Idempotent ALTER TABLE for new columns on forecast_requests

-- Add parent_request_id (nullable FK self-reference)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'forecast_requests' AND column_name = 'parent_request_id'
    ) THEN
        ALTER TABLE forecast_requests ADD COLUMN parent_request_id INTEGER NULL
            REFERENCES forecast_requests(id) ON DELETE SET NULL;
    END IF;
END $$;

-- Add revision_number (default 0 for original requests)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'forecast_requests' AND column_name = 'revision_number'
    ) THEN
        ALTER TABLE forecast_requests ADD COLUMN revision_number INTEGER NOT NULL DEFAULT 0;
    END IF;
END $$;

-- Add revision_reason (nullable text)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'forecast_requests' AND column_name = 'revision_reason'
    ) THEN
        ALTER TABLE forecast_requests ADD COLUMN revision_reason TEXT NULL;
    END IF;
END $$;

-- Add input_snapshot_id (nullable UUID)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'forecast_requests' AND column_name = 'input_snapshot_id'
    ) THEN
        ALTER TABLE forecast_requests ADD COLUMN input_snapshot_id UUID NULL;
    END IF;
END $$;

-- Index for revision chain lookups
CREATE INDEX IF NOT EXISTS idx_forecast_requests_parent ON forecast_requests (parent_request_id);

-- Index for revision_number within a parent chain
CREATE INDEX IF NOT EXISTS idx_forecast_requests_revision ON forecast_requests (parent_request_id, revision_number);

-- Add metrics_version to forecast_points if not already present (should be from R04)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'forecast_points' AND column_name = 'metrics_version'
    ) THEN
        ALTER TABLE forecast_points ADD COLUMN metrics_version INTEGER NOT NULL DEFAULT 1;
    END IF;
END $$;

-- Add baseline_error_pct to forecast_points if not present
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'forecast_points' AND column_name = 'baseline_error_pct'
    ) THEN
        ALTER TABLE forecast_points ADD COLUMN baseline_error_pct DECIMAL(12,6);
    END IF;
END $$;

-- Add skill_vs_baseline to forecast_points if not present
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'forecast_points' AND column_name = 'skill_vs_baseline'
    ) THEN
        ALTER TABLE forecast_points ADD COLUMN skill_vs_baseline DOUBLE PRECISION;
    END IF;
END $$;

-- Legacy pending cleanup: mark stale pending jobs as failed
-- Create backup table idempotently
CREATE TABLE IF NOT EXISTS forecast_jobs_legacy_backup (
    request_id INTEGER NOT NULL,
    old_state TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    backup_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Backup before modifying (idempotent: only rows not already backed up)
INSERT INTO forecast_jobs_legacy_backup (request_id, old_state, created_at)
SELECT fj.request_id, fj.state, fj.created_at
FROM forecast_jobs fj
WHERE fj.state IN ('queued', 'pending')
  AND fj.deadline_at <= NOW()
  AND NOT EXISTS (
      SELECT 1 FROM forecast_jobs_legacy_backup b
      WHERE b.request_id = fj.request_id AND b.old_state = fj.state
  );

-- Mark stale pending jobs as failed
UPDATE forecast_jobs
SET state = 'failed',
    error_code = 'legacy_missing_payload',
    finished_at = NOW()
WHERE state IN ('queued', 'pending')
  AND deadline_at <= NOW();

-- Mark corresponding forecast_requests as failed
UPDATE forecast_requests fr
SET status = 'failed', updated_at = NOW()
FROM forecast_jobs fj
WHERE fj.request_id = fr.id
  AND fj.state = 'failed'
  AND fj.error_code = 'legacy_missing_payload'
  AND fr.status IN ('pending', 'active');
-- 20260926: align forecast_jobs.deadline_at default with the code contract.
-- webui/forecast_queue.py declares the queue DDL with INTERVAL '30 minutes',
-- but the live table was created by scripts/migrate_stabilization.py with
-- INTERVAL '20 minutes' (CREATE TABLE IF NOT EXISTS never alters existing
-- tables). Enqueue does not pass deadline_at, so the effective TTL was 20 min.
-- Additive-only: changes the column default; no rows are touched.
ALTER TABLE forecast_jobs
    ALTER COLUMN deadline_at SET DEFAULT NOW() + INTERVAL '30 minutes';

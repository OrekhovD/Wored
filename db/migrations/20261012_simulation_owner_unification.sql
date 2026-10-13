-- P3b — unify the simulation plan-domain owner with the canonical owner key
--
-- Why: webui/simulation_api._owner_id used to build a *second* identity
-- namespace for the same human ("password:<username>" / "telegram:<user_id>"),
-- while paper_trading, webui/paper_api, webui/positions_workspace and the
-- Telegram bot all resolve uuid5(NAMESPACE_URL, "wored:owner:{...}").  A bot
-- write path (P3c) would therefore have created plans the dashboard could not
-- see, and vice versa.  _owner_id now returns the canonical uuid5 value.
--
-- Column type is TEXT on every affected table (20260929_simulation_v3.sql:13,
-- 35, 63; 20260930_simulation_reports.sql:19), so this is a value re-key, not
-- a schema change.  Nothing is dropped and no row is deleted.
--
-- The literal UUIDs below are the deterministic uuid5 outputs, computed with
-- the same expression the application uses:
--   uuid5(NAMESPACE_URL, "wored:owner:admin")
--     = 4f4d18fb-a0bc-5a1a-aaa0-09f86fde8a03
-- Verify before and after with:
--   SELECT owner_id, count(*) FROM simulation_plan_versions GROUP BY 1;
--
-- Rollback plan: the reverse UPDATE in the "ROLLBACK" block at the bottom
-- restores the legacy string.  It is safe only while one legacy owner maps to
-- one canonical owner (true for the data this migration was written against).
--
-- Idempotent: re-running matches zero rows.

BEGIN;

-- Pre-flight: uq_simulation_idempotency is (owner_id, idempotency_key); if two
-- different legacy owners shared an idempotency key, collapsing them would
-- violate it.  This must return 0 rows before the UPDATE is allowed to commit.
SELECT a.owner_id AS legacy_a, b.owner_id AS legacy_b, a.idempotency_key
FROM simulation_sessions a
JOIN simulation_sessions b
  ON a.idempotency_key = b.idempotency_key AND a.owner_id < b.owner_id
WHERE a.owner_id LIKE 'password:%' OR a.owner_id LIKE 'telegram:%'
ORDER BY 1, 2;

-- plan versions (drafts and approved, version chain included)
UPDATE simulation_plan_versions
   SET owner_id = '4f4d18fb-a0bc-5a1a-aaa0-09f86fde8a03'
 WHERE owner_id = 'password:admin';

-- sessions
UPDATE simulation_sessions
   SET owner_id = '4f4d18fb-a0bc-5a1a-aaa0-09f86fde8a03'
 WHERE owner_id = 'password:admin';

-- agent questionnaires / proposals
UPDATE simulation_plan_proposals
   SET owner_id = '4f4d18fb-a0bc-5a1a-aaa0-09f86fde8a03'
 WHERE owner_id = 'password:admin';

-- reports (0 rows as of 2026-10-12, kept for completeness)
UPDATE simulation_reports
   SET owner_id = '4f4d18fb-a0bc-5a1a-aaa0-09f86fde8a03'
 WHERE owner_id = 'password:admin';

-- Post-flight: must return 0 rows.  Any leftover prefixed owner means a legacy
-- identity this migration did not cover; map it explicitly instead of adding a
-- blind wildcard, because uuid5 cannot be computed inside PostgreSQL.
SELECT 'simulation_plan_versions' AS table_name, owner_id, count(*)
FROM simulation_plan_versions
WHERE owner_id LIKE 'password:%' OR owner_id LIKE 'telegram:%'
GROUP BY 2
UNION ALL
SELECT 'simulation_sessions', owner_id, count(*) FROM simulation_sessions
WHERE owner_id LIKE 'password:%' OR owner_id LIKE 'telegram:%' GROUP BY 2
UNION ALL
SELECT 'simulation_plan_proposals', owner_id, count(*) FROM simulation_plan_proposals
WHERE owner_id LIKE 'password:%' OR owner_id LIKE 'telegram:%' GROUP BY 2
UNION ALL
SELECT 'simulation_reports', owner_id, count(*) FROM simulation_reports
WHERE owner_id LIKE 'password:%' OR owner_id LIKE 'telegram:%' GROUP BY 2;

COMMIT;

-- ROLLBACK (manual, only while the 1:1 mapping holds):
-- BEGIN;
-- UPDATE simulation_plan_versions     SET owner_id = 'password:admin' WHERE owner_id = '4f4d18fb-a0bc-5a1a-aaa0-09f86fde8a03';
-- UPDATE simulation_sessions          SET owner_id = 'password:admin' WHERE owner_id = '4f4d18fb-a0bc-5a1a-aaa0-09f86fde8a03';
-- UPDATE simulation_plan_proposals    SET owner_id = 'password:admin' WHERE owner_id = '4f4d18fb-a0bc-5a1a-aaa0-09f86fde8a03';
-- UPDATE simulation_reports           SET owner_id = 'password:admin' WHERE owner_id = '4f4d18fb-a0bc-5a1a-aaa0-09f86fde8a03';
-- COMMIT;

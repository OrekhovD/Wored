-- V3 G2 step 3 — perpetual identity on forecast_requests (ТЗ V3 §51/§55/§57, §117): additive only
--
-- Why these columns exist.  ``forecast_requests`` describes a request in the
-- legacy vocabulary: ``symbol`` (``btcusdt``), an integer ``horizon_hours`` and a
-- canonical ``base_timeframe``.  That vocabulary cannot express a V3 request
-- honestly: the perpetual identity (``htx:linear-swap:BTC-USDT``), the chart
-- period (``5m``/``1d`` are *not* legacy timeframes), the horizon as a duration
-- rather than whole hours (a 15m horizon collapses to ``horizon_hours = 1``) and
-- which fact snapshot the base came from.  Until now those four facts lived only
-- inside the ``forecast_jobs`` JSONB payload, so the stored *row* could not prove
-- what instrument family it described — exactly the ambiguity ТЗ §51 forbids.
--
-- The change is strictly additive: nullable columns, no backfill, no rewrite of
-- legacy rows, no type change, no DROP.  Legacy rows keep NULL here and remain
-- readable by every existing query; a V3 request writes them and thereby makes
-- its own identity auditable in the row, not only in the job payload.
--
-- Idempotent and safe to re-apply: ``ADD COLUMN IF NOT EXISTS``,
-- ``CREATE INDEX IF NOT EXISTS``, and CHECK constraints wrapped in DO blocks that
-- swallow ``duplicate_object``.
--
-- Enforcement split (deliberate): ``webui/forecast_schema.py`` is executed by
-- ``app.ensure_prediction_schema`` which splits the script on ";", so that file
-- may contain only single-statement DDL — plain ADD COLUMN / CREATE INDEX.  The
-- domain CHECK constraints therefore live *here* (psql handles the ``$$`` bodies).
-- The application layer is the real gate in any case: ``forecast_command_v3``
-- validates period against the registry, and horizon↔period against the matrix,
-- *before* it inserts.
--
-- Rollback plan: the columns are nullable and unread by legacy code, so
-- ``ALTER TABLE forecast_requests DROP COLUMN IF EXISTS <col>`` for each of the
-- six columns plus ``DROP INDEX IF EXISTS idx_forecast_requests_v3_identity``
-- reverts this file completely.  No legacy column was modified, so no data
-- recovery is required; the JSONB job payload still holds the same facts, which
-- is what makes the rollback non-destructive.

ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS instrument_key TEXT;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS period TEXT;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS horizon TEXT;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS horizon_steps INTEGER;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS base_snapshot_id TEXT;
ALTER TABLE forecast_requests ADD COLUMN IF NOT EXISTS market_data_source TEXT;

-- Domain guards.  NULL is always allowed (legacy rows and non-V3 sources).
DO $$ BEGIN
    ALTER TABLE forecast_requests
        ADD CONSTRAINT forecast_requests_instrument_key_family
        CHECK (instrument_key IS NULL OR instrument_key ~ '^[a-z0-9]+:[a-z0-9\-]+:[A-Z0-9]+\-?[A-Z0-9]*$');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE forecast_requests
        ADD CONSTRAINT forecast_requests_period_domain
        CHECK (period IS NULL OR period IN ('1m', '5m', '15m', '1h', '4h', '1d'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE forecast_requests
        ADD CONSTRAINT forecast_requests_horizon_domain
        CHECK (horizon IS NULL OR horizon IN ('15m', '1h', '4h', '24h'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE forecast_requests
        ADD CONSTRAINT forecast_requests_horizon_steps_range
        CHECK (horizon_steps IS NULL OR (horizon_steps BETWEEN 1 AND 48));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- V3 reads slice history by instrument identity; partial so legacy rows are not
-- indexed and the legacy planner is unaffected.
CREATE INDEX IF NOT EXISTS idx_forecast_requests_v3_identity
    ON forecast_requests (instrument_key, period, horizon, created_at DESC)
    WHERE instrument_key IS NOT NULL;

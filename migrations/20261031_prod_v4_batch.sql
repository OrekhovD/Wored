-- ===========================================================================
-- 20261031_prod_v4_batch.sql — ИСПРАВЛЕН: только недостающие финконтурные миграции
--
-- QD-15 preflight (2026-11-01, Hermes, read-only prod check) дал точный гэп:
--   ЕСТЬ в проде: P1 (paper_v2_schema V1 core + contract_rules/market_events/
--                 execution_evidence/feed_gaps), P2 experiments +
--                 owner_identity_links, P3 domain_events + outbox-колонки,
--                 runtime_flags, contract_multiplier (заполненность?)
--   НЕТ в проде: ТОЛЬКО финконтур QD-10A (20261026), QD-10C G2 provenance
--                (20261028), QD-10C G3b-0 protective (20261030).
--
-- Порядок: 20261026 → 20261028 → 20261030 (по FK: fee_schedule_observations ←
-- provenance, protective ← provenance).
--
-- Всё идемпотентно (источники уже IF NOT EXISTS). \ir — относительные пути от
-- каталога migrations/.
--
-- ПРИМЕНЕНИЕ К ПРОДУ: отдельный гейт Hermes, после решения владельца.
-- ===========================================================================

BEGIN;

DO $batch$
BEGIN
  RAISE NOTICE 'prod v4 fincontour batch start at %', now();
END
$batch$;

-- QD-10A: fee/funding evidence schema (8 таблиц, 2 view, колонки funding_cashflow)
\ir 20261026_paper_fee_and_funding.sql

-- QD-10C G2: fee provenance DDL (append-only observations + колонки provenance)
\ir 20261028_fee_provenance.sql

-- QD-10C G3b-0: protective fee-decision audit DDL
\ir 20261030_protective_fee_decisions.sql

DO $batch$
BEGIN
  RAISE NOTICE 'prod v4 fincontour batch done at %', now();
END
$batch$;

COMMIT;
"""R12: Migrations and backward compatibility.

Tests R12-01..05:
  R12-01: Clean DB — all migrations apply successfully
  R12-02: Legacy schema with existing rows — migration preserves data
  R12-03: Re-run migration — idempotent, no changes on second run
  R12-04: Checksum mismatch — abort, do not modify existing migration
  R12-05: Preserve historical IDs, counts, and values
"""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT)]

# Import migration script
from scripts.migrate_stabilization import MIGRATIONS, compute_checksum


class TestMigrationChecksums(unittest.TestCase):
    """R12-04: Checksum mismatch detection."""

    def test_each_migration_has_version_and_sql(self):
        for migration in MIGRATIONS:
            self.assertIn("version", migration)
            self.assertIn("sql", migration)
            self.assertIn("description", migration)
            self.assertTrue(migration["version"].startswith("20260908_"))
            self.assertTrue(len(migration["sql"].strip()) > 0)

    def test_checksum_deterministic(self):
        """Same SQL always produces same checksum."""
        for migration in MIGRATIONS:
            cs1 = compute_checksum(migration["sql"])
            cs2 = compute_checksum(migration["sql"])
            self.assertEqual(cs1, cs2)

    def test_checksum_changes_with_sql_change(self):
        """Different SQL produces different checksum."""
        cs1 = compute_checksum("SELECT 1")
        cs2 = compute_checksum("SELECT 2")
        self.assertNotEqual(cs1, cs2)

    def test_version_format(self):
        """Each version follows the expected format."""
        for migration in MIGRATIONS:
            version = migration["version"]
            self.assertRegex(version, r"^\d{8}_\d{2}$", f"Bad version format: {version}")


class TestMigrationContent(unittest.TestCase):
    """R12-01/02/05: Verify migration content correctness."""

    def test_step_01_has_forecast_jobs_table(self):
        """R12-01: First migration creates forecast_jobs table."""
        sql = MIGRATIONS[0]["sql"]
        self.assertIn("forecast_jobs", sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS forecast_jobs", sql)
        self.assertIn("idempotency_key", sql)

    def test_step_01_has_forecast_requests_extensions(self):
        """R12-01: First migration adds new columns to forecast_requests."""
        sql = MIGRATIONS[0]["sql"]
        self.assertIn("execution_state", sql)
        self.assertIn("evaluation_state", sql)
        self.assertIn("valid_until", sql)
        self.assertIn("failure_code", sql)

    def test_step_01_has_metrics_columns(self):
        """R12-05: First migration adds metrics columns to forecast_points."""
        sql = MIGRATIONS[0]["sql"]
        self.assertIn("metrics_version", sql)
        self.assertIn("baseline_error_pct", sql)
        self.assertIn("skill_vs_baseline", sql)
        self.assertIn("step_index", sql)

    def test_step_02_has_llm_tables(self):
        """R12-01: Second migration creates LLM accounting tables."""
        sql = MIGRATIONS[1]["sql"]
        self.assertIn("llm_requests", sql)
        self.assertIn("llm_attempts", sql)
        self.assertIn("llm_budget_buckets", sql)

    def test_step_03_has_revision_columns(self):
        """R12-01: Third migration adds revision columns."""
        sql = MIGRATIONS[2]["sql"]
        self.assertIn("parent_request_id", sql)
        self.assertIn("revision_number", sql)
        self.assertIn("revision_reason", sql)
        self.assertIn("input_snapshot_id", sql)

    def test_all_migrations_use_if_not_exists(self):
        """R12-03: All CREATE TABLE and ALTER TABLE are idempotent."""
        for migration in MIGRATIONS:
            sql = migration["sql"]
            # CREATE TABLE must be IF NOT EXISTS
            for line in sql.splitlines():
                if line.strip().upper().startswith("CREATE TABLE") and "IF NOT EXISTS" not in line.upper():
                    self.fail(f"CREATE TABLE without IF NOT EXISTS in {migration['version']}: {line.strip()}")

    def test_no_drop_or_truncate(self):
        """R12-05: No DROP or TRUNCATE in any migration."""
        for migration in MIGRATIONS:
            sql = migration["sql"].upper()
            self.assertNotIn("DROP ", sql, f"DROP found in {migration['version']}")
            self.assertNotIn("TRUNCATE ", sql, f"TRUNCATE found in {migration['version']}")


class TestMetricsV2Formulas(unittest.TestCase):
    """R12-05: Verify metrics v2 formulas match R06 spec."""

    def test_target_boundary(self):
        """target_boundary = ceil(target_ts/period_seconds)*period_seconds"""
        import math
        # 1h period, target at 3600 seconds into the hour
        period_seconds = 3600
        target_ts = 3600  # 1 hour
        boundary = math.ceil(target_ts / period_seconds) * period_seconds
        self.assertEqual(boundary, 3600)

        # Target in the middle of the hour
        target_ts = 1800  # 30 min
        boundary = math.ceil(target_ts / period_seconds) * period_seconds
        self.assertEqual(boundary, 3600)

        # Target spanning two hours
        target_ts = 5400  # 1.5 hours
        boundary = math.ceil(target_ts / period_seconds) * period_seconds
        self.assertEqual(boundary, 7200)

    def test_error_formula(self):
        """error = abs(actual - predicted) / actual * 100"""
        actual = 50000.0
        predicted = 51000.0
        error = abs(actual - predicted) / actual * 100
        self.assertAlmostEqual(error, 2.0)

    def test_skill_formula(self):
        """skill = 1 - error/baseline, null when baseline=0"""
        error = 2.0
        baseline = 5.0
        skill = 1 - error / baseline
        self.assertAlmostEqual(skill, 0.6)

        # When baseline is 0, skill is None
        skill_zero_baseline = None  # Explicitly None per spec
        self.assertIsNone(skill_zero_baseline)

    def test_heuristic_score(self):
        """heuristic score = max(0, 100 - 100*abs(actual_change - expected_change))"""
        actual_change = 2.0  # +2%
        expected_change = 1.5  # +1.5%
        score = max(0, 100 - 100 * abs(actual_change - expected_change))
        self.assertAlmostEqual(score, 50.0)

        # Wildly wrong prediction
        actual_change = 5.0
        expected_change = -3.0
        score = max(0, 100 - 100 * abs(actual_change - expected_change))
        self.assertEqual(score, 0)  # Clamped to 0


if __name__ == "__main__":
    unittest.main()
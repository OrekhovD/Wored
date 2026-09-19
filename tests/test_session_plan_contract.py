"""Plan acceptance, Telegram rendering and atomic publication regressions."""
import asyncio
import json
import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "chatbot"))
from services.plan_contract import entry_risk, plan_error, validate_plan
from services.plan_presenter import render_plan
from services.plan_store import decode, revision_candidate, save_plan


def session():
    now = datetime.now(timezone.utc)
    return {"id": str(uuid.uuid4()), "symbol": "BTCUSDT", "status": "idle",
            "session_start": now - timedelta(minutes=1), "session_end": now + timedelta(hours=8),
            "active_plan_version": 1, "initial_budget_usdt": 100, "risk_mode": "balanced",
            "trade_direction": "auto", "target_net_profit_usdt": 1.5,
            "cost_filter_enabled": True, "max_trade_duration_minutes": 15}


def entry():
    return {"side": "long", "entry_zone_from": 99, "entry_zone_to": 100,
            "stop_loss": 97, "invalidation_price": 98, "take_profit": [105, 108],
            "recommended_leverage": 10, "budget_share_pct": 15, "margin_mode": "isolated",
            "confirmation_rule": "close_above_zone_on_1m_and_rsi_gt_50", "reason_code": "ATR reference"}


def candidate():
    return {"market_regime": "trend_up", "thesis": "Старший тренд вверх, младший импульс слабый.",
            "primary_scenario": "Возврат выше зоны после закрытия свечи.",
            "alternative_scenario": "Отмена при потере уровня.",
            "no_trade_condition": "Нет подтверждения или недостаточна экономика.", "entries": [entry()]}


def context():
    return {"quality": "ready", "timestamp": datetime.now(timezone.utc).isoformat(), "timeframes": {}}


class PlanContractTests(unittest.TestCase):
    def test_reported_liquidation_before_stop_is_rejected(self):
        e = {**entry(), "entry_zone_from": 77420, "entry_zone_to": 77660,
             "stop_loss": 77080, "invalidation_price": 77200, "take_profit": [77980, 78680],
             "recommended_leverage": 100, "budget_share_pct": 25}
        result = entry_risk(e, {**session(), "risk_mode": "aggressive"})
        self.assertIn("stop_beyond_liquidation", result["errors"])
        self.assertIn("net_reward_risk_below_one", result["errors"])

    def test_positive_long_and_short_economics_include_costs(self):
        long = entry_risk(entry(), session())
        self.assertTrue(long["ok"], long)
        self.assertGreater(long["net_loss_sl_usdt"], 4.5)
        self.assertLess(long["net_profit_tp1_usdt"], 7.5)
        short = {**entry(), "side": "short", "entry_zone_from": 100, "entry_zone_to": 101,
                 "stop_loss": 103, "invalidation_price": 102, "take_profit": [95, 92],
                 "confirmation_rule": "close_below_zone_on_1m_and_rsi_lt_50"}
        self.assertTrue(entry_risk(short, session())["ok"])

    def test_invalid_numbers_rules_geometry_and_direction_fail_closed(self):
        for changes in ({"stop_loss": float("nan")}, {"entry_zone_from": True},
                        {"recommended_leverage": 125}, {"recommended_leverage": "10"},
                        {"entry_zone_from": 101}, {"take_profit": []}, {"take_profit": [105, 104]},
                        {"confirmation_rule": "any"}, {"confirmation_rule": "rsi_lt_50"},
                        {"margin_mode": "cross"}, {"budget_share_pct": 80}):
            with self.subTest(changes=changes):
                self.assertFalse(entry_risk({**entry(), **changes}, session())["ok"])
        self.assertIn("direction_mismatch", entry_risk(entry(), {**session(), "trade_direction": "short"})["errors"])

    def test_no_trade_allowed_in_aggressive_mode(self):
        raw = {**candidate(), "primary_scenario": "no_trade", "entries": []}
        plan = validate_plan(raw, {**session(), "risk_mode": "aggressive"}, context())
        self.assertEqual(plan["validation_status"], "no_trade")
        self.assertEqual(plan["entries"], [])

    def test_rejected_plan_keeps_evidence_without_orders_or_nan(self):
        raw = {**candidate(), "entries": [{**entry(), "stop_loss": float("nan")}], "model_used": "forged"}
        plan = validate_plan(raw, session(), context())
        self.assertEqual(plan["validation_status"], "rejected")
        self.assertEqual(plan["entries"], [])
        self.assertEqual(len(plan["rejected_entries"]), 1)
        self.assertNotIn("model_used", plan)
        json.dumps(plan, allow_nan=False)

    def test_expired_and_terminal_sessions_cannot_generate(self):
        for changes in ({"status": "completed"}, {"status": "failed"},
                        {"session_end": datetime.now(timezone.utc) - timedelta(seconds=1)}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_plan(candidate(), {**session(), **changes}, context())

    def test_wrong_version_legacy_and_expiry_block_execution(self):
        s = session()
        p = {**validate_plan(candidate(), s, context()), "version": 1}
        self.assertIsNone(plan_error(p, s))
        self.assertEqual(plan_error(p, {**s, "active_plan_version": 2}), "active_plan_version_missing")
        self.assertEqual(plan_error({**p, "schema_version": 1}, s), "plan_not_validated")
        self.assertEqual(plan_error({**p, "valid_until": s["session_start"].isoformat()}, s), "plan_expired")

    def test_actual_fill_risk_is_rechecked(self):
        self.assertTrue(entry_risk(entry(), session())["ok"])
        self.assertFalse(entry_risk(entry(), session(), fill_price=104.9)["ok"])

    def test_stale_snapshot_and_nonfinite_economics_are_rejected(self):
        old = {**context(), "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()}
        with self.assertRaisesRegex(ValueError, "market_snapshot_expired"):
            validate_plan(candidate(), session(), old)
        self.assertFalse(entry_risk(entry(), {**session(), "initial_budget_usdt": 1e308})["ok"])

    def test_empty_directional_plan_and_metadata_are_not_accepted(self):
        for raw in ([], {**candidate(), "entries": []}, {**candidate(), "thesis": None}):
            with self.assertRaises(ValueError):
                validate_plan(raw, session(), context())

    def test_revision_cannot_mutate_open_or_unknown_orders(self):
        revision = {"execution_command": "continue", "patch": {"update_entries": [{"entry_id": "open"}]}}
        with self.assertRaisesRegex(ValueError, "unknown_pending_entry"):
            revision_candidate(candidate(), [], revision)

    def test_telegram_html_escaping_limits_and_risk_content(self):
        s = session()
        raw = candidate()
        raw.update(thesis="<b>" * 500, alternative_scenario="&" * 1000)
        raw["entries"] *= 3
        for e in raw["entries"]:
            e["reason_code"] = "<&>" * 1000
        p = {**validate_plan(raw, s, context()), "version": 1, "model_used": "<script>"}
        rendered = render_plan(s, p, [])
        self.assertLessEqual(len(rendered), 4000)
        self.assertNotIn("<script>", rendered)
        self.assertIn("ликвидация", rendered)
        self.assertIn("Net R:R", rendered)
        self.assertIn("не подтверждает сделку", rendered)

    def test_prompt_formats_without_leverage_contradictions(self):
        from services.session_manager import PLAN_GENERATION_PROMPT
        prompt = PLAN_GENERATION_PROMPT.format(trade_direction="auto", trade_horizon="fast",
            risk_mode="aggressive", budget_usdt=100, target_net_profit_usdt=1.5, market_context="{}")
        self.assertNotIn("125", prompt)
        self.assertNotIn("200", prompt)
        self.assertIn("no_trade", prompt)


@unittest.skipUnless(os.getenv("WORED_TEST_DATABASE_URL"), "Dedicated wored_qa database required")
class PlanPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncpg
        from services.pipeline_schema import ensure_pipeline_tables
        dsn = os.environ["WORED_TEST_DATABASE_URL"]
        if urlsplit(dsn).path != "/wored_qa":
            raise RuntimeError("Only the disposable wored_qa database is allowed")
        self.schema = "qa_plan_" + uuid.uuid4().hex
        self.admin = await asyncpg.connect(dsn)
        await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        self.pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4,
                                             server_settings={"search_path": self.schema})
        self.binding = patch("storage.postgres_client.get_pool", AsyncMock(return_value=self.pool))
        self.binding.start()
        await ensure_pipeline_tables()
        self.sid = str(await self.pool.fetchval("INSERT INTO trading_sessions(user_id,symbol,session_start,"
            "session_end,initial_budget_usdt,status) VALUES(42,'BTCUSDT',NOW(),NOW()+INTERVAL '8 hours',100,'idle') RETURNING id"))
        self.session = dict(await self.pool.fetchrow("SELECT * FROM trading_sessions WHERE id=$1", self.sid))

    async def asyncTearDown(self):
        self.binding.stop()
        await self.pool.close()
        # Leave the tiny per-test schema in tmpfs for diagnosis; no deletion of user data.
        await self.admin.close()

    async def publish(self, raw=None):
        checked = validate_plan(raw or candidate(), self.session, context())
        return await save_plan(self.pool, self.session, checked, "qa-model", initial=True)

    async def test_concurrent_initial_plans_publish_once(self):
        results = await asyncio.gather(self.publish(), self.publish())
        self.assertEqual(sum("error" not in r for r in results), 1, results)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM session_plans"), 1)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM planned_entries"), 1)

    async def test_expiry_during_generation_is_checked_under_lock(self):
        await self.pool.execute("UPDATE trading_sessions SET session_end=NOW()-INTERVAL '1 second'")
        result = await self.publish()
        self.assertEqual(result["error"], "session_expired")
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM session_plans"), 0)

    async def test_event_failure_rolls_back_plan_orders_and_state(self):
        import asyncpg
        await self.pool.execute("ALTER TABLE execution_events ADD CONSTRAINT qa_fail CHECK(event_type <> 'plan_validated')")
        with self.assertRaises(asyncpg.CheckViolationError):
            await self.publish()
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM session_plans"), 0)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM planned_entries"), 0)
        self.assertEqual(await self.pool.fetchval("SELECT status FROM trading_sessions"), "idle")

    async def test_rejected_and_no_trade_never_arm(self):
        raw = {**candidate(), "entries": [{**entry(), "recommended_leverage": 100}]}
        result = await self.publish(raw)
        self.assertEqual(result["validation_status"], "rejected")
        self.assertEqual(await self.pool.fetchval("SELECT status FROM trading_sessions"), "idle")
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM planned_entries"), 0)
        payload = decode(await self.pool.fetchval("SELECT plan_json FROM session_plans"))
        self.assertIn("stop_beyond_liquidation", payload["rejected_entries"][0]["risk"]["errors"])

    async def test_revision_publishes_complete_version_and_supersedes_pending(self):
        await self.publish()
        current = decode(await self.pool.fetchval("SELECT plan_json FROM session_plans"))
        pending = await self.pool.fetch("SELECT * FROM planned_entries")
        self.session = dict(await self.pool.fetchrow("SELECT * FROM trading_sessions"))
        revision = {"execution_command": "continue", "patch": {}}
        raw = revision_candidate(current, pending, revision)
        checked = validate_plan(raw, self.session, context())
        result = await save_plan(self.pool, self.session, checked, "qa-revision", initial=False, revision=revision,
                                 expected_pending=[str(e["id"]) for e in pending])
        self.assertEqual(result["version"], 2)
        self.assertEqual(await self.pool.fetchval("SELECT active_plan_version FROM trading_sessions"), 2)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM planned_entries WHERE plan_version=2 AND status='planned'"), 1)
        self.assertEqual(await self.pool.fetchval("SELECT status FROM planned_entries WHERE plan_version=1"), "superseded")
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM session_plans"), 2)

    async def test_control_command_keeps_plan_version_and_expired_resume_fails(self):
        from services.session_manager import apply_revision_command
        await self.publish()
        redis = AsyncMock()
        with patch("storage.redis_client.get_redis", return_value=redis):
            result = await apply_revision_command(self.sid, "pause")
            self.assertTrue(result["ok"])
            self.assertEqual(await self.pool.fetchval("SELECT active_plan_version FROM trading_sessions"), 1)
            self.assertEqual(await self.pool.fetchval("SELECT new_version FROM session_revisions"), 1)
            await self.pool.execute("UPDATE trading_sessions SET session_end=NOW()-INTERVAL '1 second'")
            result = await apply_revision_command(self.sid, "continue")
            self.assertIn("error", result)
            self.assertEqual(await self.pool.fetchval("SELECT status FROM trading_sessions"), "paused")

    async def test_missing_active_version_cannot_execute_or_bootstrap(self):
        from services.session_manager import bootstrap_session, execute_entry
        await self.publish()
        row = dict(await self.pool.fetchrow("SELECT * FROM planned_entries"))
        await self.pool.execute("UPDATE trading_sessions SET active_plan_version=2")
        result = await execute_entry(self.sid, row, {"closed": True, "close": 100.1}, 100, {"rsi": 55})
        self.assertEqual(result["reason"], "entry_version_mismatch")
        self.assertEqual((await bootstrap_session(self.sid))["error"], "active_plan_version_missing")
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM executed_trades"), 0)

    async def test_execution_requires_closed_candle_and_rechecks_actual_fill(self):
        from services.session_manager import execute_entry
        await self.publish()
        row = dict(await self.pool.fetchrow("SELECT * FROM planned_entries"))
        candle = {"open": 99, "high": 101, "low": 99, "close": 100.1}
        result = await execute_entry(self.sid, row, candle, 100, {"rsi": 55})
        self.assertEqual(result["reason"], "closed_candle_required")
        result = await execute_entry(self.sid, row, {**candle, "closed": True}, 100, {"rsi": 55})
        self.assertTrue(result.get("executed"), result)

    async def test_initial_generator_rejects_old_session_before_model_call(self):
        from services.session_manager import generate_initial_plan
        await self.pool.execute("UPDATE trading_sessions SET session_end=NOW()-INTERVAL '1 second'")
        with patch("services.session_manager.build_market_context", AsyncMock()) as market:
            result = await generate_initial_plan(self.sid)
            market.assert_not_called()
        self.assertEqual(result["error"], "session_expired")

    async def test_generation_to_telegram_without_network_or_real_orders(self):
        from types import SimpleNamespace
        from services.session_manager import generate_initial_plan, bootstrap_session
        from handlers.pipeline import _handle_plan
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(candidate()), reasoning=""))])))))
        cfg = SimpleNamespace(model_id="qa-model", timeout=2)
        with patch("ai.models.ANALYST_MODEL_CHAIN", ["qa"]), patch("ai.models.MODELS", {"qa": cfg}), \
             patch("ai.router.get_client", return_value=client), \
             patch("services.session_manager.build_market_context", AsyncMock(side_effect=lambda _: context())):
            result = await generate_initial_plan(self.sid)
            self.assertEqual(result["validation_status"], "accepted", result)
            self.assertTrue((await bootstrap_session(self.sid))["ok"])
            rendered = await _handle_plan(42)
        self.assertIn("Основной сценарий", rendered)
        self.assertIn("ликвидация", rendered)
        self.assertIn("qa-model", rendered)
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM executed_trades"), 0)

    async def test_revision_does_not_resurrect_consumed_order(self):
        await self.publish()
        self.session = dict(await self.pool.fetchrow("SELECT * FROM trading_sessions"))
        pending = await self.pool.fetch("SELECT id FROM planned_entries")
        await self.pool.execute("UPDATE planned_entries SET status='triggered'")
        result = await save_plan(self.pool, self.session, validate_plan(candidate(), self.session, context()),
                                 "qa-model", initial=False, revision={"execution_command": "continue"},
                                 expected_pending=[str(e["id"]) for e in pending])
        self.assertEqual(result["error"], "entries_changed_during_generation")
        self.assertEqual(await self.pool.fetchval("SELECT count(*) FROM session_plans"), 1)

    async def test_hourly_model_patch_publishes_full_plan_and_records_accuracy(self):
        from types import SimpleNamespace
        from services.session_manager import hourly_revision
        await self.publish()
        reply = {"execution_command": "continue", "summary": "Условия подтверждены", "patch": {}}
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(reply), reasoning=""))])))))
        with patch("ai.models.ANALYST_MODEL_CHAIN", ["qa"]), \
             patch("ai.models.MODELS", {"qa": SimpleNamespace(model_id="qa-model", timeout=2)}), \
             patch("ai.router.get_client", return_value=client), \
             patch("services.session_manager.build_market_context", AsyncMock(side_effect=lambda _: context())), \
             patch("services.plan_accuracy.record_plan_prediction", AsyncMock()) as accuracy:
            result = await hourly_revision(self.sid)
            self.assertEqual(result["new_version"], 2, result)
            accuracy.assert_awaited_once()
        saved = decode(await self.pool.fetchval("SELECT plan_json FROM session_plans WHERE version=2"))
        self.assertEqual(saved["validation_status"], "accepted")
        self.assertEqual(saved["entries"][0]["stop_loss"], 97)

    async def test_no_trade_bootstrap_stays_idle(self):
        from services.session_manager import bootstrap_session
        await self.publish({**candidate(), "entries": [], "primary_scenario": "no_trade"})
        result = await bootstrap_session(self.sid)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "idle")

    async def test_close_all_uses_fresh_price_and_never_resurrects_completed(self):
        from services.session_manager import apply_revision_command, execute_entry
        await self.publish()
        row = dict(await self.pool.fetchrow("SELECT * FROM planned_entries"))
        result = await execute_entry(self.sid, row, {"closed": True, "open": 99, "high": 101,
                                     "low": 99, "close": 100.1}, 100, {"rsi": 55})
        self.assertTrue(result.get("executed"))
        redis = AsyncMock()
        redis.get.return_value = "{}"
        with patch("storage.redis_client.get_redis", return_value=redis):
            result = await apply_revision_command(self.sid, "close_all")
            self.assertEqual(result["error"], "close_pending_fresh_price")
            self.assertEqual(await self.pool.fetchval("SELECT status FROM trading_sessions"), "paused")
            self.assertEqual(await self.pool.fetchval("SELECT status FROM executed_trades"), "open")
            redis.get.return_value = json.dumps({"price": 101, "timestamp": datetime.now(timezone.utc).isoformat()})
            result = await apply_revision_command(self.sid, "close_all")
            self.assertTrue(result["ok"], result)
            self.assertEqual(await self.pool.fetchval("SELECT status FROM executed_trades"), "closed")
            self.assertEqual(await self.pool.fetchval("SELECT status FROM trading_sessions"), "stopped")
            result = await apply_revision_command(self.sid, "close_all")
            self.assertEqual(result["error"], "session_ended")

    async def test_active_snapshot_agrees_with_telegram_rows(self):
        from services.session_manager import build_active_snapshot
        await self.publish()
        snapshot = await build_active_snapshot(self.sid)
        self.assertEqual(snapshot["session"]["activeplanversion"], snapshot["plan"]["version"])
        self.assertEqual(snapshot["plan"]["details"]["validation_status"], "accepted")
        self.assertEqual(snapshot["plan"]["entries"][0]["takeprofit"], [105, 108])


if __name__ == "__main__":
    unittest.main()

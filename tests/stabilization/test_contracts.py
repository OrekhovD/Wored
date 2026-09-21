"""Offline regression tests: no provider requests, Telegram messages or live data writes."""
import asyncio
import hashlib
import hmac
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "webui"), str(ROOT / "chatbot")]
from access_control import allowed_origin, verify_telegram, safe_next_url
from forecast_queue import process_one
from services.market_data import calculate_closed_indicators, fresh_ticker, read_market_context, timestamp_age
from services.sim_math import preview, settlement, validate_order


def signed_telegram(date=1000, user_id=42):
    data = {"auth_date": str(date), "user": json.dumps({"id": user_id})}
    message = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", b"test-token", hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


class AccessTests(unittest.TestCase):
    def test_valid_telegram_admin(self):
        self.assertEqual(verify_telegram(signed_telegram(), "test-token", {42}, now=1100)["user_id"], 42)

    def test_expired_and_future_telegram_are_rejected(self):
        for now in (999, 1301):
            self.assertIsNone(verify_telegram(signed_telegram(), "test-token", {42}, now=now))

    def test_non_admin_bad_signature_and_duplicate_fields_are_rejected(self):
        for data in (signed_telegram(user_id=43), signed_telegram() + "&auth_date=1000", signed_telegram().replace("hash=", "hash=x")):
            self.assertIsNone(verify_telegram(data, "test-token", {42}, now=1100))

    def test_origin_validation(self):
        self.assertTrue(allowed_origin("http://localhost:8080", "http://localhost:8080/api/close"))
        self.assertTrue(allowed_origin("https://dashboard.example", "http://webui:8000", "https://dashboard.example"))
        for origin in ("null", "", "https://localhost:8080", "http://localhost:8081", "https://evil.example", "https://localhost:bad"):
            self.assertFalse(allowed_origin(origin, "http://localhost:8080/api/close"))

    def test_login_redirect_cannot_leave_application(self):
        for value in ("//evil.test", "/\\evil.test", "https://evil.test", "/\r\nLocation: https://evil.test"):
            self.assertEqual(safe_next_url(value), "/")
        self.assertEqual(safe_next_url("/command-deck"), "/command-deck")


def candles(count=40):
    return [{"id": 6000 + i * 60, "open": 100 + i, "close": 100 + i, "high": 101 + i, "low": 99 + i} for i in range(count)]


class IndicatorTests(unittest.TestCase):
    def test_missing_future_and_old_ticker_are_not_fresh(self):
        for value in (None, 900, 1001, float("nan"), "invalid"):
            self.assertFalse(fresh_ticker({"price": 100, "timestamp": value}, now=1000))
        self.assertFalse(fresh_ticker({"price": float("nan"), "timestamp": 990}, now=1000))
        self.assertTrue(fresh_ticker({"price": 100, "timestamp": 990}, now=1000))
        self.assertIsNone(timestamp_age("2026-01-01T00:00:00", now=1000))

    def test_current_candle_never_changes_closed_indicators(self):
        baseline = calculate_closed_indicators(candles(40), "1m", now=8400)
        later = candles(41)
        later[-1]["close"] = 1e9
        self.assertEqual(baseline, calculate_closed_indicators(later, "1min", now=8400))
        self.assertEqual(baseline["as_of"], 8400)
        self.assertEqual(baseline["rsi"], 100)
        self.assertEqual(baseline["atr"], 2)

    def test_flat_prices_have_neutral_rsi(self):
        rows = [{**row, "close": 100, "high": 101, "low": 99} for row in candles()]
        result = calculate_closed_indicators(rows, "1min", now=8400)
        self.assertEqual(result["rsi"], 50)
        self.assertEqual(result["macd_hist"], 0)

    def test_gaps_duplicates_insufficient_and_invalid_prices_are_unavailable(self):
        for rows in (candles(10), candles()[:20] + candles()[21:], candles() + [candles()[5]],
                     [{**row, "close": float("nan")} for row in candles()]):
            self.assertEqual(calculate_closed_indicators(rows, "1min", now=9000), {})


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_data_is_null_and_blocks_planning(self):
        redis = AsyncMock()
        redis.get.return_value = None
        result = await read_market_context(redis, "btcusdt", now=10000)
        self.assertEqual(result["quality"], "unavailable")
        self.assertIsNone(result["price"])
        self.assertIsNone(result["timeframes"]["1m"]["rsi"])
        self.assertNotIn("none", result["risk_flags"])

    async def test_ready_snapshot_then_stale_publication(self):
        redis = AsyncMock()
        frames = {tf: {"as_of": 9980, "rsi": 50, "macd_hist": 0, "atr": 2} for tf in ("1m", "5m", "15m", "1h")}
        async def get(key):
            if key.startswith("ticker:"):
                return json.dumps({"price": 100, "timestamp": 9999})
            return json.dumps({"schema_version": 1, "published_at": 9999, "timeframes": frames})
        redis.get.side_effect = get
        self.assertEqual((await read_market_context(redis, "btcusdt", now=10000))["quality"], "ready")
        self.assertEqual((await read_market_context(redis, "btcusdt", now=10100))["quality"], "unavailable")


class SimulationTests(unittest.TestCase):
    def test_invalid_inputs_cannot_reach_storage(self):
        for direction, leverage, margin, price in (("invalid", 100, 10, 100), ("long", 200, 10, 100),
                ("long", True, 10, 100), ("long", 1.5, 10, 100), ("long", 100, -1, 100),
                ("long", 100, float("nan"), 100), ("long", 100, 10, float("inf"))):
            with self.assertRaises(ValueError):
                validate_order(direction, leverage, margin, price)

    def test_unchanged_price_has_net_loss_from_two_fees_and_funding(self):
        pnl, fee = settlement("long", 100, 100, 10, .6, .1)
        self.assertAlmostEqual(fee, .6)
        self.assertAlmostEqual(pnl, -1.3)

    def test_closing_fee_uses_closing_notional(self):
        pnl, fee = settlement("short", 100, 90, 10, .6)
        self.assertAlmostEqual(fee, .54)
        self.assertAlmostEqual(pnl, 98.86)

    def test_preview_limits_loss_at_simulated_liquidation(self):
        for direction, loss_key in (("long", "-5%"), ("short", "+5%")):
            result = preview(direction, 100, 10, 100)
            self.assertNotEqual(result["liquidation_price"], 100)
            self.assertTrue(result["scenario_liquidated"][loss_key])
            self.assertGreater(result["scenarios"][loss_key], -10)


class Connection:
    def __init__(self, row):
        self.fetchrow = AsyncMock(return_value=row)
        self.execute = AsyncMock()

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Pool:
    def __init__(self, row):
        self.connection = Connection(row)

    def acquire(self):
        return self.connection


class QueueTests(unittest.IsolatedAsyncioTestCase):
    def pool(self, expired=False):
        return Pool({"request_id": 7, "expired": expired, "payload": '{"horizon_steps": 4}'})

    async def test_empty_queue_does_not_call_model(self):
        runner = AsyncMock()
        self.assertFalse(await process_one(Pool(None), runner))
        runner.assert_not_awaited()

    async def test_completed_job_uses_same_connection_as_acknowledgement(self):
        pool, runner = self.pool(), AsyncMock()
        self.assertTrue(await process_one(pool, runner))
        runner.assert_awaited_once_with(7, {"horizon_steps": 4}, pool.connection)
        # Verify that a completed job updates state to 'completed'
        execute_calls = pool.connection.execute.call_args_list
        completed_calls = [call for call in execute_calls
                           if "completed" in str(call.args[0])]
        self.assertTrue(len(completed_calls) > 0, "Expected completed state update")

    async def test_expired_job_does_not_call_provider(self):
        pool, runner = self.pool(True), AsyncMock()
        await process_one(pool, runner)
        runner.assert_not_awaited()
        # Expired job should set error_code to deadline_exceeded
        execute_calls = pool.connection.execute.call_args_list
        deadline_calls = [call for call in execute_calls
                          if len(call.args) > 2 and call.args[2] == "deadline_exceeded"]
        self.assertTrue(len(deadline_calls) > 0, "Expected deadline_exceeded error_code")

    async def test_failure_is_terminal_and_does_not_expose_exception_message(self):
        pool = self.pool()
        await process_one(pool, AsyncMock(side_effect=ValueError("secret-provider-detail")))
        execute_calls = pool.connection.execute.call_args_list
        # Find the call that sets failure info
        failure_calls = [call for call in execute_calls
                          if len(call.args) > 2 and call.args[2] == "ValueError"]
        self.assertTrue(len(failure_calls) > 0, "Expected ValueError as error_code")

    async def test_timeout_is_terminal(self):
        pool = self.pool()
        async def hanging(*args):
            await asyncio.Event().wait()
        await process_one(pool, hanging, timeout=0.01)
        execute_calls = pool.connection.execute.call_args_list
        timeout_calls = [call for call in execute_calls
                          if len(call.args) > 2 and call.args[2] == "TimeoutError"]
        self.assertTrue(len(timeout_calls) > 0, "Expected TimeoutError as error_code")

    async def test_shutdown_does_not_acknowledge_job(self):
        pool = self.pool()
        with self.assertRaises(asyncio.CancelledError):
            await process_one(pool, AsyncMock(side_effect=asyncio.CancelledError))
        # CancelledError should not commit any state change for the job
        # The transaction context manager handles rollback


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_adapter_rejects_thinking_only_and_truncated_response(self):
        import os
        import httpx
        from prediction_engine import MODEL_CONFIGS, _build_runtime_candidates, _ollama_chat
        candidate = _build_runtime_candidates(MODEL_CONFIGS["analyst"])[0]
        context = {"horizon_steps": 1, "base_price": 100, "symbol": "btcusdt"}
        for body in ({"done": True, "message": {"thinking": "private", "reasoning": "private"}},
                     {"done": True, "done_reason": "length", "message": {"content": "{}"}},
                     {"done": False, "message": {"content": "{}"}}):
            client = AsyncMock()
            client.post.return_value = httpx.Response(200, json=body, request=httpx.Request("POST", "https://example.test"))
            manager = AsyncMock()
            manager.__aenter__.return_value = client
            with patch.dict(os.environ, {candidate.api_key_env: "test-token"}), patch("httpx.AsyncClient", return_value=manager):
                with self.assertRaises(ValueError):
                    await _ollama_chat(candidate, MODEL_CONFIGS["analyst"], context)


VALID_FORECAST_JSON = json.dumps({
    "summary": "flat",
    "points": [{"step": 1, "price": 101.0, "change_pct": 1.0,
                "confidence": 50, "low": 100.0, "high": 102.0}],
})


class LocalModelRoleTests(unittest.IsolatedAsyncioTestCase):
    """The workstation Ollama branch: opt-in chain, thinking off, no cloud keys."""

    def setUp(self):
        import os
        self.context = {"horizon_steps": 1, "base_price": 100, "symbol": "btcusdt"}
        # Cloud keys are cleared so a test cannot pass by silently using them.
        self.no_cloud = {k: "" for k in os.environ if k.startswith(("OLLAMA_", "NVIDIA_"))}
        self.no_cloud["LOCAL_LLM_ROLES"] = ""

    def _candidates(self, key, roles=""):
        import os
        from prediction_engine import MODEL_CONFIGS, _build_runtime_candidates
        env = dict(self.no_cloud, LOCAL_LLM_ROLES=roles)
        with patch.dict(os.environ, env, clear=False):
            return _build_runtime_candidates(MODEL_CONFIGS[key])

    def test_local_chain_is_off_by_default(self):
        self.assertNotIn("ollama_local", [c.provider for c in self._candidates("analyst")])

    def test_local_candidate_leads_only_the_configured_role(self):
        analyst = self._candidates("analyst", roles="analyst")
        premium = self._candidates("premium", roles="analyst")
        self.assertEqual(analyst[0].provider, "ollama_local")
        self.assertEqual(analyst[0].model_id, "bonsai-27b")
        self.assertEqual(analyst[0].timeout, 120.0)
        # The cloud chain of the same role must stay behind it, untouched.
        self.assertNotIn("ollama_local", [c.provider for c in analyst[1:]])
        self.assertNotIn("ollama_local", [c.provider for c in premium])

    def test_local_candidate_leads_every_role_for_all(self):
        providers = {key: self._candidates(key, roles="all")[0].provider
                     for key in ("worker", "analyst", "premium", "minimax")}
        self.assertEqual(providers, {"worker": "ollama_local", "analyst": "ollama_local",
                                     "premium": "ollama_local", "minimax": "ollama_local"})

    async def test_local_adapter_forces_thinking_off_and_appends_schema(self):
        import os
        import httpx
        from prediction_engine import MODEL_CONFIGS, _local_ollama_chat
        candidate = self._candidates("analyst", roles="analyst")[0]
        body = {"done": True, "done_reason": "stop", "message": {"content": VALID_FORECAST_JSON}}
        client = AsyncMock()
        client.post.return_value = httpx.Response(
            200, json=body, request=httpx.Request("POST", "http://127.0.0.1:8088/api/chat"))
        manager = AsyncMock()
        manager.__aenter__.return_value = client
        env = dict(self.no_cloud, LOCAL_LLM_ROLES="analyst",
                   LOCAL_LLM_BASE_URL="http://127.0.0.1:8088/v1")
        with patch.dict(os.environ, env, clear=False), patch("httpx.AsyncClient", return_value=manager):
            content = await _local_ollama_chat(candidate, MODEL_CONFIGS["analyst"], self.context)
        self.assertEqual(content, VALID_FORECAST_JSON)

        args, kwargs = client.post.call_args
        # A "/v1" base URL must not produce "/v1/api/chat".
        self.assertEqual(args[0], "http://127.0.0.1:8088/api/chat")
        payload = kwargs["json"]
        self.assertIs(payload["think"], False)
        self.assertEqual(payload["options"]["num_predict"], MODEL_CONFIGS["analyst"].max_tokens)
        # The workstation server has no auth; sending a bearer token would be noise.
        self.assertNotIn("Authorization", kwargs["headers"])
        user_text = payload["messages"][-1]["content"]
        self.assertGreater(user_text.find("Output ONLY one JSON object"), user_text.find("btcusdt"))

    def _candidates_local_first(self, key):
        return self._candidates(key, roles=key)[0]

    async def test_local_adapter_rejects_empty_and_incomplete_content(self):
        import os
        import httpx
        from prediction_engine import MODEL_CONFIGS, _local_ollama_chat
        candidate = self._candidates_local_first("analyst")
        for body in (
            {"done": True, "done_reason": "stop", "message": {"thinking": "private"}},
            {"done": False, "done_reason": "length", "message": {"content": VALID_FORECAST_JSON}},
        ):
            client = AsyncMock()
            client.post.return_value = httpx.Response(
                200, json=body, request=httpx.Request("POST", "http://127.0.0.1:8088/api/chat"))
            manager = AsyncMock()
            manager.__aenter__.return_value = client
            with patch.dict(os.environ, self.no_cloud, clear=False), patch("httpx.AsyncClient", return_value=manager):
                with self.assertRaises(ValueError):
                    await _local_ollama_chat(candidate, MODEL_CONFIGS["analyst"], self.context)

    def test_local_candidate_gets_a_single_attempt(self):
        import os
        from prediction_engine import MODEL_CONFIGS, _candidate_attempt_schedule
        local = self._candidates_local_first("analyst")
        cloud = next(c for c in self._candidates("analyst") if c.provider != "ollama_local")
        self.assertEqual(_candidate_attempt_schedule(MODEL_CONFIGS["analyst"], local), (0.0,))
        self.assertEqual(len(_candidate_attempt_schedule(MODEL_CONFIGS["analyst"], cloud)), 3)

    def test_local_candidate_is_available_without_api_key(self):
        import os
        from prediction_engine import _candidate_is_available
        local = self._candidates_local_first("analyst")
        with patch.dict(os.environ, self.no_cloud, clear=False):
            self.assertTrue(_candidate_is_available(local))

    async def test_local_failure_falls_back_to_the_cloud_chain(self):
        import os
        import prediction_engine
        from prediction_engine import MODEL_CONFIGS, generate_model_prediction
        env = dict(self.no_cloud, LOCAL_LLM_ROLES="analyst", OLLAMA_API_KEY="test-token")
        with patch.dict(os.environ, env, clear=False):
            with patch.object(prediction_engine, "_local_ollama_chat",
                              AsyncMock(side_effect=RuntimeError("connection refused"))), \
                 patch.object(prediction_engine, "_ollama_chat",
                              AsyncMock(return_value=VALID_FORECAST_JSON)) as cloud:
                result = await generate_model_prediction(MODEL_CONFIGS["analyst"], self.context, role="bull")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.model_id, "glm-5.1")
        self.assertEqual(cloud.await_count, 1)


if __name__ == "__main__":
    unittest.main()

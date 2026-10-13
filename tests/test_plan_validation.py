"""P3c — the one shared plan-validation pipeline.

:func:`trading_math.plan_validation.validate_plan` is what both the dashboard
endpoint and the Telegram console call, so the P3 exit gate («тот же ``plan_hash``
и violations, что в WebUI») holds only if this module behaves identically for the
same body regardless of who supplies the market and coverage reads.  These tests
pin exactly that: provider wiring, fail-closed defaults and hash determinism.

No FastAPI, no asyncpg, no redis here on purpose — the same file must be loadable
inside the chatbot container (Python 3.9), which is where the bot's copy of the
answer comes from.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trading_math.plan_contract import normalize_plan, plan_hash
from trading_math.plan_validation import validate_plan

INSTR = "htx:linear-swap:BTC-USDT"
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _body(mode: str = "live_paper", **overrides) -> dict:
    if mode == "live_paper":
        start, end = NOW + timedelta(minutes=5), NOW + timedelta(hours=8)
    else:
        start, end = NOW - timedelta(hours=3), NOW - timedelta(hours=1)
    base = {
        "instrument_key": INSTR,
        "mode": mode,
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "timezone": "Asia/Bangkok",
        "manual_budget": "1000",
        "auto_budget": "1000",
        "strategy_id": "baseline_v1",
        "strategy_version": "baseline_v1",
        "style": "default",
        "allowed_sides": ["long", "short"],
        "max_leverage": 3,
        "max_risk_per_order": "10",
        "max_daily_loss": "50",
        "max_session_loss": "100",
        "max_total_exposure": "500",
        "max_open_positions": 2,
    }
    if mode == "historical_replay":
        base["seed"] = 42
    base.update(overrides)
    return base


def _state(*, can_enter: bool = True, worst: str = "live", identity_ok=True) -> dict:
    """Shaped like :func:`trading_math.market_state.build_market_state`."""
    if identity_ok is False:
        return {"identity_ok": False, "observed": {"venue": "binance"}}
    return {
        "snapshot_id": "snap-1",
        "as_of": NOW.isoformat(),
        "quality": {"worst": worst},
        "capabilities": {"can_enter": can_enter, "reason_code": None if can_enter else "stale_ticker"},
    }


def _codes(result: dict) -> list[str]:
    return [v["code"] for v in result["violations"]]


def _market_provider(**kwargs):
    """Provider shaped like the real one: ``async (instrument_key) -> state``."""

    async def _provider(instrument_key):
        return _state(**kwargs)

    return _provider


# ─── providers are required, absence is a violation ─────────────────────


@pytest.mark.asyncio
async def test_live_plan_without_market_provider_is_not_approved():
    result = await validate_plan(_body(), now=NOW)
    assert _codes(result) == ["quote_source_unavailable"]
    assert result["can_approve"] is False
    assert result["plan_hash"] is None


@pytest.mark.asyncio
async def test_replay_plan_without_coverage_provider_is_not_approved():
    result = await validate_plan(_body("historical_replay"), now=NOW)
    assert "history_source_unavailable" in _codes(result)
    assert result["can_approve"] is False


@pytest.mark.asyncio
async def test_fresh_market_and_full_coverage_approve_the_same_body():
    result = await validate_plan(
        _body(), market=_market_provider(), coverage=None, now=NOW,
    )
    assert result["violations"] == []
    assert result["can_approve"] is True
    assert result["market"]["can_enter"] is True
    # the digest is the contract's own, not a second implementation
    plan, _ = normalize_plan(_body())
    assert result["plan_hash"] == plan_hash(plan)


# ─── gates that must block in every consumer ────────────────────────────


@pytest.mark.asyncio
async def test_stale_quote_blocks_with_the_domains_reason_code():
    result = await validate_plan(
        _body(), market=_market_provider(can_enter=False, worst="stale"), now=NOW,
    )
    assert _codes(result) == ["stale_quote"]
    assert result["violations"][0]["message"] == "stale_ticker"
    assert result["plan_hash"] is None


@pytest.mark.asyncio
async def test_identity_mismatch_is_a_violation_not_a_crash():
    """A payload describing another instrument must fail closed.

    Before the extraction the webui copy indexed ``state["quality"]`` directly and
    raised ``KeyError`` on this shape — the dashboard answered 500 instead of a
    violation.
    """
    result = await validate_plan(
        _body(), market=_market_provider(identity_ok=False), now=NOW,
    )
    assert _codes(result) == ["identity_mismatch"]
    assert result["can_approve"] is False


@pytest.mark.asyncio
async def test_replay_coverage_gap_blocks():
    async def _thin(key, start, end):
        return {"required_intervals": 120, "available_intervals": 90,
                "reason_code": "missing_intervals"}

    result = await validate_plan(
        _body("historical_replay"), market=None, coverage=_thin, now=NOW,
    )
    assert "missing_intervals" in _codes(result)
    assert result["market"]["coverage"]["available_intervals"] == 90


@pytest.mark.asyncio
async def test_replay_full_coverage_approves():
    async def _full(key, start, end):
        return {"required_intervals": 120, "available_intervals": 120}

    result = await validate_plan(
        _body("historical_replay"), market=None, coverage=_full, now=NOW,
    )
    assert result["violations"] == []
    assert result["can_approve"] is True


# ─── unparseable bodies ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unparseable_body_has_no_worst_case_and_no_hash():
    result = await validate_plan(_body(mode="scenario"), now=NOW)
    assert result["normalized"] is None
    assert result["violations"]
    assert result["worst_case_risk"] is None
    assert result["plan_hash"] is None
    assert result["can_approve"] is False


@pytest.mark.asyncio
async def test_semantic_violation_is_added_after_the_contract_passes():
    result = await validate_plan(
        _body(max_session_loss="10"),  # below max_daily_loss
        market=_market_provider(), now=NOW,
    )
    assert _codes(result) == ["loss_hierarchy"]
    assert result["normalized"] is not None
    assert result["worst_case_risk"] is not None


@pytest.mark.asyncio
async def test_live_window_in_the_past_blocks_at_validation_only():
    """The contract parses the body; the clock is what refuses it.

    That split is why the dashboard can store such a draft and the bot can show
    it with a violation instead of refusing to save it.
    """
    body = _body(start_at=(NOW - timedelta(hours=2)).isoformat(),
                 end_at=(NOW - timedelta(hours=1)).isoformat())
    plan, codes = normalize_plan(body)
    assert plan is not None and codes == []

    result = await validate_plan(body, market=_market_provider(), now=NOW)
    assert _codes(result) == ["live_window_past"]

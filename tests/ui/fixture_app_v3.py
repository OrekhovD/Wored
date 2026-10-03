"""V3-aware UI fixture server for real-browser (Playwright) evidence.

This reuses the full ``tests.ui.fixture_app`` app — real Jinja templates from
``webui/templates/``, real static files from ``webui/static/``, the login flow
and every existing page route — and layers the actual ``/api/v3`` market
read-model (``webui/market_workspace.py``) on top of two deterministic,
in-memory stores:

  * ``MarketStore``  — stands in for Redis and can be flipped live <-> stale at
    runtime through ``POST /__qa__/v3_mode`` so a test can watch the SSE stream
    degrade and recover for real;
  * ``CandlesStore`` — stands in for ``trader_v1_perp_candles`` and returns rows
    whose spacing matches whatever stored timeframe the endpoint asked for, so
    every period (1m/5m/1h/1d…) validates.

The stores are seeded on every request by a pure-ASGI middleware (not
``BaseHTTPMiddleware``, which would buffer the SSE ``StreamingResponse``),
because ``fixture_app``'s lifespan deliberately nulls ``redis_client`` /
``pg_pool`` to ``None`` at startup.

Nothing here touches runtime code: the V3 router, ``build_market_state``, the
candle validator and the browser JS are the real production artifacts.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

# Reuse the fully-wired fixture app (templates, static, login, page routes).
from tests.ui.fixture_app import app  # noqa: E402  (import for side effects)

from instrument_registry import load_registry  # noqa: E402
from market_workspace import router as v3_router  # noqa: E402
from forecast_workspace import router as forecast_v3_router  # noqa: E402
from simulation_api import (  # noqa: E402
    MemorySimulationStore,
    router as simulation_v3_router,
)
from simulation_proposal import (  # noqa: E402
    MemoryProposalStore,
    rules_source,
    router as proposal_v3_router,
)
from positions_workspace import router as positions_v3_router  # noqa: E402

# ── deterministic market store (Redis stand-in) ───────────────────────────────

CONTRACT_CODE = "BTC-USDT"


class MarketStore:
    """Mimics the collector's ``market:perpetual:htx:BTC-USDT`` snapshot.

    ``mode`` is mutable at runtime; the open SSE connection re-reads it each
    poll, so flipping live -> stale is reflected in the next snapshot frame.
    """

    def __init__(self) -> None:
        self.mode = "live"

    def _payload(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        if self.mode == "live":
            ticker = now
            comp = now
        else:  # stale: ticker/mark/index pushed beyond their thresholds
            ticker = now - timedelta(seconds=30)      # ticker threshold = 5s
            comp = now - timedelta(seconds=300)       # mark/index threshold = 90s
        funding = now - timedelta(seconds=10)         # funding threshold = 3600s
        tick_iso = ticker.isoformat()
        comp_iso = comp.isoformat()
        return {
            "venue": "htx",
            "market_type": "linear-swap",
            "contract_code": CONTRACT_CODE,
            "bid": "65000.1",
            "ask": "65000.9",
            "last": "65000.5",
            "mark": "65000.3",
            "index": "65000.7",
            "funding_rate": "0.0001",
            "next_funding_at": (now + timedelta(hours=2)).isoformat(),
            "source_at": tick_iso,
            "received_at": now.isoformat(),
            "component_times": {
                "ticker": tick_iso,
                "mark": comp_iso,
                "index": comp_iso,
                "funding": funding.isoformat(),
            },
        }

    async def get(self, key: str) -> str:  # noqa: ARG002 (key fixed by design)
        return json.dumps(self._payload())


# ── deterministic candles store (Postgres stand-in) ───────────────────────────

_STORED_SECONDS = {"1min": 60, "15min": 900, "60min": 3600, "4hour": 14400}


class CandlesStore:
    """Return ``trader_v1_perp_candles``-shaped rows with datetime columns.

    The endpoint passes ``(venue, contract_code, stored_timeframe, until,
    limit)`` positionally; spacing is chosen to match ``stored_timeframe`` so
    the OHLC/span invariants in ``validate_candle_rows`` hold for every period.
    Rows come newest-first (``ORDER BY open_time DESC``); the endpoint reverses.
    """

    async def fetch(self, sql: str, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        stored = args[2] if len(args) > 2 else "1min"
        secs = _STORED_SECONDS.get(stored, 60)
        raw_limit = args[4] if len(args) > 4 else 200
        try:
            n = int(raw_limit)
        except (TypeError, ValueError):
            n = 200
        n = max(1, min(n, 500))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        rows: list[dict[str, Any]] = []
        base = 64000.0
        for i in range(n):
            start = now - timedelta(seconds=secs * i)
            o = round(base + i * 7.0, 2)
            c = round(o + 5.0, 2)
            rows.append(
                {
                    "open_time": start,
                    "close_time": start + timedelta(seconds=secs),
                    "open": str(o),
                    "high": str(round(c + 3.0, 2)),
                    "low": str(round(o - 3.0, 2)),
                    "close": str(c),
                    "volume": str(round(10 + i * 0.5, 3)),
                    "source": "htx-api",
                    "venue": "htx",
                }
            )
        return rows


MARKET = MarketStore()
CANDLES = CandlesStore()

# Replay-history coverage toggle for the P3 session gates (MC-10 negative):
# complete=True answers count(*) with the full 1m interval count for the
# requested window; complete=False leaves 5 intervals "missing".
COVERAGE = {"complete": True}

# ── deterministic positions store (paper_v2_* stand-in, P5.3b / MC-17) ────────
from uuid import uuid4 as _uuid4  # noqa: E402


def _seed_positions() -> dict[str, list[dict[str, object]]]:
    """One rich scenario: an open long (manual), a pending intent (auto), a
    closed long (manual) and a liquidated short (auto) — so all four buckets,
    the chart overlays and the ledger links have something real to render.
    Amounts are Decimal strings; ids are the same UUIDs the tables would hold."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    open_id, pend_id, closed_id, liq_id = (_uuid4(), _uuid4(), _uuid4(), _uuid4())
    manual_acct, auto_acct, day = _uuid4(), _uuid4(), _uuid4()
    positions = [
        {  # open long — carries mark/unrealized/liquidation the card derives
            "position_id": str(open_id), "account_id": str(manual_acct), "account_kind": "manual",
            "day_id": str(day), "instrument": CONTRACT_CODE, "side": "long",
            "qty": "1", "avg_entry_price": "65000", "isolated_margin": "0.65",
            "stop_loss": "64000", "take_profit": "67000", "status": "open",
            "opened_at": now - timedelta(minutes=30), "closed_at": None, "close_price": None,
            "realized_gross_pnl": "0", "realized_net_pnl": "0",
            "entry_fee": "0.04", "exit_fee": "0", "funding_cashflow": "0",
        },
        {  # closed long — realized net is a recorded fact
            "position_id": str(closed_id), "account_id": str(manual_acct), "account_kind": "manual",
            "day_id": str(day), "instrument": CONTRACT_CODE, "side": "long",
            "qty": "2", "avg_entry_price": "63000", "isolated_margin": "1.26",
            "stop_loss": "62000", "take_profit": "65000", "status": "closed",
            "opened_at": now - timedelta(minutes=120), "closed_at": now - timedelta(minutes=60),
            "close_price": "64000", "realized_gross_pnl": "0.2", "realized_net_pnl": "0.12",
            "entry_fee": "0.08", "exit_fee": "0.08", "funding_cashflow": "0",
        },
        {  # liquidated short — exit_reason 'liquidation' on the card
            "position_id": str(liq_id), "account_id": str(auto_acct), "account_kind": "auto",
            "day_id": str(day), "instrument": CONTRACT_CODE, "side": "short",
            "qty": "1", "avg_entry_price": "60000", "isolated_margin": "0.6",
            "stop_loss": None, "take_profit": "58000", "status": "liquidated",
            "opened_at": now - timedelta(minutes=200), "closed_at": now - timedelta(minutes=150),
            "close_price": "66000", "realized_gross_pnl": "-0.6", "realized_net_pnl": "-0.68",
            "entry_fee": "0.04", "exit_fee": "0.04", "funding_cashflow": "0",
        },
    ]
    orders = [
        {  # pending entry intent (auto), no position yet
            "order_id": str(pend_id), "account_id": str(auto_acct), "account_kind": "auto",
            "day_id": str(day), "instrument": CONTRACT_CODE, "side": "buy", "qty": "1",
            "price": "64500", "stop_loss": "64000", "take_profit": "66000",
            "state": "submitted", "filled_qty": "0", "origin": "auto", "actor": "auto",
            "created_at": now - timedelta(minutes=5),
        },
    ]
    fills = [
        {"fill_id": str(_uuid4()), "order_id": str(open_id)},
        {"fill_id": str(_uuid4()), "order_id": str(closed_id)},
        {"fill_id": str(_uuid4()), "order_id": str(liq_id)},
    ]
    postings = [
        {"event_id": str(_uuid4()), "source_ref": str(open_id) + ":open"},
        {"event_id": str(_uuid4()), "source_ref": f"liq:{liq_id}"},
    ]
    # link the fill source_ref the postings query expects (fills carry fill_id)
    return {"positions": positions, "orders": orders, "fills": fills, "postings": postings}


POSITIONS = _seed_positions()

# Source-state toggle for the P5.3b positions dashboard (MC-17): ``ok`` serves
# the seeded buckets; ``empty`` returns no rows so the fail-visible empty state
# is exercised; ``error`` raises so the endpoint's 503 error state is exercised.
POSITIONS_MODE = {"mode": "ok"}


# ── deterministic forecast store (forecast_requests / _points stand-in) ────────

_REQ_ID = 7


class ForecastStore:
    """Serves a completed 3-role forecast for ``btcusdt`` whose predicted window
    overlaps the loaded fact candles, so the overlay is a real forecast-vs-fact
    comparison (MC-07).  ``present=False`` makes the read-model fail closed
    (``no_verified_forecast``).  ``settled=True`` fills ``actual_price`` so the
    MC-08 holdout produces real numbers; with ``settled=False`` there are no
    realised facts and the holdout must read ``N/A`` (insufficient sample)."""

    def __init__(self) -> None:
        self.present = True
        self.settled = False

    def _request_row(self) -> dict[str, Any]:
        # Base time is far enough back that the 15..24 predicted bars land
        # inside the candle history the /candles endpoint returns (last ~200m).
        base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=210)
        return {
            "id": _REQ_ID,
            "symbol": "btcusdt",
            "contract_code": CONTRACT_CODE,
            "base_price": "65000.0",
            "horizon_hours": 1,
            "base_timeframe": "1m",
            "created_at": base,
            "execution_state": "completed",
            "plan_version": "v3-qa-1",
            "status": "completed",
        }

    def _points(self) -> list[dict[str, Any]]:
        base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=210)
        # 3 independent role voices per step → an honest q10/q50/q90 band.
        roles = [("bull", 101, "glm-qa"), ("bear", 102, "deepseek-qa"), ("arbiter", 103, "minimax-qa")]
        rows: list[dict[str, Any]] = []
        for step in range(24):
            center = 65000.0 + step * 3.0
            target = base + timedelta(seconds=60 * (step + 1))
            # settled fact: near the ensemble median (model error small), above
            # the flat 65000 baseline (so a real forecast beats persistence).
            actual = round(center + 2.0, 1) if self.settled else None
            for j, (role, run_id, model) in enumerate(roles):
                off = (j - 1) * 40.0  # bull +40, arbiter 0, bear -40
                price = round(center + off, 1)
                rows.append(
                    {
                        "step_index": step,
                        "target_time": target,
                        "predicted_price": str(price),
                        "predicted_high": None,  # no proven intra-candle extreme
                        "predicted_low": None,
                        "confidence": str(60 + j),
                        "predicted_change_pct": str(round(off / center * 100, 4)),
                        "run_id": run_id,
                        "agent_role": role,
                        "model_id": model,
                        "actual_price": str(actual) if actual is not None else None,
                        "price_error_pct": None,
                        "skill_vs_baseline": None,
                        "in_range": None,
                    }
                )
        return rows


FORECAST = ForecastStore()


class V3Pool:
    """Single ``pg_pool`` stand-in shared by the candle and forecast endpoints.

    The market candles endpoint and ``forecast_workspace`` both read
    ``request.app.state.pg_pool``; this routes on the SQL text so one object can
    answer both.  Any other query (candles) delegates to ``CandlesStore``.
    """

    @staticmethod
    def _filter_positions(args: tuple) -> list[dict[str, Any]]:
        """Mirror the real SQL's ``p.status = $n`` / ``a.kind = $m`` predicates:
        the endpoint passes the filter values positionally, so pick them out of
        ``args`` and narrow the seeded rows accordingly (a stand-in for WHERE)."""
        status = next((a for a in args if a in ("open", "closed", "liquidated")), None)
        account = next((a for a in args if a in ("manual", "auto")), None)
        rows = POSITIONS["positions"]
        if status:
            rows = [r for r in rows if str(r["status"]) == status]
        if account:
            rows = [r for r in rows if str(r["account_kind"]) == account]
        return rows

    async def fetch(self, sql: str, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        low = sql.lower()
        if "forecast_requests" in low:
            return [FORECAST._request_row()] if FORECAST.present else []
        if "forecast_points" in low:
            return FORECAST._points() if FORECAST.present else []
        if "count(*)" in low and "trader_v1_perp_candles" in low:
            # P3 replay coverage probe: args are (venue, contract, start, end)
            start, end = args[2], args[3]
            required = int((end - start).total_seconds() // 60)
            available = required if COVERAGE["complete"] else max(0, required - 5)
            return [{"n": available}]
        # P5.3b positions read-model (paper_v2_* stand-in)
        if any(t in low for t in ("paper_v2_fills", "paper_v2_postings", "from paper_v2_orders", "from paper_v2_positions")):
            if POSITIONS_MODE["mode"] == "error":
                raise RuntimeError("positions source unavailable (QA)")
            if POSITIONS_MODE["mode"] == "empty":
                return []
        if "paper_v2_fills" in low:
            return POSITIONS["fills"]
        if "paper_v2_postings" in low:
            return POSITIONS["postings"]
        if "from paper_v2_orders" in low:
            return POSITIONS["orders"]
        if "from paper_v2_positions" in low:
            return self._filter_positions(args)
        return await CANDLES.fetch(sql, *args, **kwargs)

    async def fetchrow(self, sql: str, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        low = sql.lower()
        if "forecast_requests" in low:
            return FORECAST._request_row() if FORECAST.present else None
        if "count(*)" in low and "paper_v2_positions" in low:
            if POSITIONS_MODE["mode"] == "error":
                raise RuntimeError("positions source unavailable (QA)")
            if POSITIONS_MODE["mode"] == "empty":
                return {"n": 0}
            return {"n": len(self._filter_positions(args))}
        if "from paper_v2_positions" in low and "position_id" in low:
            wanted = str(args[0]) if args else None
            for r in POSITIONS["positions"]:
                if str(r["position_id"]) == wanted:
                    return r
            return None
        return None


POOL = V3Pool()



# ── pure-ASGI state seeding (survives lifespan's None reset; no SSE buffering) ─

SIM = MemorySimulationStore()
PROPOSALS = MemoryProposalStore()

# P4 QA agent-source toggle (MC-12/MC-13). Default = the real deterministic
# rules engine; failure stubs are injected ONLY via app.state so production
# wiring (paid LLM) can never be reached accidentally from a test.
PROPOSAL_KIND = {"kind": "rules"}


async def _qa_source_slow(q, ctx):
    import asyncio

    await asyncio.sleep(5)  # > simulation_proposal_timeout (1.0s in QA) → llm_timeout
    return await rules_source(q, ctx)


async def _qa_source_quota(q, ctx):
    raise RuntimeError("provider quota exceeded (HTTP 402)")


async def _qa_source_garbage(q, ctx):
    return {"nope": 1}  # no "plan" key → llm_invalid_response


async def _qa_source_bad_draft(q, ctx):
    result = await rules_source(q, ctx)
    result["plan"]["strategy_version"] = "moon_laser_v9"  # not in catalog → veto
    return result


_QA_SOURCES = {
    "rules": rules_source,
    "slow": _qa_source_slow,
    "quota": _qa_source_quota,
    "garbage": _qa_source_garbage,
    "bad_draft": _qa_source_bad_draft,
}


class SeedStateMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") in ("http", "websocket"):
            app_obj = scope.get("app")
            if app_obj is not None:
                app_obj.state.redis_client = MARKET
                app_obj.state.pg_pool = POOL
                app_obj.state.simulation_store = SIM
                app_obj.state.simulation_proposal_store = PROPOSALS
                app_obj.state.simulation_proposal_source = _QA_SOURCES[PROPOSAL_KIND["kind"]]
                if PROPOSAL_KIND["kind"] == "slow":
                    app_obj.state.simulation_proposal_timeout = 1.0
                elif hasattr(app_obj.state, "simulation_proposal_timeout"):
                    del app_obj.state.simulation_proposal_timeout
        await self.app(scope, receive, send)


app.add_middleware(SeedStateMiddleware)

# ── QA control to flip market quality for degraded-state evidence ──────────────


@app.post("/__qa__/v3_mode")
async def _qa_v3_mode(mode: str = "live") -> dict[str, str]:
    MARKET.mode = mode if mode in ("live", "stale") else "live"
    return {"mode": MARKET.mode}


@app.get("/__qa__/v3_mode")
async def _qa_v3_mode_get() -> dict[str, str]:
    return {"mode": MARKET.mode}


@app.post("/__qa__/v3_forecast")
async def _qa_v3_forecast(present: bool = True, settled: bool = False) -> dict[str, bool]:
    """Toggle the forecast store: ``present`` on/off (overlay draw vs fail-closed)
    and ``settled`` on/off (MC-08 holdout with realised facts vs N/A)."""
    FORECAST.present = bool(present)
    FORECAST.settled = bool(settled)
    return {"present": FORECAST.present, "settled": FORECAST.settled}


@app.post("/__qa__/v3_coverage")
async def _qa_v3_coverage(complete: bool = True) -> dict[str, bool]:
    """Toggle 1m replay-history coverage for the P3 session gates (MC-10)."""
    COVERAGE["complete"] = bool(complete)
    return {"complete": COVERAGE["complete"]}


@app.post("/__qa__/v3_positions")
async def _qa_v3_positions(mode: str = "ok") -> dict[str, str]:
    """Flip the P5.3b positions source state for MC-17 evidence: ``ok`` serves
    the seeded buckets, ``empty`` returns no rows (fail-visible empty state),
    ``error`` makes the endpoint 503 (source-outage state)."""
    POSITIONS_MODE["mode"] = mode if mode in ("ok", "empty", "error") else "ok"
    return {"mode": POSITIONS_MODE["mode"]}


@app.post("/__qa__/v3_sim_reset")
async def _qa_v3_sim_reset() -> dict[str, int]:
    """Blank the in-memory simulation + proposal stores between scenarios."""
    SIM.plans.clear()
    SIM.sessions.clear()
    SIM._by_owner_key.clear()
    PROPOSALS.items.clear()
    return {"plans": 0}


@app.post("/__qa__/v3_proposal_source")
async def _qa_v3_proposal_source(kind: str = "rules") -> dict[str, str]:
    """Pick the P4 agent source: real deterministic rules engine or a failure
    stub (slow/quota/garbage/bad_draft) for MC-12/MC-13 negative evidence."""
    PROPOSAL_KIND["kind"] = kind if kind in _QA_SOURCES else "rules"
    return {"kind": PROPOSAL_KIND["kind"]}


# Mount the real V3 read-model. Force a registry load so a malformed file fails
# loudly at import rather than as a 500 in the middle of a browser test.
load_registry(force_reload=True)
app.include_router(v3_router)
app.include_router(forecast_v3_router)
app.include_router(simulation_v3_router)
app.include_router(proposal_v3_router)
app.include_router(positions_v3_router)

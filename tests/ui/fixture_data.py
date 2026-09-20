"""UI-11 fixture data loader and API shape adapters.

Loads fixtures.json, merges base + override per merge_rule, and provides
adapters that map fixture data to the actual HTTP contract shapes used
by the webui API endpoints.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

FIXTURES_PATH = Path(os.environ.get(
    "WORED_FIXTURES_PATH",
    Path(__file__).resolve().parents[2] / "TASOCHKI" / "HERMES-WORED-UIUX" / "fixtures.json",
))

# ─── Fixture loading and merge ────────────────────────────────────────────────

_raw_cache: dict[str, Any] | None = None


def _load_raw() -> dict[str, Any]:
    global _raw_cache
    if _raw_cache is None:
        with open(FIXTURES_PATH, encoding="utf-8") as fh:
            _raw_cache = json.load(fh)
    return _raw_cache


def merge(base: Any, override: Any) -> Any:
    """Recursively merge *override* onto *base*.

    - dicts: recursively merge keys
    - lists: override replaces entirely
    - ``None`` in override replaces the whole subtree (even dicts)
    - missing keys in override leave base unchanged
    """
    if override is None:
        return None
    if isinstance(base, dict) and isinstance(override, dict):
        result = copy.deepcopy(base)
        for key, val in override.items():
            if key in result:
                result[key] = merge(result[key], val)
            else:
                result[key] = copy.deepcopy(val)
        return result
    # Arrays replace; scalars replace
    return copy.deepcopy(override)


def get_fixture(name: str) -> dict[str, Any]:
    """Return merged fixture data for *name* (an override key).

    ``"base"`` returns the unmerged base. Unknown names raise KeyError.
    """
    raw = _load_raw()
    if name == "base":
        return copy.deepcopy(raw["base"])
    overrides = raw.get("overrides", {})
    if name not in overrides:
        raise KeyError(f"Unknown fixture override: {name}")
    return merge(raw["base"], overrides[name])


def get_clock() -> str:
    """Return the fixture clock timestamp."""
    return _load_raw().get("clock", "2026-09-09T12:00:00Z")


# ─── API shape adapters ──────────────────────────────────────────────────────
# Map fixture data to the contract shapes the real API endpoints return.


def market_to_deck(fixture: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Convert fixture ``market`` + ``history`` → command-deck market list."""
    fx = fixture if fixture is not None else get_fixture("ready")
    market = fx.get("market", {})
    history = fx.get("history", [])
    return [{
        "symbol": market.get("symbol", "btcusdt"),
        "price": float(market.get("price", 0)),
        "fetched_at": market.get("as_of"),
        "as_of": market.get("as_of"),
        "fresh": market.get("fresh", True),
        "stale_after_seconds": market.get("stale_after_seconds", 60),
        "volume": float(history[-1]["volume"]) if history else 0,
        "change_pct": 0.0,
        "reason_code": market.get("reason_code"),
        "candles": [
            {
                "time": c["time"],
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"],
            }
            for c in history
        ],
    }]


def forecast_to_deck(fixture: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert fixture ``forecast`` → command-deck consensus dict."""
    fx = fixture if fixture is not None else get_fixture("ready")
    fc = fx.get("forecast")
    if not fc:
        return {}
    return {
        "request_id": fc.get("request_id"),
        "execution_state": fc.get("execution_state"),
        "evaluation_state": fc.get("evaluation_state"),
        "created_at": fc.get("as_of"),
        "as_of": fc.get("as_of"),
        "valid_until": fc.get("valid_until"),
        "base_timeframe": fc.get("base_timeframe", "15min"),
        "roles": fc.get("roles", []),
        "candles": fc.get("points", []),
    }


def session_to_api(fixture: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert fixture ``session`` → /api/daily-session/active shape."""
    fx = fixture if fixture is not None else get_fixture("ready")
    sess = fx.get("session")
    if not sess:
        return {"session": None, "plan": None, "metrics": None,
                "trades": [], "events": [], "revision": None}
    return {
        "session": {
            "id": str(sess.get("id", 7001)),
            "status": sess.get("technical_status", "ARMED"),
            "readiness": sess.get("readiness", "waiting_trigger"),
            "reason_code": sess.get("reason_code"),
            "technical_status": sess.get("technical_status", "ARMED"),
            "allowed_commands": sess.get("allowed_commands", []),
            "entries": sess.get("entries", []),
        },
        "plan": None,
        "metrics": None,
        "trades": [],
        "events": [],
        "revision": None,
    }


def preview_to_api(fixture: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert fixture ``preview`` → /api/trade/preview shape."""
    fx = fixture if fixture is not None else get_fixture("preview_valid")
    pv = fx.get("preview", {})
    if not pv:
        return {"allowed": True, "entry_price": 64250.5, "notional": 1000,
                "size": 0.01556, "taker_fee": 0.00001234,
                "liquidation_price": 100, "liq_distance_pct": 0.5,
                "scenarios": {}}
    scenarios = pv.get("scenarios", [])
    if isinstance(scenarios, list):
        scenarios = {s.get("label", "+1%"): s.get("net_pnl", 0) for s in scenarios}
    return {
        "allowed": pv.get("allowed", True),
        "reasons": pv.get("reasons", []),
        "entry_price": 64250.5,
        "notional": 1000,
        "size": 0.01556,
        "taker_fee": float(pv.get("entry_fee", 0)),
        "estimated_exit_fee": float(pv.get("estimated_exit_fee", 0)),
        "liquidation_price": 64250.5 * (1 - 0.005),
        "liq_distance_pct": pv.get("liq_distance_pct", 0.5),
        "funding_assumption": pv.get("funding_assumption", ""),
        "scenarios": scenarios,
    }


def metrics_to_api(fixture: dict[str, Any] | None = None) -> dict[str, Any]:
    """Convert fixture ``metrics`` → /api/strategy/metrics shape."""
    fx = fixture if fixture is not None else get_fixture("ready")
    m = fx.get("metrics", {})
    return {
        "independent_requests": m.get("independent_requests"),
        "evaluated_points": m.get("evaluated_points"),
        "metrics_version": m.get("metrics_version", 2),
        "ranking_available": m.get("ranking_available", False),
    }
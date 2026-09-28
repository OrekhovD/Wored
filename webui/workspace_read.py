"""workspace_read — read-only BFF for /workspace.

Calls the existing paper_trading domain bridge and market snapshot to build
a unified workspace state.  Performs NO writes, NO financial arithmetic, NO
client-side balance computation.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# Domain bridge — reuse paper_api imports (same process/module scope)
try:
    from .paper_api import (
        _pt_available,
        _pt_get_state,
        _pt_owner_id,
        _owner_from_request,
        _use_pt_domain,
        DEFAULT_SETTINGS,
    )
    from .paper_market import paper_market_mode, resolve_market_snapshot
except ImportError:
    from paper_api import (  # type: ignore[no-redef]
        _pt_available,
        _pt_get_state,
        _pt_owner_id,
        _owner_from_request,
        _use_pt_domain,
        DEFAULT_SETTINGS,
    )
    from paper_market import paper_market_mode, resolve_market_snapshot  # type: ignore[no-redef]

from workspace_presenters import (
    build_attention,
    build_capabilities,
    day_stage,
    format_status_bar,
)

SCHEMA_VERSION = 2


async def get_workspace_state(request) -> Dict[str, Any]:
    """Build the full workspace state for the authenticated user.

    Returns a JSON-serialisable dict matching the BFF schema from RFC §4.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Domain state
    domain_ok = False
    day: Optional[Dict[str, Any]] = None
    accounts: list = []
    automation_state: Optional[str] = None
    next_action: Optional[str] = None

    use_domain = _pt_available and _use_pt_domain(request)
    if use_domain:
        try:
            # Identity resolution matches paper_api command paths so the day
            # started via Drawer is the same day the workspace renders.
            owner = _owner_from_request(request)
            state = await _pt_get_state(owner)
            if state.get("ok"):
                domain_ok = True
                day = state.get("day")
                accounts = state.get("accounts", [])
                next_action = state.get("next_action")
                # Derive automation state from day or explicit field
                automation_state = _extract_auto_state(day, accounts)
            else:
                domain_ok = False
                log.warning("workspace domain unavailable: %s", state.get("error"))
        except Exception:
            log.exception("workspace domain error")
            domain_ok = False

    # 2. Market snapshot
    market: Dict[str, Any] = {"quality": "unknown", "age_seconds": None}
    try:
        snap = await resolve_market_snapshot(request, "btcusdt")
        market_mode = paper_market_mode(request)
        if snap:
            market = {
                "quality": "live" if market_mode == "live" else "stale",
                "bid": str(getattr(snap, "bid", "—")),
                "ask": str(getattr(snap, "ask", "—")),
                "mark": str(getattr(snap, "mark", getattr(snap, "last", "—"))),
                "source_at": getattr(snap, "ts", None),
                "mode": market_mode,
            }
        else:
            market = {"quality": "unavailable", "mode": market_mode}
    except Exception:
        log.debug("workspace market fetch failed", exc_info=True)
        market = {"quality": "unavailable"}

    # 3. Capabilities (derived from state)
    caps = build_capabilities(
        day=day,
        ok=domain_ok,
        market_quality=market.get("quality", "unknown"),
    )
    # B3: commands enabled when domain is reachable
    caps["commands_enabled"] = domain_ok

    # F08 Phase 4a: read operational mode (trade/reduce_only/pause)
    trader_mode = "trade"  # default
    try:
        redis = getattr(request.app.state, "redis_client", None)
        if redis is not None:
            import json as _json
            raw_mode = await redis.get("trader:mode")
            if raw_mode:
                mode_data = _json.loads(raw_mode) if isinstance(raw_mode, str) else _json.loads(raw_mode.decode())
                trader_mode = mode_data.get("mode", "trade")
    except Exception:
        pass
    # can_enter: mode gate (reduce_only/pause blocks new entries)
    caps["can_enter"] = (
        caps.get("can_trade", False) and trader_mode == "trade"
    )

    # 4. Stage
    stage = day_stage(day.get("state") if day else None)

    # 5. Attention queue
    attention_input = {
        "ok": domain_ok,
        "day": day,
        "market": market,
        "automation_state": automation_state,
        "capabilities": caps,
        "pending_commands": [],
    }
    attention = build_attention(attention_input)

    # 6. Objects (positions, orders — from accounts)
    objects = _flatten_objects(accounts)

    # 7. Sources
    sources = [
        {
            "source_name": "paper_trading",
            "status": "ok" if domain_ok else "unavailable",
            "observed_at": now_iso,
        },
        {
            "source_name": "market",
            "status": market.get("quality", "unknown"),
            "observed_at": market.get("source_at"),
        },
    ]

    # 8. Status bar
    status_bar = format_status_bar(
        day=day, accounts=accounts, market=market,
        pending_count=0, stage=stage,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "as_of": now_iso,
        "stage": stage,
        "day": _day_with_ref(day),
        "accounts": accounts,
        "market": market,
        "objects": objects,
        "attention": attention,
        "capabilities": caps,
        "sources": sources,
        "status_bar": status_bar,
        "automation_state": automation_state,
        "trader_mode": trader_mode,
        "next_action": next_action,
        "commands_enabled": domain_ok,  # B3 flag
    }


def _extract_auto_state(
    day: Optional[Dict[str, Any]], accounts: list
) -> Optional[str]:
    """Derive automation state from domain payload."""
    if day is None:
        return None
    # Domain may provide explicit automation_state; otherwise infer from auto account
    explicit = day.get("automation_state")
    if explicit:
        return explicit
    # If day is running, check auto account for hints
    for acc in accounts:
        if acc.get("kind") == "auto":
            open_pos = acc.get("open_positions", 0)
            if open_pos and open_pos > 0:
                return "position_open"
            return "waiting_signal"
    return None


def _flatten_objects(accounts: list) -> list:
    """Flatten nested positions from accounts into a typed object list."""
    objects = []
    for acc in accounts:
        kind = acc.get("kind", "?")
        for pos in acc.get("positions", []):
            objects.append({
                "object_ref": {
                    "kind": "position",
                    "id": str(pos.get("id", "")),
                    "account_id": kind,
                    "source": "paper_trading",
                },
                "type": "position",
                "side": pos.get("side", "?"),
                "qty": str(pos.get("qty", "—")),
                "entry_price": str(pos.get("avg_entry", "—")),
                "stop": pos.get("stop"),
                "target": pos.get("target"),
                "origin": kind,
                "summary": f"{pos.get('side', '?')} {pos.get('qty', '?')} ({kind})",
            })
    return objects


def _day_with_ref(day: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Wrap day with object_ref if present."""
    if day is None:
        return None
    result = dict(day)
    result["object_ref"] = {
        "kind": "day",
        "id": str(day.get("id", "")),
        "source": "paper_trading",
    }
    return result

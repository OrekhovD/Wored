"""Atomic plan publication and immutable JSON history; no infrastructure ownership."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from services.plan_contract import session_error, utc


def decode(value):
    return json.loads(value) if isinstance(value, str) else value


async def save_plan(pool, session: dict, checked: dict, model: str, *, initial: bool,
                    revision: dict | None = None, expected_pending: list[str] | None = None) -> dict:
    sid = str(session["id"])
    base = int(session["active_plan_version"])
    async with pool.acquire() as conn, conn.transaction():
        locked = await conn.fetchrow("SELECT * FROM trading_sessions WHERE id=$1 FOR UPDATE", sid)
        if not locked:
            return {"error": "session_not_found"}
        error = session_error(dict(locked))
        if error:
            return {"error": error}
        now = datetime.now(timezone.utc)
        if utc(checked["valid_until"]) <= now or (now - utc(checked["market_snapshot"]["timestamp"])).total_seconds() > 90:
            return {"error": "market_snapshot_expired"}
        if locked["active_plan_version"] != base or locked["status"] != session["status"]:
            return {"error": "session_changed_during_generation"}
        exists = await conn.fetchval("SELECT 1 FROM session_plans WHERE session_id=$1 LIMIT 1", sid)
        if initial and exists:
            return {"error": "plan_already_exists"}
        if not initial and not await conn.fetchval(
            "SELECT 1 FROM session_plans WHERE session_id=$1 AND version=$2", sid, base
        ):
            return {"error": "active_plan_version_missing"}
        if expected_pending is not None:
            actual = await conn.fetch("SELECT id FROM planned_entries WHERE session_id=$1 AND plan_version=$2 AND status='planned'", sid, base)
            if sorted(str(e["id"]) for e in actual) != sorted(expected_pending):
                return {"error": "entries_changed_during_generation"}
        version = base if initial else base + 1
        plan_id = str(uuid.uuid4())
        entries = [{**e, "entry_id": str(uuid.uuid4())} for e in checked["entries"]]
        full = {**checked, "entries": entries, "plan_id": plan_id, "session_id": sid,
                "version": version, "model_used": model, "agent_role": "analyst",
                "created_at": datetime.now(timezone.utc).isoformat()}
        # Publish plan + orders + active version under one session row lock.
        await conn.execute(
            "INSERT INTO session_plans(id,session_id,version,plan_type,plan_json,created_by_role) "
            "VALUES($1,$2,$3,$4,$5,'analyst')", plan_id, sid, version,
            "initial" if initial else "revision", json.dumps(full, allow_nan=False))
        await conn.execute(
            "UPDATE planned_entries SET status='superseded' WHERE session_id=$1 AND status='planned'", sid)
        for e in entries:
            await conn.execute(
                "INSERT INTO planned_entries(id,session_id,plan_version,side,status,entry_zone_from,"
                "entry_zone_to,invalidation_price,stop_loss,take_profit_json,recommended_leverage,"
                "budget_share_pct,margin_mode,confirmation_rule,reason_code) "
                "VALUES($1,$2,$3,$4,'planned',$5,$6,$7,$8,$9,$10,$11,'isolated',$12,$13)",
                e["entry_id"], sid, version, e["side"], float(e["entry_zone_from"]),
                float(e["entry_zone_to"]), float(e["invalidation_price"]), float(e["stop_loss"]),
                json.dumps(e["take_profit"]), e["recommended_leverage"], float(e["budget_share_pct"]),
                e["confirmation_rule"], str(e.get("reason_code", "validated_plan")))
        status = locked["status"]
        command = (revision or {}).get("execution_command", "continue")
        if command in {"pause", "close_all"}:
            status = "paused"
        elif status not in {"in_position", "paused", "cooldown"}:
            status = "armed" if entries else "idle"
        await conn.execute(
            "UPDATE trading_sessions SET active_plan_version=$2,status=$3,updated_at=NOW() WHERE id=$1",
            sid, version, status)
        revision_id = None
        if revision is not None:
            revision_id = str(uuid.uuid4())
            await conn.execute(
                "INSERT INTO session_revisions(id,session_id,base_version,new_version,execution_command,revision_json) "
                "VALUES($1,$2,$3,$4,$5,$6)", revision_id, sid, base, version, command, json.dumps(revision))
        await conn.execute(
            "INSERT INTO execution_events(id,session_id,event_type,state_before,state_after,event_payload) "
            "VALUES($1,$2,'plan_validated',$3,$4,$5)", str(uuid.uuid4()), sid, locked["status"], status,
            json.dumps({"version": version, "validation_status": checked["validation_status"],
                        "accepted_entries": len(entries),
                        "rejections": [e["risk"]["errors"] for e in checked["rejected_entries"]]}))
    return {"plan_id": plan_id, "session_id": sid, "version": version, "model_used": model,
            "entries_count": len(entries), "market_regime": full["market_regime"],
            "thesis": full["thesis"], "validation_status": full["validation_status"],
            "revision_id": revision_id, "status": status}


def revision_candidate(current: dict, pending: list[dict], revision: dict) -> dict:
    """Only pending orders can be revised; open trades retain their protective order."""
    command = revision.get("execution_command")
    if command not in {"continue", "tighten", "reduce", "pause", "close_all"}:
        raise ValueError("invalid_revision_command")
    patch = revision.get("patch")
    if not isinstance(patch, dict):
        raise ValueError("invalid_revision_patch")
    if patch.get("update_session_risk"):
        raise ValueError("session_risk_patch_not_supported")
    by_id = {}
    for row in pending:
        e = dict(row)
        e["take_profit"] = decode(e.pop("take_profit_json"))
        for key in ("entry_zone_from", "entry_zone_to", "stop_loss", "invalidation_price", "budget_share_pct"):
            e[key] = float(e[key])
        by_id[str(e.pop("id"))] = e
    cancels, updates, additions = (patch.get(k, []) for k in ("cancel_entries", "update_entries", "add_entries"))
    if not all(isinstance(v, list) for v in (cancels, updates, additions)) or len(additions) > 2:
        raise ValueError("invalid_revision_entries")
    for key in cancels:
        if not isinstance(key, str) or key not in by_id:
            raise ValueError("unknown_pending_entry")
        del by_id[key]
    allowed = {"entry_id", "entry_zone_from", "entry_zone_to", "stop_loss", "invalidation_price",
               "take_profit", "recommended_leverage", "budget_share_pct", "confirmation_rule", "reason_code"}
    for update in updates:
        if not isinstance(update, dict) or set(update) - allowed:
            raise ValueError("unsupported_entry_patch")
        key = str(update.get("entry_id", ""))
        if key not in by_id:
            raise ValueError("unknown_pending_entry")
        by_id[key].update({k: v for k, v in update.items() if k != "entry_id"})
    entries = list(by_id.values()) + additions
    # Keep only JSON candidate fields, never UUID/Decimal/DB timestamps.
    fields = allowed - {"entry_id"} | {"side", "margin_mode"}
    entries = [{k: v for k, v in e.items() if k in fields} for e in entries]
    candidate = {**current, "entries": entries}
    if command in {"pause", "close_all"} or not entries:
        candidate.update(entries=[], primary_scenario="no_trade")
    # An explanatory revision is optional; prices are always revalidated against fresh context.
    for key in ("thesis", "primary_scenario", "alternative_scenario", "no_trade_condition", "market_regime"):
        if key in revision:
            candidate[key] = revision[key]
    return candidate

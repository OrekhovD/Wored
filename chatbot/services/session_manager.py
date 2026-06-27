"""
Daily Pipeline WORED v2 — Session Manager.

Управляет lifecycle торговой сессии:
  - Создание сессии (ТЗ 5.1)
  - Bootstrap: initial plan generation через Analyst (ТЗ 5.3)
  - Hourly revision: patch через Analyst (ТЗ 5.4)
  - Execution: открывает/закрывает сделки по плану (ТЗ 5.5)
  - Closeout: финальные метрики (ТЗ 5.6)

ТЗ разделы 3, 4, 5, 11, 14.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from services.execution_engine import (
    ExecutionStateMachine,
    SessionState,
    ExecutionCommand,
    check_entry_trigger,
    check_stop_loss_hit,
    check_take_profit_hit,
    check_invalidation,
    apply_slippage,
    calc_position_size,
    calc_fees,
    calc_realised_pnl,
    calc_unrealised_pnl,
    calc_equity,
    calc_drawdown_pct,
    calc_liquidation_price,
    is_liquidated,
    get_risk_params,
    validate_leverage,
    validate_budget_share,
    TAKER_FEE_RATE,
    DEFAULT_SLIPPAGE_BPS,
)

log = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ─── Market Context Snapshot (ТЗ 5.2) ──────────────────────────────────

async def build_market_context(symbol: str = "btcusdt") -> dict:
    """
    ТЗ 5.2 — собирает Market Context Snapshot из collector-компонентов.
    НЕ генерируется LLM — только collector/htx + indicators.
    """
    from storage.redis_client import get_redis

    redis = get_redis()
    snapshot = {
        "snapshot_id": _uuid(),
        "symbol": symbol.upper(),
        "timestamp": _now_utc().isoformat(),
        "price": 0.0,
        "mark_price": 0.0,
        "funding_context": {"rate": 0.0, "next_funding_at": ""},
        "timeframes": {
            "1m": {"trend": "flat", "rsi": 0.0, "macd_hist": 0.0, "atr": 0.0},
            "5m": {"trend": "flat", "rsi": 0.0, "macd_hist": 0.0, "atr": 0.0},
            "15m": {"trend": "flat", "rsi": 0.0, "macd_hist": 0.0, "atr": 0.0},
            "1h": {"trend": "flat", "rsi": 0.0, "macd_hist": 0.0, "atr": 0.0},
        },
        "volatility_regime": "normal",
        "liquidity_regime": "normal",
        "risk_flags": ["none"],
    }

    # Price from Redis hot cache
    try:
        ticker_raw = await redis.get(f"ticker:{symbol}")
        if ticker_raw:
            data = json.loads(ticker_raw)
            snapshot["price"] = float(data.get("price", 0))
            snapshot["mark_price"] = float(data.get("price", 0))
    except Exception as exc:
        log.warning("Market context: no Redis ticker for %s: %s", symbol, exc)

    # Indicators from collector (imported defensively — collector package may not be in path)
    try:
        import importlib
        calc_mod = importlib.import_module("indicators.calculator")
        calculate_indicators = getattr(calc_mod, "calculate_indicators")
        for tf in ("1m", "5m", "15m", "1h"):
            inds = await calculate_indicators(symbol, tf)
            if inds:
                snapshot["timeframes"][tf] = {
                    "trend": inds.get("trend", "flat"),
                    "rsi": float(inds.get("rsi", 0)),
                    "macd_hist": float(inds.get("macd_hist", 0)),
                    "atr": float(inds.get("atr", 0)),
                }
    except Exception:
        pass  # indicators may not be available for all timeframes

    return snapshot


# ─── Session CRUD ──────────────────────────────────────────────────────

async def create_session(
    user_id: int,
    budget_usdt: float = 100.0,
    duration_hours: int = 8,
    risk_mode: str = "balanced",
    symbol: str = "BTCUSDT",
    source: str = "telegram",
) -> dict:
    """
    ТЗ 5.1 — создать торговую сессию.
    """
    from storage.postgres_client import get_pool

    session_id = _uuid()
    now = _now_utc()
    session_end = now + timedelta(hours=duration_hours)

    pool = await get_pool()
    if not pool:
        return {"error": "No DB pool"}

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO trading_sessions
                (id, user_id, symbol, exchange, session_start, session_end,
                 forecast_horizon_hours, initial_budget_usdt, risk_mode, status, created_at, updated_at)
            VALUES ($1, $2, $3, 'HTX', $4, $5, $6, $7, $8, 'idle', NOW(), NOW())
            """,
            session_id, user_id, symbol, now, session_end,
            duration_hours, budget_usdt, risk_mode,
        )

    log.info("Session %s created for user %d: %s %dh %s budget=%.2f",
             session_id, user_id, symbol, duration_hours, risk_mode, budget_usdt)

    return {
        "session_id": session_id,
        "user_id": user_id,
        "symbol": symbol,
        "budget_usdt": budget_usdt,
        "duration_hours": duration_hours,
        "risk_mode": risk_mode,
        "status": "idle",
        "session_start": now.isoformat(),
        "session_end": session_end.isoformat(),
    }


async def get_session(session_id: str) -> dict | None:
    from storage.postgres_client import get_pool
    pool = await get_pool()
    if not pool:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM trading_sessions WHERE id = $1",
            session_id,
        )
    return dict(row) if row else None


async def get_active_session(user_id: int) -> dict | None:
    """Найти активную сессию пользователя."""
    from storage.postgres_client import get_pool
    pool = await get_pool()
    if not pool:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM trading_sessions
            WHERE user_id = $1 AND status NOT IN ('completed', 'stopped')
            ORDER BY created_at DESC LIMIT 1
            """,
            user_id,
        )
    return dict(row) if row else None


async def update_session_status(session_id: str, status: str, final_reason: str | None = None) -> bool:
    from storage.postgres_client import get_pool
    pool = await get_pool()
    if not pool:
        return False
    async with pool.acquire() as conn:
        if final_reason:
            await conn.execute(
                "UPDATE trading_sessions SET status=$2, final_status_reason=$3, updated_at=NOW() WHERE id=$1",
                session_id, status, final_reason,
            )
        else:
            await conn.execute(
                "UPDATE trading_sessions SET status=$2, updated_at=NOW() WHERE id=$1",
                session_id, status,
            )
    return True


# ─── Plan Generation (ТЗ 5.3) ──────────────────────────────────────────

PLAN_GENERATION_PROMPT = """Ты — Crypto Trader Agent (Analyst), эксперт по BTCUSDT perpetual futures на HTX.

Тебе передан Market Context Snapshot в JSON. На основе этих данных сформируй 8-часовой торговый план.

Верни СТРОГО JSON (без markdown, без ```json) следующей структуры:
{
  "market_regime": "trend_up|trend_down|range|volatile",
  "thesis": "краткий тезис на английском",
  "primary_scenario": "long_on_reclaim|short_on_breakdown|range_trade|no_trade",
  "alternative_scenario": "short_on_failed_breakout|long_on_reversal|none",
  "no_trade_condition": "условие когда не торговать",
  "entries": [
    {
      "side": "long|short",
      "entry_zone_from": 0.0,
      "entry_zone_to": 0.0,
      "trigger_type": "zone_reclaim_confirmed|breakout_confirmation|range_bound",
      "confirmation_rule": "close_above_zone_on_1m_and_rsi_gt_50|rsi_gt_50|any",
      "invalidation_price": 0.0,
      "stop_loss": 0.0,
      "take_profit": [0.0, 0.0],
      "recommended_leverage": 125,
      "budget_share_pct": 15.0,
      "margin_mode": "isolated",
      "reason_code": "trend_pullback_entry|breakout_entry|range_entry"
    }
  ]
}

ПРАВИЛА:
- leverate только из [100, 125, 150, 200]
- budget_share_pct: 5-10 для defensive, 10-20 для balanced, 20-30 для aggressive
- Не более 3 entry в плане
- stop_loss должен быть дальше invalidation_price
- take_profit[0] ближе чем take_profit[1]
- Все цены — реальные числа из market context, не нули

Рыночный контекст:
{market_context}

Режим риска: {risk_mode}
Бюджет: {budget_usdt} USDT"""


async def generate_initial_plan(session_id: str) -> dict:
    """
    ТЗ 5.3, 11 (generate_initial_8h_plan) — Analyst генерирует стартовый план.
    """
    from ai.models import MODELS, PREMIUM_MODEL_CHAIN
    from ai.router import get_client
    from storage.postgres_client import get_pool

    session = await get_session(session_id)
    if not session:
        return {"error": "Session not found"}

    # Build market context
    symbol = session["symbol"].lower()
    market_ctx = await build_market_context(symbol)

    risk_mode = session["risk_mode"]
    budget = float(session["initial_budget_usdt"])

    # Call Analyst (deepseek-v4-pro via Ollama Cloud)
    prompt = PLAN_GENERATION_PROMPT.format(
        market_context=json.dumps(market_ctx, indent=2, ensure_ascii=False),
        risk_mode=risk_mode,
        budget_usdt=budget,
    )

    plan_json = None
    model_used = "unknown"

    for tier in PREMIUM_MODEL_CHAIN:
        cfg = MODELS.get(tier)
        if not cfg:
            continue
        client = get_client(tier)
        if client is None:
            continue

        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=cfg.model_id,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=1500,
                    temperature=0.3,
                ),
                timeout=cfg.timeout,
            )
            raw = (response.choices[0].message.content or "").strip()
            # Strip markdown fences
            if raw.startswith("```"):
                lines = raw.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()

            plan_json = json.loads(raw)
            model_used = cfg.model_id
            log.info("Plan generated by %s for session %s", model_used, session_id)
            break
        except Exception as exc:
            log.warning("Plan generation failed on %s: %s", cfg.model_id, exc)
            continue

    if not plan_json:
        return {"error": "Analyst returned no valid plan", "model": model_used}

    # Save plan to DB
    plan_id = _uuid()
    version = 1
    pool = await get_pool()
    if not pool:
        return {"error": "No DB pool"}

    full_plan = {
        "plan_id": plan_id,
        "session_id": session_id,
        "version": version,
        "created_at": _now_utc().isoformat(),
        "agent_role": "analyst",
        "model_used": model_used,
        **plan_json,
    }

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO session_plans (id, session_id, version, plan_type, plan_json, created_by_role)
            VALUES ($1, $2, 1, 'initial', $3, 'analyst')
            """,
            plan_id, session_id, json.dumps(full_plan),
        )

        # Save planned entries
        for entry in plan_json.get("entries", []):
            entry_id = _uuid()
            await conn.execute(
                """
                INSERT INTO planned_entries
                    (id, session_id, plan_version, side, status,
                     entry_zone_from, entry_zone_to, invalidation_price, stop_loss,
                     take_profit_json, recommended_leverage, budget_share_pct,
                     margin_mode, confirmation_rule, reason_code)
                VALUES ($1, $2, 1, $3, 'planned', $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                """,
                entry_id, session_id,
                entry.get("side", "long"),
                float(entry.get("entry_zone_from", 0)),
                float(entry.get("entry_zone_to", 0)),
                float(entry.get("invalidation_price", 0)),
                float(entry.get("stop_loss", 0)),
                json.dumps(entry.get("take_profit", [])),
                int(entry.get("recommended_leverage", 125)),
                float(entry.get("budget_share_pct", 15)),
                entry.get("margin_mode", "isolated"),
                entry.get("confirmation_rule", "any"),
                entry.get("reason_code", "trend_pullback_entry"),
            )

    # Update session status
    await update_session_status(session_id, "armed")

    log.info("Initial plan v1 saved for session %s with %d entries",
             session_id, len(plan_json.get("entries", [])))

    return {
        "plan_id": plan_id,
        "session_id": session_id,
        "version": version,
        "model_used": model_used,
        "entries_count": len(plan_json.get("entries", [])),
        "market_regime": plan_json.get("market_regime"),
        "thesis": plan_json.get("thesis"),
    }


# ─── Hourly Revision (ТЗ 5.4) ──────────────────────────────────────────

REVISION_PROMPT = """Ты — Crypto Trader Agent (Analyst), выполняешь почасовую корректировку плана.

Текущий план (версия {base_version}):
{current_plan}

Обновлённый рыночный контекст:
{market_context}

Создай PATCH (не переписывай весь план). Верни СТРОГО JSON:
{
  "market_regime_status": "intact|weakened|strengthened|reversed",
  "summary": "краткое описание изменений на английском",
  "execution_command": "continue|tighten|reduce|pause|close_all",
  "patch": {
    "update_session_risk": {},
    "update_entries": [],
    "cancel_entries": [],
    "add_entries": []
  }
}

ПРАВИЛА:
- execution_command: continue если план актуален, tighten если риск вырос, pause если неопределённость, close_all если тренд сломан
- update_entries: только изменившиеся поля (entry_id обязателен)
- cancel_entries: список entry_id для отмены
- add_entries: новые entry в том же формате что в initial plan
- Не более 2 новых entry
- Если рынок сильно изменился — верни close_all
"""


async def hourly_revision(session_id: str) -> dict:
    """
    ТЗ 5.4, 11 (hourly_recalibration) — Analyst выпускает patch.
    """
    from ai.models import MODELS, PREMIUM_MODEL_CHAIN
    from ai.router import get_client
    from storage.postgres_client import get_pool

    session = await get_session(session_id)
    if not session:
        return {"error": "Session not found"}

    pool = await get_pool()
    if not pool:
        return {"error": "No DB pool"}

    # Get current active plan
    async with pool.acquire() as conn:
        plan_row = await conn.fetchrow(
            "SELECT * FROM session_plans WHERE session_id=$1 ORDER BY version DESC LIMIT 1",
            session_id,
        )
    if not plan_row:
        return {"error": "No plan found for session"}

    current_plan = plan_row["plan_json"]
    if isinstance(current_plan, str):
        current_plan = json.loads(current_plan)

    base_version = int(plan_row["version"])

    # Build fresh market context
    market_ctx = await build_market_context(session["symbol"].lower())

    prompt = REVISION_PROMPT.format(
        base_version=base_version,
        current_plan=json.dumps(current_plan, indent=2, ensure_ascii=False),
        market_context=json.dumps(market_ctx, indent=2, ensure_ascii=False),
    )

    revision_json = None
    model_used = "unknown"

    for tier in PREMIUM_MODEL_CHAIN:
        cfg = MODELS.get(tier)
        if not cfg:
            continue
        client = get_client(tier)
        if client is None:
            continue

        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=cfg.model_id,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=1200,
                    temperature=0.2,
                ),
                timeout=cfg.timeout,
            )
            raw = (response.choices[0].message.content or "").strip()
            if raw.startswith("```"):
                lines = raw.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()

            revision_json = json.loads(raw)
            model_used = cfg.model_id
            log.info("Revision generated by %s for session %s", model_used, session_id)
            break
        except Exception as exc:
            log.warning("Revision failed on %s: %s", cfg.model_id, exc)
            continue

    if not revision_json:
        return {"error": "Analyst returned no valid revision", "model": model_used}

    new_version = base_version + 1
    revision_id = _uuid()

    async with pool.acquire() as conn:
        # Save revision
        await conn.execute(
            """
            INSERT INTO session_revisions
                (id, session_id, base_version, new_version, execution_command, revision_json)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            revision_id, session_id, base_version, new_version,
            revision_json.get("execution_command", "continue"),
            json.dumps(revision_json),
        )

        # Apply patch to planned_entries
        patch = revision_json.get("patch", {})

        # Cancel entries
        for cancel_id in patch.get("cancel_entries", []):
            await conn.execute(
                "UPDATE planned_entries SET status='cancelled' WHERE id=$1 AND session_id=$2",
                cancel_id, session_id,
            )

        # Update entries
        for update in patch.get("update_entries", []):
            entry_id = update.get("entry_id")
            if not entry_id:
                continue
            # Build dynamic update
            set_parts = []
            params = [entry_id, session_id]
            param_idx = 3
            for field_name, field_val in update.items():
                if field_name == "entry_id":
                    continue
                if field_name == "take_profit":
                    set_parts.append(f"take_profit_json=${param_idx}")
                    params.append(json.dumps(field_val))
                    param_idx += 1
                elif field_name in ("stop_loss", "entry_zone_from", "entry_zone_to",
                                     "invalidation_price", "budget_share_pct"):
                    set_parts.append(f"{field_name}=${param_idx}")
                    params.append(float(field_val))
                    param_idx += 1
                elif field_name == "recommended_leverage":
                    set_parts.append(f"recommended_leverage=${param_idx}")
                    params.append(int(field_val))
                    param_idx += 1
                elif field_name == "status":
                    set_parts.append(f"status=${param_idx}")
                    params.append(field_val)
                    param_idx += 1

            if set_parts:
                query = f"UPDATE planned_entries SET {', '.join(set_parts)} WHERE id=$1 AND session_id=$2"
                await conn.execute(query, *params)

        # Add new entries
        for new_entry in patch.get("add_entries", []):
            entry_id = _uuid()
            await conn.execute(
                """
                INSERT INTO planned_entries
                    (id, session_id, plan_version, side, status,
                     entry_zone_from, entry_zone_to, invalidation_price, stop_loss,
                     take_profit_json, recommended_leverage, budget_share_pct,
                     margin_mode, confirmation_rule, reason_code)
                VALUES ($1, $2, $3, $4, 'planned', $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                """,
                entry_id, session_id, new_version,
                new_entry.get("side", "long"),
                float(new_entry.get("entry_zone_from", 0)),
                float(new_entry.get("entry_zone_to", 0)),
                float(new_entry.get("invalidation_price", 0)),
                float(new_entry.get("stop_loss", 0)),
                json.dumps(new_entry.get("take_profit", [])),
                int(new_entry.get("recommended_leverage", 125)),
                float(new_entry.get("budget_share_pct", 15)),
                new_entry.get("margin_mode", "isolated"),
                new_entry.get("confirmation_rule", "any"),
                new_entry.get("reason_code", "revision_add"),
            )

        # Update session active_plan_version
        await conn.execute(
            "UPDATE trading_sessions SET active_plan_version=$2, updated_at=NOW() WHERE id=$1",
            session_id, new_version,
        )

    # Apply execution command to state machine
    cmd = revision_json.get("execution_command", "continue")
    if cmd == "pause":
        await update_session_status(session_id, "paused")
    elif cmd == "close_all":
        await update_session_status(session_id, "stopped", "close_all_command")

    log.info("Revision v%d saved for session %s: cmd=%s", new_version, session_id, cmd)

    return {
        "revision_id": revision_id,
        "session_id": session_id,
        "base_version": base_version,
        "new_version": new_version,
        "model_used": model_used,
        "execution_command": cmd,
        "summary": revision_json.get("summary"),
    }


# ─── Execution: open/close trades ──────────────────────────────────────

async def execute_entry(
    session_id: str,
    entry: dict,
    candle: dict,
    budget_usdt: float,
    indicators: dict | None = None,
) -> dict:
    """
    ТЗ 5.5 — открыть позицию по planned_entry если trigger подтверждён.
    """
    from storage.postgres_client import get_pool

    side = entry.get("side", "long")
    entry_from = float(entry.get("entry_zone_from", 0))
    entry_to = float(entry.get("entry_zone_to", 0))
    conf_rule = entry.get("confirmation_rule", "any")
    leverage = int(entry.get("recommended_leverage", 125))
    budget_share = float(entry.get("budget_share_pct", 15))

    # Check trigger
    triggered = check_entry_trigger(candle, entry_from, entry_to, conf_rule, indicators)
    if not triggered:
        return {"executed": False, "reason": "entry_not_confirmed"}

    # Check invalidation in same candle (ТЗ 7.2 conservative)
    inv_price = float(entry.get("invalidation_price", 0))
    if inv_price > 0 and check_invalidation(candle, inv_price, side):
        return {"executed": False, "reason": "invalidation_in_same_candle"}

    # Calculate position
    pos = calc_position_size(budget_usdt, budget_share, leverage)
    notional = pos["position_notional_usdt"]
    margin_used = pos["margin_used_usdt"]

    # Entry price with slippage
    raw_entry = float(candle.get("close", entry_from))
    entry_price = apply_slippage(raw_entry, side, is_entry=True)

    fees = calc_fees(notional)
    position_qty = notional / entry_price

    trade_id = _uuid()
    pool = await get_pool()
    if not pool:
        return {"error": "No DB pool"}

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO executed_trades
                (id, session_id, entry_id, side, margin_mode, leverage,
                 opened_at, entry_price, position_qty, position_notional_usdt,
                 margin_used_usdt, open_fee_usdt, status)
            VALUES ($1, $2, $3, $4, $5, $6, NOW(), $7, $8, $9, $10, $11, 'open')
            """,
            trade_id, session_id, entry.get("id"),
            side, entry.get("margin_mode", "isolated"), leverage,
            entry_price, position_qty, notional, margin_used, fees["open_fee_usdt"],
        )

        # Mark entry as triggered
        await conn.execute(
            "UPDATE planned_entries SET status='triggered' WHERE id=$1",
            entry.get("id"),
        )

        # Log execution event
        await conn.execute(
            """
            INSERT INTO execution_events
                (id, session_id, trade_id, entry_id, event_type, state_before, state_after, event_payload)
            VALUES ($1, $2, $3, $4, 'position_opened', 'armed', 'in_position', $5)
            """,
            _uuid(), session_id, trade_id, entry.get("id"),
            json.dumps({
                "side": side,
                "leverage": leverage,
                "entry_price": entry_price,
                "position_notional": notional,
                "margin_used": margin_used,
                "open_fee": fees["open_fee_usdt"],
                "reason_code": entry.get("reason_code", ""),
            }),
        )

        # Update session status
        await conn.execute(
            "UPDATE trading_sessions SET status='in_position', updated_at=NOW() WHERE id=$1",
            session_id,
        )

    log.info("Trade opened: session=%s trade=%s side=%s entry=%.2f lev=%dx",
             session_id, trade_id, side, entry_price, leverage)

    return {
        "executed": True,
        "trade_id": trade_id,
        "side": side,
        "entry_price": entry_price,
        "leverage": leverage,
        "position_notional": notional,
        "margin_used": margin_used,
        "open_fee": fees["open_fee_usdt"],
        "position_qty": position_qty,
    }


async def execute_exit(
    session_id: str,
    trade_id: str,
    exit_price: float,
    close_reason: str = "take_profit",
    mark_price: float | None = None,
) -> dict:
    """
    ТЗ 5.5 — закрыть позицию.
    """
    from storage.postgres_client import get_pool

    pool = await get_pool()
    if not pool:
        return {"error": "No DB pool"}

    async with pool.acquire() as conn:
        trade = await conn.fetchrow(
            "SELECT * FROM executed_trades WHERE id=$1 AND status='open'",
            trade_id,
        )
        if not trade:
            return {"error": "Trade not found or already closed"}

        side = trade["side"]
        entry_price = float(trade["entry_price"])
        position_qty = float(trade["position_qty"])
        notional = float(trade["position_notional_usdt"])
        open_fee = float(trade["open_fee_usdt"])

        # Exit price with slippage
    slip_exit = apply_slippage(exit_price, side, is_entry=False)

    close_fee = notional * TAKER_FEE_RATE
    total_fee = open_fee + close_fee
    realised = calc_realised_pnl(side, entry_price, slip_exit, position_qty, total_fee)

    pool = await get_pool()
    if not pool:
        return {"error": "No DB pool"}
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE executed_trades
            SET status='closed', closed_at=NOW(), exit_price=$2,
                mark_exit_price=$3, close_fee_usdt=$4, realised_pnl_usdt=$5, close_reason=$6
            WHERE id=$1
            """,
            trade_id, slip_exit, mark_price or slip_exit, close_fee, realised, close_reason,
        )

        # Log event
        state_after = "cooldown" if close_reason == "stop_loss" else "armed"
        await conn.execute(
            """
            INSERT INTO execution_events
                (id, session_id, trade_id, event_type, state_before, state_after, event_payload)
            VALUES ($1, $2, $3, 'position_closed', 'in_position', $4, $5)
            """,
            _uuid(), session_id, trade_id, state_after,
            json.dumps({
                "exit_price": slip_exit,
                "close_fee": close_fee,
                "realised_pnl": realised,
                "close_reason": close_reason,
            }),
        )

        # Update session status
        await conn.execute(
            "UPDATE trading_sessions SET status=$2, updated_at=NOW() WHERE id=$1",
            session_id, state_after,
        )

    log.info("Trade closed: session=%s trade=%s pnl=%.4f reason=%s",
             session_id, trade_id, realised, close_reason)

    return {
        "trade_id": trade_id,
        "exit_price": slip_exit,
        "realised_pnl": realised,
        "close_fee": close_fee,
        "close_reason": close_reason,
        "state_after": state_after,
    }


# ─── Execution Watch Loop (ТЗ 11) ──────────────────────────────────────

async def execution_watch_loop(session_id: str) -> dict:
    """
    ТЗ 11 (execution_watch_loop, каждые 10 сек) — проверить все planned entries
    и открытые позиции по текущей 1m свече.
    """
    from storage.postgres_client import get_pool
    from storage.redis_client import get_redis

    session = await get_session(session_id)
    if not session:
        return {"error": "Session not found"}

    if session["status"] in ("stopped", "completed", "paused"):
        return {"skipped": True, "reason": f"session_status={session['status']}"}

    pool = await get_pool()
    if not pool:
        return {"error": "No DB pool"}

    symbol = session["symbol"].lower()
    budget = float(session["initial_budget_usdt"])

    # Get current 1m candle from Redis
    redis = get_redis()
    ticker_raw = await redis.get(f"ticker:{symbol}")
    if not ticker_raw:
        return {"skipped": True, "reason": "no_market_data"}

    ticker = json.loads(ticker_raw)
    current_price = float(ticker["price"])

    # Build pseudo-candle from ticker
    candle = {
        "open": current_price,
        "high": current_price,
        "low": current_price,
        "close": current_price,
    }

    # Try to get last 1m candle from historical data
    try:
        import importlib
        rest_mod = importlib.import_module("htx.rest")
        get_recent_klines = getattr(rest_mod, "get_recent_klines", None)
        if get_recent_klines:
            klines = await get_recent_klines(symbol, "1min", 1)
            if klines:
                k = klines[-1]
                candle = {
                    "open": float(k.get("open", current_price)),
                    "high": float(k.get("high", current_price)),
                    "low": float(k.get("low", current_price)),
                    "close": float(k.get("close", current_price)),
                }
    except Exception:
        pass  # use ticker-based candle

    # Get indicators for confirmation
    indicators = None
    try:
        import importlib
        calc_mod = importlib.import_module("indicators.calculator")
        calculate_indicators = getattr(calc_mod, "calculate_indicators")
        indicators = await calculate_indicators(symbol, "1m")
    except Exception:
        pass

    actions = []

    # 1. Check open positions for exit conditions
    async with pool.acquire() as conn:
        open_trades = await conn.fetch(
            "SELECT * FROM executed_trades WHERE session_id=$1 AND status='open'",
            session_id,
        )
        planned_entries = await conn.fetch(
            "SELECT * FROM planned_entries WHERE session_id=$1 AND status='planned'",
            session_id,
        )

    # Check exits first (priority)
    for trade in open_trades:
        side = trade["side"]
        entry_price = float(trade["entry_price"])
        leverage = int(trade["leverage"])
        stop_loss = 0.0
        take_profits = []

        # Get stop/tp from planned entry
        if trade["entry_id"]:
            async with pool.acquire() as conn:
                pe = await conn.fetchrow(
                    "SELECT stop_loss, take_profit_json FROM planned_entries WHERE id=$1",
                    trade["entry_id"],
                )
                if pe:
                    stop_loss = float(pe["stop_loss"])
                    tp_raw = pe["take_profit_json"]
                    if isinstance(tp_raw, str):
                        tp_raw = json.loads(tp_raw)
                    take_profits = [float(x) for x in tp_raw if x]

        # Check liquidation
        liq_price = calc_liquidation_price(entry_price, leverage, side)
        if is_liquidated(current_price, liq_price, side):
            result = await execute_exit(session_id, str(trade["id"]), liq_price, "liquidation")
            actions.append({"action": "liquidation", "trade_id": str(trade["id"]), "result": result})
            await update_session_status(session_id, "stopped", "liquidation")
            break

        # Check stop-loss
        if stop_loss > 0 and check_stop_loss_hit(candle, stop_loss, side):
            result = await execute_exit(session_id, str(trade["id"]), stop_loss, "stop_loss")
            actions.append({"action": "stop_loss", "trade_id": str(trade["id"]), "result": result})
            continue

        # Check take-profit (first target)
        if take_profits:
            tp1 = take_profits[0]
            if check_take_profit_hit(candle, tp1, side):
                result = await execute_exit(session_id, str(trade["id"]), tp1, "take_profit")
                actions.append({"action": "take_profit", "trade_id": str(trade["id"]), "result": result})
                continue

    # 2. Check entries (only if no open position — ТЗ 7.4)
    has_open = any(a.get("action") != "liquidation" for a in actions)
    if not open_trades and not has_open:
        for pe in planned_entries:
            entry_dict = dict(pe)
            entry_dict["id"] = str(pe["id"])
            result = await execute_entry(session_id, entry_dict, candle, budget, indicators)
            if result.get("executed"):
                actions.append({"action": "entry", "entry_id": str(pe["id"]), "result": result})
                break  # ТЗ 7.4: only 1 position at a time

    # 3. Check session window completion
    session_end = session["session_end"]
    if session_end.tzinfo is None:
        session_end = session_end.replace(tzinfo=timezone.utc)
    if _now_utc() >= session_end and not open_trades:
        await update_session_status(session_id, "completed", "session_window_completed")
        actions.append({"action": "session_completed"})

    return {"session_id": session_id, "actions": actions}
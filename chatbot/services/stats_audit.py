"""
Daily Pipeline WORED v2 — Stats & Audit Agent.

ТЗ раздел 3 (Stats & Audit Agent): PnL, комиссии, сверка, статистика, аудит.
ТЗ раздел 8 (формулы): equity, drawdown, profit factor.
ТЗ раздел 5.6 (Session Summary): финальные метрики.
ТЗ раздел 11 (stats_snapshot, session_closeout, post_session_review).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from services.execution_engine import (
    calc_equity,
    calc_drawdown_pct,
    calc_profit_factor,
    calc_unrealised_pnl,
)

log = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ─── Stats Snapshot (ТЗ 11: stats_snapshot, каждые 60 сек) ─────────────

async def stats_snapshot(session_id: str) -> dict:
    """
    ТЗ 11 — обновить equity, PnL, drawdown, open risk каждую минуту.
    """
    from storage.postgres_client import get_pool
    from storage.redis_client import get_redis

    pool = await get_pool()
    if not pool:
        return {"error": "No DB pool"}

    async with pool.acquire() as conn:
        session = await conn.fetchrow(
            "SELECT * FROM trading_sessions WHERE id = $1",
            session_id,
        )
        if not session:
            return {"error": "Session not found"}

        trades = await conn.fetch(
            "SELECT * FROM executed_trades WHERE session_id = $1 ORDER BY opened_at",
            session_id,
        )

    budget = float(session["initial_budget_usdt"])
    symbol = session["symbol"].lower()

    # Get current price
    redis = get_redis()
    ticker_raw = await redis.get(f"ticker:{symbol}")
    current_price = 0.0
    if ticker_raw:
        current_price = float(json.loads(ticker_raw)["price"])

    # Calculate metrics
    realised_pnl_total = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    win_count = 0
    loss_count = 0
    liquidation_count = 0
    unrealised_pnl_total = 0.0
    peak_equity = budget
    current_equity = budget
    max_drawdown = 0.0
    win_streak = 0
    max_win_streak = 0
    loss_streak = 0
    max_loss_streak = 0
    time_in_market_seconds = 0
    total_session_seconds = 0

    session_start = session["session_start"]
    if session_start.tzinfo is None:
        session_start = session_start.replace(tzinfo=timezone.utc)
    total_session_seconds = (_now_utc() - session_start).total_seconds()

    for trade in trades:
        trade_pnl = 0.0
        if trade["status"] == "closed" and trade["realised_pnl_usdt"] is not None:
            trade_pnl = float(trade["realised_pnl_usdt"])
            realised_pnl_total += trade_pnl

            if trade_pnl > 0:
                gross_profit += trade_pnl
                win_count += 1
                win_streak += 1
                loss_streak = 0
                max_win_streak = max(max_win_streak, win_streak)
            elif trade_pnl < 0:
                gross_loss += abs(trade_pnl)
                loss_count += 1
                loss_streak += 1
                win_streak = 0
                max_loss_streak = max(max_loss_streak, loss_streak)

            if trade["close_reason"] == "liquidation":
                liquidation_count += 1

            # Time in market
            opened = trade["opened_at"]
            closed = trade["closed_at"]
            if opened and closed:
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=timezone.utc)
                if closed.tzinfo is None:
                    closed = closed.replace(tzinfo=timezone.utc)
                time_in_market_seconds += (closed - opened).total_seconds()

        elif trade["status"] == "open":
            # Unrealised PnL
            entry_price = float(trade["entry_price"])
            position_qty = float(trade["position_qty"])
            open_fee = float(trade["open_fee_usdt"])
            side = trade["side"]
            if current_price > 0:
                unreal = calc_unrealised_pnl(side, entry_price, current_price, position_qty, open_fee)
                unrealised_pnl_total += unreal

                opened = trade["opened_at"]
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=timezone.utc)
                time_in_market_seconds += (_now_utc() - opened).total_seconds()

        # Equity tracking
        current_equity = calc_equity(budget, realised_pnl_total, unrealised_pnl_total)
        if current_equity > peak_equity:
            peak_equity = current_equity
        dd = calc_drawdown_pct(peak_equity, current_equity)
        if dd > max_drawdown:
            max_drawdown = dd

    # Calculate derived metrics
    total_pnl = realised_pnl_total + unrealised_pnl_total
    total_pnl_pct = (total_pnl / budget * 100.0) if budget > 0 else 0.0
    trade_count = win_count + loss_count + liquidation_count
    profit_factor = calc_profit_factor(gross_profit, gross_loss)
    avg_win = gross_profit / win_count if win_count > 0 else None
    avg_loss = gross_loss / loss_count if loss_count > 0 else None
    time_in_market_pct = (time_in_market_seconds / total_session_seconds * 100.0) if total_session_seconds > 0 else 0.0
    idle_time_pct = 100.0 - time_in_market_pct

    metrics = {
        "session_id": session_id,
        "trade_count": trade_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "liquidation_count": liquidation_count,
        "total_pnl_usdt": round(total_pnl, 8),
        "total_pnl_pct": round(total_pnl_pct, 8),
        "max_drawdown_pct": round(max_drawdown, 8),
        "profit_factor": profit_factor,
        "avg_win_usdt": round(avg_win, 8) if avg_win is not None else None,
        "avg_loss_usdt": round(avg_loss, 8) if avg_loss is not None else None,
        "time_in_market_pct": round(time_in_market_pct, 8),
        "idle_time_pct": round(idle_time_pct, 8),
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "current_equity": round(current_equity, 8),
        "unrealised_pnl": round(unrealised_pnl_total, 8),
    }

    # Upsert into session_metrics
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO session_metrics
                (session_id, trade_count, win_count, loss_count, liquidation_count,
                 total_pnl_usdt, total_pnl_pct, max_drawdown_pct, profit_factor,
                 avg_win_usdt, avg_loss_usdt, time_in_market_pct, idle_time_pct,
                 max_win_streak, max_loss_streak, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, NOW())
            ON CONFLICT (session_id) DO UPDATE SET
                trade_count = EXCLUDED.trade_count,
                win_count = EXCLUDED.win_count,
                loss_count = EXCLUDED.loss_count,
                liquidation_count = EXCLUDED.liquidation_count,
                total_pnl_usdt = EXCLUDED.total_pnl_usdt,
                total_pnl_pct = EXCLUDED.total_pnl_pct,
                max_drawdown_pct = EXCLUDED.max_drawdown_pct,
                profit_factor = EXCLUDED.profit_factor,
                avg_win_usdt = EXCLUDED.avg_win_usdt,
                avg_loss_usdt = EXCLUDED.avg_loss_usdt,
                time_in_market_pct = EXCLUDED.time_in_market_pct,
                idle_time_pct = EXCLUDED.idle_time_pct,
                max_win_streak = EXCLUDED.max_win_streak,
                max_loss_streak = EXCLUDED.max_loss_streak,
                updated_at = NOW()
            """,
            session_id,
            metrics["trade_count"], metrics["win_count"], metrics["loss_count"],
            metrics["liquidation_count"], metrics["total_pnl_usdt"], metrics["total_pnl_pct"],
            metrics["max_drawdown_pct"], metrics["profit_factor"],
            metrics["avg_win_usdt"], metrics["avg_loss_usdt"],
            metrics["time_in_market_pct"], metrics["idle_time_pct"],
            metrics["max_win_streak"], metrics["max_loss_streak"],
        )

    return metrics


# ─── Session Closeout (ТЗ 11: session_closeout) ────────────────────────

async def session_closeout(session_id: str) -> dict:
    """
    ТЗ 11 — закрытие сессии, итоговые метрики.
    ТЗ 5.6 — Session Summary Schema.
    """
    from storage.postgres_client import get_pool
    from services.session_manager import update_session_status, get_session

    # Close any remaining open trades at current price
    pool = await get_pool()
    if not pool:
        return {"error": "No DB pool"}

    from storage.redis_client import get_redis
    redis = get_redis()

    async with pool.acquire() as conn:
        open_trades = await conn.fetch(
            "SELECT * FROM executed_trades WHERE session_id=$1 AND status='open'",
            session_id,
        )

    session = await get_session(session_id)
    if not session:
        return {"error": "Session not found"}

    symbol = session["symbol"].lower()
    ticker_raw = await redis.get(f"ticker:{symbol}")
    current_price = 0.0
    if ticker_raw:
        current_price = float(json.loads(ticker_raw)["price"])

    # Close remaining trades
    closed_count = 0
    for trade in open_trades:
        if current_price > 0:
            from services.session_manager import execute_exit
            await execute_exit(
                session_id, str(trade["id"]), current_price,
                close_reason="session_closeout",
            )
            closed_count += 1

    # Final metrics
    metrics = await stats_snapshot(session_id)

    # Update session status
    await update_session_status(session_id, "completed", "session_window_completed")

    # Build ТЗ 5.6 Session Summary
    summary = {
        "session_id": session_id,
        "status": "completed",
        "start_time": session["session_start"].isoformat() if session["session_start"] else "",
        "end_time": _now_utc().isoformat(),
        "budget_usdt": float(session["initial_budget_usdt"]),
        "end_equity_usdt": metrics["current_equity"],
        "total_pnl_usdt": metrics["total_pnl_usdt"],
        "total_pnl_pct": metrics["total_pnl_pct"],
        "trade_count": metrics["trade_count"],
        "win_count": metrics["win_count"],
        "loss_count": metrics["loss_count"],
        "liquidation_count": metrics["liquidation_count"],
        "max_drawdown_pct": metrics["max_drawdown_pct"],
        "profit_factor": metrics["profit_factor"],
        "time_in_market_pct": metrics["time_in_market_pct"],
        "final_status_reason": "session_window_completed",
    }

    log.info("Session %s closed: trades=%d pnl=%.4f dd=%.2f%%",
             session_id, summary["trade_count"], summary["total_pnl_usdt"],
             summary["max_drawdown_pct"])

    return summary


# ─── Post-Session Review (ТЗ 11: post_session_review, premium) ─────────

REVIEW_PROMPT = """Ты — Review Agent (Premium), проводишь итоговый разбор торговой сессии.

Сводка сессии:
{session_summary}

Сделки:
{trades_details}

Ревизии плана:
{revisions_details}

Проведи разбор:
1. Что сработало хорошо (what_worked)
2. Что не сработало (what_failed)
3. Рекомендации по изменению правил (rule_changes)

Верни СТРОГО JSON:
{
  "review_text": "развёрнутый текстовый разбор на русском",
  "what_worked": "что сработало",
  "what_failed": "что не сработало",
  "rule_changes": {"key": "value"}
}

Не является финансовым советом."""


async def post_session_review(session_id: str) -> dict:
    """
    ТЗ 11 (post_session_review) — Premium (glm-5.2) формирует review.
    ТЗ 14.2: Premium failure НЕ блокирует закрытие — review = deferred.
    """
    from ai.models import MODELS, PREMIUM_MODEL_CHAIN
    from ai.router import get_client
    from storage.postgres_client import get_pool

    pool = await get_pool()
    if not pool:
        return {"error": "No DB pool"}

    # Gather data
    async with pool.acquire() as conn:
        trades = await conn.fetch(
            "SELECT side, leverage, entry_price, exit_price, realised_pnl_usdt, close_reason, status FROM executed_trades WHERE session_id=$1 ORDER BY opened_at",
            session_id,
        )
        revisions = await conn.fetch(
            "SELECT base_version, new_version, execution_command, revision_json FROM session_revisions WHERE session_id=$1 ORDER BY created_at",
            session_id,
        )
        metrics = await conn.fetchrow(
            "SELECT * FROM session_metrics WHERE session_id=$1",
            session_id,
        )

    summary = await session_closeout(session_id) if not metrics else dict(metrics)

    trades_text = "\n".join([
        f"  #{i+1} {t['side']} {t['leverage']}x entry={float(t['entry_price']):.2f} exit={float(t['exit_price'] or 0):.2f} pnl={float(t['realised_pnl_usdt'] or 0):.4f} reason={t['close_reason']}"
        for i, t in enumerate(trades)
    ]) or "  No trades"

    revisions_text = "\n".join([
        f"  v{r['base_version']}→v{r['new_version']} cmd={r['execution_command']}"
        for r in revisions
    ]) or "  No revisions"

    prompt = REVIEW_PROMPT.format(
        session_summary=json.dumps(summary, indent=2, default=str),
        trades_details=trades_text,
        revisions_details=revisions_text,
    )

    review_json = None
    model_used = "unknown"

    for tier in PREMIUM_MODEL_CHAIN:
        cfg = MODELS.get(tier)
        if not cfg:
            continue
        client = get_client(tier)
        if client is None:
            continue

        try:
            import asyncio
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=cfg.model_id,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=2000,
                    temperature=0.4,
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

            review_json = json.loads(raw)
            model_used = cfg.model_id
            log.info("Review generated by %s for session %s", model_used, session_id)
            break
        except Exception as exc:
            log.warning("Review generation failed on %s: %s", cfg.model_id, exc)
            continue

    if not review_json:
        # ТЗ 14.2 — deferred review
        review_json = {
            "review_text": "Review deferred — Premium model unavailable",
            "what_worked": "",
            "what_failed": "",
            "rule_changes": {},
        }
        model_used = "deferred"

    # Save to daily_reviews
    import uuid
    review_id = str(uuid.uuid4())
    review_status = "deferred" if model_used == "deferred" else "completed"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO daily_reviews
                (id, session_id, review_model, review_text, what_worked, what_failed, rule_changes, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            review_id, session_id, model_used,
            review_json.get("review_text", ""),
            review_json.get("what_worked", ""),
            review_json.get("what_failed", ""),
            json.dumps(review_json.get("rule_changes", {})),
            review_status,
        )

    return {
        "review_id": review_id,
        "session_id": session_id,
        "model_used": model_used,
        "status": review_status,
        "review_text": review_json.get("review_text", ""),
    }


# ─── Audit Check (ТЗ 14.3) ─────────────────────────────────────────────

async def audit_check(session_id: str) -> dict:
    """
    ТЗ 14.3 — сверка расчётов Stats & Audit Agent.
    """
    from storage.postgres_client import get_pool

    pool = await get_pool()
    if not pool:
        return {"error": "No DB pool"}

    async with pool.acquire() as conn:
        trades = await conn.fetch(
            "SELECT * FROM executed_trades WHERE session_id=$1 AND status='closed'",
            session_id,
        )
        metrics = await conn.fetchrow(
            "SELECT * FROM session_metrics WHERE session_id=$1",
            session_id,
        )

    audit_issues = []

    # Recalculate PnL from trades and compare with metrics
    recalculated_pnl = sum(float(t["realised_pnl_usdt"] or 0) for t in trades)
    stored_pnl = float(metrics["total_pnl_usdt"]) if metrics else 0.0

    if abs(recalculated_pnl - stored_pnl) > 0.0001:
        audit_issues.append({
            "type": "pnl_mismatch",
            "recalculated": round(recalculated_pnl, 8),
            "stored": round(stored_pnl, 8),
            "diff": round(recalculated_pnl - stored_pnl, 8),
        })

    # Check trade count
    recalculated_count = len(trades)
    stored_count = int(metrics["trade_count"]) if metrics else 0
    if recalculated_count != stored_count:
        audit_issues.append({
            "type": "trade_count_mismatch",
            "recalculated": recalculated_count,
            "stored": stored_count,
        })

    audit_status = "ok" if not audit_issues else "audit_warning"

    if audit_issues:
        log.warning("Audit issues for session %s: %s", session_id, audit_issues)
        # Mark session with audit_warning
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO execution_events (id, session_id, event_type, event_payload)
                VALUES ($1, $2, 'audit_warning', $3)
                """,
                str(__import__("uuid").uuid44()),
                session_id,
                json.dumps({"issues": audit_issues}),
            )

    return {
        "session_id": session_id,
        "audit_status": audit_status,
        "issues": audit_issues,
        "trade_count": recalculated_count,
        "pnl_check": abs(recalculated_pnl - stored_pnl) < 0.0001,
    }
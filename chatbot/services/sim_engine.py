"""
Trade Simulation Engine — имитация фьючерсной торговли на HTX.
Симулирует: комиссии, ликвидацию, PnL, задержки исполнения.
Не реальные сделки — учебный/аналитический симулятор.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# HTX-подобные комиссии и параметры
TAKER_FEE_RATE = 0.0006   # 0.06% taker (рыночный ордер)
MAKER_FEE_RATE = 0.0002   # 0.02% maker (лимитный ордер)
FUNDING_RATE = 0.0001     # 0.01% каждые 8ч (упрощённо)
FUNDING_INTERVAL_HOURS = 8
EXECUTION_DELAY_MS = (50, 350)  # имитация задержки исполнения 50-350ms
LIQUIDATION_MARGIN = 0.005    # ликвидация при падении до 0.5% от маржи


@dataclass
class SimPosition:
    id: int
    user_id: int
    symbol: str
    direction: str          # long | short
    order_type: str         # limit | market
    margin_mode: str        # cross | isolated
    leverage: int
    margin: float           # USDT вложено
    entry_price: float
    size: float             # размер позиции в монете
    notional: float          # номинал = margin * leverage
    entry_fee: float
    status: str             # open | closed | liquidated
    close_price: Optional[float] = None
    close_fee: Optional[float] = None
    realized_pnl: Optional[float] = None
    funding_paid: float = 0.0
    opened_at: str = ""
    closed_at: Optional[str] = None
    close_reason: Optional[str] = None  # manual | ai | stop_loss | take_profit | liquidation
    ai_managed: bool = False  #True если "торгуй" — AI сама закрывает


SIM_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS sim_positions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    symbol VARCHAR(15) NOT NULL,
    direction VARCHAR(10) NOT NULL,
    order_type VARCHAR(10) NOT NULL,
    margin_mode VARCHAR(10) NOT NULL,
    leverage INT NOT NULL,
    margin DECIMAL(20, 8) NOT NULL,
    entry_price DECIMAL(20, 8) NOT NULL,
    size DECIMAL(20, 8) NOT NULL,
    notional DECIMAL(20, 8) NOT NULL,
    entry_fee DECIMAL(20, 8) NOT NULL,
    status VARCHAR(15) NOT NULL DEFAULT 'open',
    close_price DECIMAL(20, 8),
    close_fee DECIMAL(20, 8),
    realized_pnl DECIMAL(20, 8),
    funding_paid DECIMAL(20, 8) DEFAULT 0,
    opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,
    close_reason VARCHAR(20),
    ai_managed BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_sim_positions_user_status ON sim_positions (user_id, status);
CREATE INDEX IF NOT EXISTS idx_sim_positions_status ON sim_positions (status) WHERE status = 'open';
"""


async def _get_pool():
    """Get asyncpg pool from chatbot's postgres_client."""
    from storage.postgres_client import get_pool
    pool = await get_pool()
    # Ensure sim tables exist
    async with pool.acquire() as conn:
        for stmt in [s.strip() for s in SIM_TABLES_SQL.split(";") if s.strip()]:
            await conn.execute(stmt)
    return pool


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


async def open_position(
    user_id: int,
    symbol: str,
    direction: str,
    order_type: str,
    margin_mode: str,
    leverage: int,
    margin: float,
    entry_price: float,
    ai_managed: bool = False,
) -> dict:
    """
    Открыть симулированную позицию.
    Возвращает dict с деталями позиции.
    """
    direction = direction.lower()
    order_type = order_type.lower()
    margin_mode = margin_mode.lower()

    if direction not in ("long", "short"):
        return {"error": f"Направление должно long/short, получено: {direction}"}
    if order_type not in ("limit", "market"):
        return {"error": f"Тип ордера должен limit/market, получено: {order_type}"}
    if margin_mode not in ("cross", "isolated"):
        return {"error": f"Режим маржи должен cross/isolated, получено: {margin_mode}"}
    if leverage < 1 or leverage > 200:
        return {"error": f"Плечо должно 1-200x, получено: {leverage}"}
    if margin <= 0:
        return {"error": "Маржа должна быть положительной"}
    if entry_price <= 0:
        return {"error": "Цена входа должна быть положительной"}

    # Имитация задержки исполнения
    delay_ms = random.uniform(*EXECUTION_DELAY_MS)
    await asyncio.sleep(delay_ms / 1000)

    # Расчёт размера позиции
    notional = margin * leverage
    size = notional / entry_price

    # Комиссия за вход
    fee_rate = MAKER_FEE_RATE if order_type == "limit" else TAKER_FEE_RATE
    entry_fee = notional * fee_rate

    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO sim_positions
                (user_id, symbol, direction, order_type, margin_mode, leverage,
                 margin, entry_price, size, notional, entry_fee, status, ai_managed, opened_at)
            VALUES
                ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'open', $12, NOW())
            RETURNING id, opened_at
            """,
            user_id, symbol, direction, order_type, margin_mode, leverage,
            margin, entry_price, size, notional, entry_fee, ai_managed,
        )

    pos_id = row["id"]
    opened_at = row["opened_at"].isoformat() if row["opened_at"] else _now_utc()

    result = {
        "id": pos_id,
        "user_id": user_id,
        "symbol": symbol,
        "direction": direction,
        "order_type": order_type,
        "margin_mode": margin_mode,
        "leverage": leverage,
        "margin": margin,
        "entry_price": entry_price,
        "size": size,
        "notional": notional,
        "entry_fee": entry_fee,
        "status": "open",
        "ai_managed": ai_managed,
        "opened_at": opened_at,
        "execution_delay_ms": round(delay_ms, 1),
    }

    log.info(
        "Sim position #%d opened: %s %s %dx %s margin=%.2f entry=%.2f fee=%.4f delay=%.1fms",
        pos_id, direction, symbol.upper(), leverage, order_type, margin, entry_price, entry_fee, delay_ms,
    )
    return result


async def close_position(position_id: int, current_price: float, reason: str = "manual") -> dict:
    """
    Закрыть позицию по текущей цене.
    """
    pool = await _get_pool()
    async with pool.acquire() as conn:
        pos = await conn.fetchrow(
            "SELECT * FROM sim_positions WHERE id = $1 AND status = 'open'",
            position_id,
        )
        if not pos:
            return {"error": f"Позиция #{position_id} не найдена или уже закрыта"}

        # Имитация задержки
        delay_ms = random.uniform(*EXECUTION_DELAY_MS)
        await asyncio.sleep(delay_ms / 1000)

        notional = float(pos["notional"])
        size = float(pos["size"])
        entry_price = float(pos["entry_price"])
        margin = float(pos["margin"])
        direction = pos["direction"]
        funding_paid = float(pos["funding_paid"] or 0)

        # Комиссия за закрытие (taker — рыночное закрытие)
        close_fee = notional * TAKER_FEE_RATE

        # PnL расчёт
        if direction == "long":
            raw_pnl = (current_price - entry_price) * size
        else:
            raw_pnl = (entry_price - current_price) * size

        # Чистый PnL: raw - комиссии - funding
        realized_pnl = raw_pnl - float(pos["entry_fee"]) - close_fee - funding_paid

        # ROI %
        roi_pct = (realized_pnl / margin) * 100 if margin > 0 else 0

        await conn.execute(
            """
            UPDATE sim_positions
            SET status = 'closed', close_price = $2, close_fee = $3,
                realized_pnl = $4, closed_at = NOW(), close_reason = $5
            WHERE id = $1
            """,
            position_id, current_price, close_fee, realized_pnl, reason,
        )

    result = {
        "id": position_id,
        "symbol": pos["symbol"],
        "direction": direction,
        "leverage": pos["leverage"],
        "margin": margin,
        "entry_price": entry_price,
        "close_price": current_price,
        "entry_fee": float(pos["entry_fee"]),
        "close_fee": close_fee,
        "funding_paid": funding_paid,
        "raw_pnl": round(raw_pnl, 4),
        "realized_pnl": round(realized_pnl, 4),
        "roi_pct": round(roi_pct, 2),
        "close_reason": reason,
        "execution_delay_ms": round(delay_ms, 1),
        "status": "closed",
    }

    log.info(
        "Sim position #%d closed: %s %s pnl=%.4f roi=%.2f%% reason=%s",
        position_id, direction, pos["symbol"], realized_pnl, roi_pct, reason,
    )
    return result


async def get_open_positions(user_id: int | None = None) -> list[dict]:
    """Получить открытые позиции (все или конкретного пользователя)."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        if user_id:
            rows = await conn.fetch(
                "SELECT * FROM sim_positions WHERE user_id = $1 AND status = 'open' ORDER BY opened_at DESC",
                user_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM sim_positions WHERE status = 'open' ORDER BY opened_at DESC"
            )
    return [dict(r) for r in rows]


async def get_position(position_id: int) -> dict | None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM sim_positions WHERE id = $1", position_id)
    return dict(row) if row else None


async def get_user_history(user_id: int, limit: int = 20) -> list[dict]:
    """История закрытых позиций пользователя."""
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM sim_positions WHERE user_id = $1 AND status IN ('closed', 'liquidated') ORDER BY closed_at DESC LIMIT $2",
            user_id, limit,
        )
    return [dict(r) for r in rows]


def calculate_unrealized_pnl(position: dict, current_price: float) -> dict:
    """
    Расчёт нереализованного PnL и проверки ликвидации.
    """
    entry_price = float(position["entry_price"])
    size = float(position["size"])
    margin = float(position["margin"])
    leverage = int(position["leverage"])
    direction = position["direction"]
    entry_fee = float(position["entry_fee"])
    funding_paid = float(position.get("funding_paid", 0))

    if direction == "long":
        raw_pnl = (current_price - entry_price) * size
        liquidation_price = entry_price * (1 - 1 / leverage + LIQUIDATION_MARGIN)
    else:
        raw_pnl = (entry_price - current_price) * size
        liquidation_price = entry_price * (1 + 1 / leverage - LIQUIDATION_MARGIN)

    # Если цена дошла до уровня ликвидации
    is_liquidated = (
        direction == "long" and current_price <= liquidation_price
    ) or (
        direction == "short" and current_price >= liquidation_price
    )

    unrealized_pnl = raw_pnl - entry_fee - funding_paid
    roi_pct = (unrealized_pnl / margin) * 100 if margin > 0 else 0

    # ROI по отношению к марже с учётом плеча
    price_change_pct = ((current_price - entry_price) / entry_price) * 100
    if direction == "short":
        price_change_pct = -price_change_pct

    return {
        "unrealized_pnl": round(unrealized_pnl, 4),
        "roi_pct": round(roi_pct, 2),
        "price_change_pct": round(price_change_pct, 2),
        "liquidation_price": round(liquidation_price, 2),
        "is_liquidated": is_liquidated,
        "current_price": current_price,
    }


async def check_and_liquidate(current_prices: dict[str, float]) -> list[dict]:
    """
    Проверить все открытые позиции на ликвидацию.
    current_prices: {symbol: price}
    Возвращает список ликвидированных позиций.
    """
    positions = await get_open_positions()
    liquidated = []

    for pos in positions:
        symbol = pos["symbol"]
        if symbol not in current_prices:
            continue

        price = current_prices[symbol]
        pnl_info = calculate_unrealized_pnl(pos, price)

        if pnl_info["is_liquidated"]:
            result = await close_position(
                pos["id"], pnl_info["liquidation_price"], reason="liquidation"
            )
            if "error" not in result:
                liquidated.append(result)

    return liquidated


async def apply_funding(current_prices: dict[str, float]) -> int:
    """
    Применить funding rate к позициям старше FUNDING_INTERVAL_HOURS.
    Возвращает количество обновлённых позиций.
    """
    pool = await _get_pool()
    count = 0
    positions = await get_open_positions()

    for pos in positions:
        symbol = pos["symbol"]
        if symbol not in current_prices:
            continue

        # Проверяем, прошёл ли funding-интервал с момента открытия
        opened_at = pos["opened_at"]
        if isinstance(opened_at, str):
            opened_at = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=timezone.utc)

        age_hours = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
        funding_periods = int(age_hours // FUNDING_INTERVAL_HOURS)

        if funding_periods <= 0:
            continue

        notional = float(pos["notional"])
        funding_cost = notional * FUNDING_RATE * funding_periods

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE sim_positions SET funding_paid = $2 WHERE id = $1",
                pos["id"], funding_cost,
            )
        count += 1

    return count


def format_position_card(pos: dict, pnl: dict | None = None) -> str:
    """Форматировать карточку позиции для Telegram."""
    symbol = pos["symbol"].upper()
    direction = pos["direction"].upper()
    leverage = pos["leverage"]
    margin = float(pos["margin"])
    entry = float(pos["entry_price"])

    dir_emoji = "🟢" if direction == "LONG" else "🔴"
    ai_tag = " 🤖AI" if pos.get("ai_managed") else ""

    lines = [
        f"<b>#{pos['id']} {dir_emoji} {direction} {symbol} {leverage}x{ai_tag}</b>",
        f"Тип: {pos['order_type']} | Маржа: {pos['margin_mode']} | {margin:.2f} USDT",
        f"Вход: ${entry:,.2f}",
    ]

    if pnl:
        pnl_emoji = "📈" if pnl["unrealized_pnl"] >= 0 else "📉"
        lines.append(f"Тек.: ${pnl['current_price']:,.2f}")
        lines.append(f"{pnl_emoji} PnL: {pnl['unrealized_pnl']:+.4f} USDT ({pnl['roi_pct']:+.2f}%)")
        lines.append(f"💧 Ликв.: ${pnl['liquidation_price']:,.2f}")

    if pos.get("status") == "closed" and pos.get("realized_pnl") is not None:
        pnl_emoji = "📈" if float(pos["realized_pnl"]) >= 0 else "📉"
        lines.append(f"Закрыто: ${float(pos['close_price']):,.2f}")
        lines.append(f"{pnl_emoji} PnL: {float(pos['realized_pnl']):+.4f} USDT")
        lines.append(f"Причина: {pos.get('close_reason', 'manual')}")

    return "\n".join(lines)
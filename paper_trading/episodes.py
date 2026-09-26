"""Episode builder — one normalised trade record across both loops (block C, D6).

The paper engine persists closed trades in ``paper_v2_positions`` while the
chatbot simulator uses ``sim_positions``.  The statistical gate (block D) and the
learning loop must reason over a *single* episode schema, otherwise a "strategy"
can look better simply because the two tables spell their columns differently.

:func:`episode_from_paper_row` and :func:`episode_from_sim_row` normalise a
joined ``positions (+ fills)`` row into :class:`Episode`.  Critically,
``net_pnl`` is **recomputed** from the primitives through
:func:`trading_math.settlement` rather than trusting whichever figure each loop
stored, so the same trade represented in both loops yields an identical
``net_pnl`` *and* an identical ``data_hash`` (the acceptance criterion for
block C).  Each ``Episode`` carries the mandatory ``data_hash``,
``features_hash``, ``math_version`` and ``strategy_version`` so the gate can
honour the ban on mixing ``math_version`` (TZ §7.3).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping, Optional

from trading_math import TAKER_FEE_RATE, settlement

__all__ = [
    "Episode",
    "episode_from_paper_row",
    "episode_from_sim_row",
    "data_hash_of",
    "fetch_paper_episodes",
    "fetch_sim_episodes",
]

ZERO = Decimal(0)


def _dec(value, default=ZERO) -> Decimal:
    """Coerce a DB/JSON scalar to a normalised ``Decimal``."""
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _iso(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


@dataclass(frozen=True)
class Episode:
    """A normalised, immutable closed-trade record.

    Monetary fields are ``Decimal``.  ``net_pnl`` is recomputed via
    :mod:`trading_math`; ``stored_net_pnl`` keeps whatever the source table
    recorded, for reconciliation only.
    """

    source: str                 # "paper_v2" | "sim"
    position_ref: str           # position_id / sim id
    contract_code: str
    side: str                   # "long" | "short"
    entry_price: Decimal
    exit_price: Decimal
    size: Decimal
    leverage: int
    entry_fee: Decimal
    exit_fee: Decimal
    funding: Decimal            # cost, positive reduces PnL
    gross_pnl: Decimal
    net_pnl: Decimal
    stored_net_pnl: Decimal
    opened_at: str
    closed_at: str
    liquidated: bool
    math_version: str
    strategy_version: str
    data_hash: str
    features_hash: str = ""
    r_multiple: float = 0.0     # net_pnl / risk (risk = isolated margin at 1R)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_flat(self) -> bool:
        return self.net_pnl == 0


def _canonical_primitives(ep_side, entry, exit_, size, funding, opened_at, closed_at,
                          math_version, strategy_version, contract_code) -> str:
    """Stable serialisation used for :func:`data_hash_of`.

    Deliberately excludes ``leverage``/margin: the net result is a function of
    entry/exit/size/funding only, and those primitives are what must agree across
    the two loops for the same trade (block C acceptance)."""
    payload = {
        "side": ep_side,
        "contract": contract_code,
        "entry": format(entry.normalize(), "f"),
        "exit": format(exit_.normalize(), "f"),
        "size": format(size.normalize(), "f"),
        "funding": format(funding.normalize(), "f"),
        "opened_at": opened_at,
        "closed_at": closed_at,
        "math_version": str(math_version),
        "strategy_version": str(strategy_version),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def data_hash_of(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _features_hash(features: Optional[Mapping[str, Any]]) -> str:
    if not features:
        return ""
    blob = json.dumps(features, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _build(*, source, position_ref, contract_code, side, entry, exit_, size,
           leverage, funding, opened_at, closed_at, liquidated, math_version,
           strategy_version, stored_net_pnl, features=None, isolated_margin=ZERO,
           extra: Optional[Mapping[str, Any]] = None) -> Episode:
    side = side.lower()
    # Canonical net PnL recomputed from primitives — identical for identical
    # primitives regardless of the source loop (block C acceptance).
    entry_fee = abs(size * entry) * TAKER_FEE_RATE
    net_pnl_raw, exit_fee_raw = settlement(side, entry, exit_, size, entry_fee, funding)
    # settlement is Decimal-in/Decimal-out by contract; normalise for typing.
    net_pnl = net_pnl_raw if isinstance(net_pnl_raw, Decimal) else Decimal(str(net_pnl_raw))
    exit_fee = exit_fee_raw if isinstance(exit_fee_raw, Decimal) else Decimal(str(exit_fee_raw))
    gross_pnl = (exit_ - entry) * size * (Decimal(1) if side == "long" else Decimal(-1))

    risk = abs(isolated_margin)
    r_multiple = float(net_pnl / risk) if risk and risk != 0 else 0.0

    canonical = _canonical_primitives(
        side, entry, exit_, size, funding, opened_at, closed_at,
        math_version, strategy_version, contract_code,
    )
    return Episode(
        source=source,
        position_ref=str(position_ref),
        contract_code=contract_code,
        side=side,
        entry_price=entry,
        exit_price=exit_,
        size=size,
        leverage=int(leverage),
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        funding=funding,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        stored_net_pnl=stored_net_pnl,
        opened_at=opened_at,
        closed_at=closed_at,
        liquidated=liquidated,
        math_version=str(math_version),
        strategy_version=str(strategy_version),
        data_hash=data_hash_of(canonical),
        features_hash=_features_hash(features),
        r_multiple=r_multiple,
        metadata=dict(extra or {}),
    )


def episode_from_paper_row(row: Mapping[str, Any], *, strategy_version: str,
                           features: Optional[Mapping[str, Any]] = None) -> Episode:
    """Build an :class:`Episode` from a ``paper_v2_positions`` row."""
    side = str(row["side"])
    return _build(
        source="paper_v2",
        position_ref=row.get("position_id"),
        contract_code=str(row.get("instrument") or "BTC-USDT"),
        side=side,
        entry=_dec(row["avg_entry_price"]),
        exit_=_dec(row.get("close_price")),
        size=_dec(row.get("qty")),
        leverage=int(row.get("leverage") or 1),
        funding=_dec(row.get("funding_cashflow")),
        opened_at=_iso(row.get("opened_at")),
        closed_at=_iso(row.get("closed_at")),
        liquidated=str(row.get("status")) == "liquidated",
        math_version=row.get("schema_version") or row.get("owner_engine_version") or "2",
        strategy_version=strategy_version,
        stored_net_pnl=_dec(row.get("realized_net_pnl")),
        isolated_margin=_dec(row.get("isolated_margin")),
        features=features,
        extra={"account_id": str(row.get("account_id") or ""), "day_id": str(row.get("day_id") or "")},
    )


def episode_from_sim_row(row: Mapping[str, Any], *, strategy_version: str,
                         features: Optional[Mapping[str, Any]] = None) -> Episode:
    """Build an :class:`Episode` from a ``sim_positions`` row."""
    side = str(row.get("direction") or row.get("side"))
    leverage = int(row.get("leverage") or 1)
    margin = _dec(row.get("margin"))
    return _build(
        source="sim",
        position_ref=row.get("id"),
        contract_code=str(row.get("symbol") or "BTC-USDT"),
        side=side,
        entry=_dec(row["entry_price"]),
        exit_=_dec(row.get("close_price")),
        size=_dec(row.get("size")),
        leverage=leverage,
        funding=_dec(row.get("funding_paid")),
        opened_at=_iso(row.get("opened_at")),
        closed_at=_iso(row.get("closed_at")),
        liquidated=(str(row.get("status")) == "liquidated"
                    or str(row.get("close_reason")) == "liquidation"),
        math_version=row.get("calculation_version") or "1",
        strategy_version=strategy_version,
        stored_net_pnl=_dec(row.get("realized_pnl")),
        isolated_margin=margin,
        features=features,
        extra={"user_id": str(row.get("user_id") or "")},
    )


async def fetch_paper_episodes(pool, *, contract_code: Optional[str] = None,
                               since: Optional[datetime] = None,
                               strategy_version: str = "unknown") -> list[Episode]:
    """Load closed/liquidated ``paper_v2_positions`` as episodes (chronological)."""
    sql = ("SELECT * FROM paper_v2_positions WHERE status IN ('closed','liquidated')")
    args: list[Any] = []
    if contract_code:
        args.append(contract_code)
        sql += f" AND instrument = ${len(args)}"
    if since:
        args.append(since)
        sql += f" AND closed_at >= ${len(args)}"
    sql += " ORDER BY closed_at ASC"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [episode_from_paper_row(dict(r), strategy_version=strategy_version) for r in rows]


async def fetch_sim_episodes(pool, *, contract_code: Optional[str] = None,
                             since: Optional[datetime] = None,
                             strategy_version: str = "unknown") -> list[Episode]:
    """Load closed/liquidated ``sim_positions`` as episodes (chronological)."""
    sql = ("SELECT * FROM sim_positions WHERE status IN ('closed','liquidated')")
    args2: list[Any] = []
    if contract_code:
        args2.append(contract_code)
        sql += f" AND symbol = ${len(args2)}"
    if since:
        args2.append(since)
        sql += f" AND closed_at >= ${len(args2)}"
    sql += " ORDER BY closed_at ASC"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args2)
    return [episode_from_sim_row(dict(r), strategy_version=strategy_version) for r in rows]

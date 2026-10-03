"""paper_trading.replay — deterministic multi-window replay of a strategy (P6.4-C, MC-21).

ТЗ §35 / MC-21 want a *reproducible replay over several non-overlapping windows*,
train kept out of the holdout, and a comparison against no-trade / buy-hold with
**the same costs** — rather than a verdict on one lucky session. This module is
that orchestration. It is **pure and deterministic**: no clock reads, no RNG, no
network, no database. Given the same bars it produces byte-identical results, so
`replay_data_hash` pins the run.

What makes the P&L *not* fabricated: each fill is executed by the real simulated
engine in :mod:`paper_trading.execution` (``execute_market_order`` /
``execute_close``) with the engine's ``FEE_RATE`` and adverse slippage, so the
per-window ``net_pnl`` is the realised net the ledger would book — the same cost
model used for the buy-hold benchmark.

The strategy under replay is an explicitly-labelled **long-momentum probe**: it
goes long on a bullish close, manages the position intrabar with adverse-first
stop/target handling (the conservative rule required for replay inside one
candle, ТЗ §75), and force-closes at the window's last bar. It is a clean,
auditable stand-in for "a strategy" so MC-21's *reproducibility / holdout /
benchmark* properties are provable; swapping in :class:`BaselineV1Strategy` later
is a wiring change, not a change to the replay contract.

Python 3.9 compatible. All money is ``Decimal``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from paper_trading import execution
from paper_trading.execution import FEE_RATE, execute_close, execute_market_order
from paper_trading.market import PerpetualSnapshot, RiskTier
from paper_trading.replay_distribution import (
    CostModel,
    WindowOutcome,
    assert_disjoint_windows,
    buy_hold_with_costs,
    compare_to_benchmarks,
    holdout_distribution,
    replay_data_hash,
    split_train_holdout,
    train_distribution,
)
from paper_trading.strategy import Bar

__all__ = [
    "ReplayWindow",
    "run_window",
    "ReplayResult",
    "replay",
]

_INSTRUMENT = "BTC-USDT"
# contract_size = 1 makes base_qty == contracts, i.e. the golden execution
# semantics in execution.execute_close (see its docstring worked examples).
_CONTRACT_SIZE = Decimal(1)
_STEP = Decimal("0.001")
# generous top-of-book allowance so the probe's modest size always fills whole
_AVAIL_FRACTION = Decimal(1000)
_NO_TIER = RiskTier(1, 100, Decimal("0.01"), "1970-01-01T00:00:00+00:00", "replay")


# ---------------------------------------------------------------------------
# snapshot construction from a bar
# ---------------------------------------------------------------------------


def _snapshot(bar: Bar, price: Decimal | None = None) -> PerpetualSnapshot:
    """Build an executable snapshot at ``bar.close`` (or an explicit ``price``).

    bid/ask/last/mark/index are set equal (a tight, non-crossed book); the engine
    still applies *adverse* slippage per side, so fills are conservative. Times
    ride the bar's own ISO timestamp for reproducibility.
    """
    p = price if price is not None else bar.close
    iso = bar.timestamp
    return PerpetualSnapshot(
        schema_version=1,
        mode="replay",
        venue="htx",
        market_type="linear-swap",
        contract_code=_INSTRUMENT,
        bid=p,
        ask=p,
        last=p,
        mark=p,
        index=p,
        funding_rate=Decimal(0),
        next_funding_at=None,
        component_times={"ticker": iso, "index": iso, "mark": iso, "funding": iso},
        contract_size=_CONTRACT_SIZE,
        price_tick=Decimal("0.1"),
        quantity_step=_STEP,
        source_at=iso,
        received_at=iso,
        source="replay",
        quality="replay",
        risk_tier=_NO_TIER,
    )


def _quantity_for_budget(budget: Decimal, ref_price: Decimal) -> Decimal:
    """Contracts a budget buys at ``ref_price`` (contract_size=1), rounded down to step."""
    if ref_price <= 0:
        return Decimal(0)
    raw = budget / ref_price
    return (raw / _STEP).to_integral_value(rounding="ROUND_DOWN") * _STEP


# ---------------------------------------------------------------------------
# one window
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayWindow:
    window_id: str
    split: str  # "train" | "holdout"
    bars: Sequence[Bar]


def _window_data_hash(bars: Sequence[Bar]) -> str:
    payload = [
        {
            "ts": b.timestamp,
            "o": format(b.open, "f"),
            "h": format(b.high, "f"),
            "l": format(b.low, "f"),
            "c": format(b.close, "f"),
        }
        for b in bars
    ]
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def run_window(
    window: ReplayWindow,
    *,
    budget: Decimal,
    slippage_bps: int = 2,
    stop_pct: Decimal = Decimal("0.02"),
    tp_pct: Decimal = Decimal("0.04"),
) -> WindowOutcome:
    """Replay one window through the execution engine; return its outcome.

    Deterministic given ``window.bars``. ``buy_hold_net`` is the same-cost
    buy-and-hold over the window's first/last close, so the benchmark is charged
    identical fees/slippage (MC-21 "the same costs").
    """
    bars = list(window.bars)
    costs = CostModel(
        fee_rate=FEE_RATE, slippage_bps=Decimal(slippage_bps), funding=Decimal(0)
    )
    if len(bars) < 2:
        bh = Decimal(0)
    else:
        bh = buy_hold_with_costs(
            start_price=Decimal(bars[0].close), end_price=Decimal(bars[-1].close),
            budget=Decimal(budget), costs=costs,
        )

    net = Decimal(0)
    position: execution.Position | None = None  # open probe position
    trades = 0

    for i, bar in enumerate(bars):
        # 1) manage an open position intrabar (adverse-first: stop before target)
        if position is not None:
            close = _maybe_manage(position, bar, slippage_bps)
            if close is not None:
                net += close.realized_net
                trades += 1
                position = None
            # a bar both opened-and-stopped is still conservative (stop wins)

        # 2) consider a new entry (not on the final bar — nothing left to exit on)
        if position is None and i < len(bars) - 1:
            snap = _snapshot(bar)
            if bar.close > bar.open:  # bullish momentum trigger
                qty = _quantity_for_budget(Decimal(budget), snap.ask)
                if qty > 0:
                    stop_price = (bar.close * (Decimal(1) - stop_pct)).quantize(Decimal("0.1"))
                    take_profit = (bar.close * (Decimal(1) + tp_pct)).quantize(Decimal("0.1"))
                    result = execute_market_order(
                        snap, "long", qty, 1, stop_price,
                        position_id=f"{window.window_id}-t{trades}",
                        account_id="replay", instrument=_INSTRUMENT,
                        take_profit=take_profit, slippage_bps=slippage_bps,
                        available_fraction=_AVAIL_FRACTION,
                    )
                    if result.filled and result.position is not None:
                        position = result.position

    # 3) force-close any open position at the last bar (session end)
    if position is not None and bars:
        last = bars[-1]
        res = execute_close(
            position, _snapshot(last), close_quantity=position.quantity,
            exit_reason="session_end", slippage_bps=slippage_bps,
        )
        net += res.realized_net
        trades += 1
        position = None

    return WindowOutcome(
        window_id=window.window_id,
        split=window.split,
        net_pnl=net,
        buy_hold_net=bh if isinstance(bh, Decimal) else Decimal(bh),
        start_utc=bars[0].timestamp if bars else None,
        end_utc=bars[-1].timestamp if bars else None,
        data_hash=_window_data_hash(bars),
    )


def _maybe_manage(
    position: execution.Position, bar: Bar, slippage_bps: int
) -> execution.CloseResult | None:
    """Close the position on this bar if stop or target is touched (adverse-first)."""
    if bar.low <= position.stop_price:
        return execute_close(
            position, _snapshot(bar, price=position.stop_price),
            close_quantity=position.quantity, exit_reason="stop_loss",
            slippage_bps=slippage_bps,
        )
    if position.take_profit is not None and bar.high >= position.take_profit:
        return execute_close(
            position, _snapshot(bar, price=position.take_profit),
            close_quantity=position.quantity, exit_reason="take_profit",
            slippage_bps=slippage_bps,
        )
    return None


# ---------------------------------------------------------------------------
# whole replay: many windows → train/holdout split + benchmarks + distribution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayResult:
    outcomes: tuple[WindowOutcome, ...]
    windows: int
    train_windows: int
    holdout_windows: int
    train: dict
    holdout: dict
    benchmarks: dict
    data_hash: str

    def to_dict(self) -> dict:
        return dict(
            windows=self.windows,
            outcomes=[o.window_id for o in self.outcomes],
            train_windows=self.train_windows,
            holdout_windows=self.holdout_windows,
            train=self.train,
            holdout=self.holdout,
            benchmarks=self.benchmarks,
            data_hash=self.data_hash,
        )


def replay(
    windows: Sequence[ReplayWindow],
    *,
    budget: Decimal,
    slippage_bps: int = 2,
    stop_pct: Decimal = Decimal("0.02"),
    tp_pct: Decimal = Decimal("0.04"),
) -> ReplayResult:
    """Replay every window, enforce non-overlap, and summarise out-of-sample.

    The headline verdict (:data:`holdout`, :data:`benchmarks`) is computed on the
    **holdout** windows only — train is reported separately and never folded into
    the PASS basis. ``data_hash`` is the reproducibility anchor for the window set.
    """
    outcomes: list[WindowOutcome] = []
    for w in windows:
        outcomes.append(
            run_window(
                w, budget=budget, slippage_bps=slippage_bps,
                stop_pct=stop_pct, tp_pct=tp_pct,
            )
        )

    assert_disjoint_windows(outcomes)

    train, holdout = split_train_holdout(outcomes)
    return ReplayResult(
        outcomes=tuple(outcomes),
        windows=len(outcomes),
        train_windows=len(train),
        holdout_windows=len(holdout),
        train=train_distribution(outcomes).to_dict(),
        holdout=holdout_distribution(outcomes).to_dict(),
        benchmarks=compare_to_benchmarks(outcomes).to_dict(),
        data_hash=replay_data_hash(outcomes),
    )

"""Integration test for the forecast-driven exit branch of ``_check_sl_tp``.

The gate is fully unit-tested in :mod:`test_forecast_gate`; this file only pins
the wiring on the runner side: when the evaluator rejects the hold, the
``to_close`` list gets a ``forecast_exit`` entry, a Decision with
``ReasonCode.forecast_exit`` is recorded, and neither an SL nor a TP hit
suppresses the check.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from paper_trading import forecast_gate
from paper_trading.contracts import Position, PositionSide, PositionStatus, ReasonCode
from paper_trading.forecast_gate import GateResult
from paper_trading.runner import PaperTradingRunner
from paper_trading.strategy import Bar, BaselineV1Config, BaselineV1Strategy


def _bars(closes: list[float]) -> list[Bar]:
    return [
        Bar(
            timestamp=f"b{i}",
            open=Decimal(str(c)),
            high=Decimal(str(c)),
            low=Decimal(str(c)),
            close=Decimal(str(c)),
            volume=Decimal(0),
        )
        for i, c in enumerate(closes)
    ]


class _FakeMarketData:
    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars

    async def fetch_bars(self, tf: str, count: int) -> list[Bar]:  # noqa: ARG002
        return list(self._bars)


def _runner_with_position(
    *,
    forecast_exit_enabled: bool,
    hold_seconds: float,
    mark: Decimal,
    side: PositionSide,
) -> tuple[PaperTradingRunner, Position, list[tuple[str, str]]]:
    cfg = BaselineV1Config(
        forecast_exit_enabled=forecast_exit_enabled,
        forecast_exit_min_hold_seconds=0.0 if hold_seconds == 0 else 60.0,
    )
    runner = PaperTradingRunner(strategy=BaselineV1Strategy(cfg))
    runner.market_data = _FakeMarketData(_bars([100, 102, 101, 104, 103, 106, 105, 108, 107, 110]))

    async def _snapshot(*_a, **_k):
        return SimpleNamespace(mark=mark, bid=mark, ask=mark, last=mark, index=mark)

    runner._fetch_snapshot = _snapshot  # type: ignore[method-assign]

    # Record close calls instead of touching the DB.  The default runner has no
    # repository so ``_execute_position_close`` would fall through to a plain
    # dict pop; we want to see the ``exit_reason`` string.
    closes: list[tuple[str, str]] = []

    async def _record_close(pos: Position, _snapshot, exit_reason: str) -> None:  # noqa: ARG001
        closes.append((str(pos.position_id), exit_reason))

    runner._execute_position_close = _record_close  # type: ignore[method-assign]

    opened_at = datetime.now(timezone.utc) - timedelta(seconds=hold_seconds)
    if side == PositionSide.long:
        sl = mark - Decimal("5")
        tp = mark + Decimal("20")
    else:
        sl = mark + Decimal("5")
        tp = mark - Decimal("20")
    pos = Position(
        position_id=uuid4(),
        account_id=uuid4(),
        day_id=uuid4(),
        side=side,
        status=PositionStatus.open,
        qty=Decimal("1"),
        avg_entry_price=mark,
        isolated_margin=Decimal("100"),
        stop_loss=sl,
        take_profit=tp,
        opened_at=opened_at,
    )
    runner._positions[pos.position_id] = pos
    return runner, pos, closes


@pytest.mark.asyncio
async def test_forecast_exit_close_when_hold_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, pos, closes = _runner_with_position(
        forecast_exit_enabled=True,
        hold_seconds=120,
        mark=Decimal("100"),
        side=PositionSide.long,
    )

    def _fake_evaluate(*_a, **_k) -> GateResult:
        return GateResult(
            accept=False,
            p_hit=Decimal("0.05"),
            ev=Decimal("-1.5"),
            detail="long [hold] rejected by test",
        )

    monkeypatch.setattr(forecast_gate, "evaluate_open_position", _fake_evaluate)

    await runner._check_sl_tp(datetime.now(timezone.utc).timestamp())

    assert closes == [(str(pos.position_id), "forecast_exit")]
    assert runner._last_decision is not None
    assert runner._last_decision.reason_code == ReasonCode.forecast_exit
    assert "rejected by test" in (runner._last_decision.reason_detail or "")


@pytest.mark.asyncio
async def test_forecast_exit_keep_when_hold_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, _pos, closes = _runner_with_position(
        forecast_exit_enabled=True,
        hold_seconds=120,
        mark=Decimal("100"),
        side=PositionSide.long,
    )

    def _fake_evaluate(*_a, **_k) -> GateResult:
        return GateResult(
            accept=True,
            p_hit=Decimal("0.55"),
            ev=Decimal("4.2"),
            detail="long [hold] kept by test",
        )

    monkeypatch.setattr(forecast_gate, "evaluate_open_position", _fake_evaluate)

    await runner._check_sl_tp(datetime.now(timezone.utc).timestamp())

    assert closes == []


@pytest.mark.asyncio
async def test_forecast_exit_skipped_before_min_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Position opened only 10s ago; the default 60s min-hold must guard the
    # first-minute noise.  ``forecast_gate.evaluate_open_position`` should not
    # be called at all in this cycle.
    runner, _pos, closes = _runner_with_position(
        forecast_exit_enabled=True,
        hold_seconds=10,
        mark=Decimal("100"),
        side=PositionSide.long,
    )

    def _boom(*_a, **_k):
        raise AssertionError("evaluate_open_position must not run inside min-hold")

    monkeypatch.setattr(forecast_gate, "evaluate_open_position", _boom)

    await runner._check_sl_tp(datetime.now(timezone.utc).timestamp())

    assert closes == []


@pytest.mark.asyncio
async def test_forecast_exit_disabled_never_calls_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, _pos, closes = _runner_with_position(
        forecast_exit_enabled=False,
        hold_seconds=120,
        mark=Decimal("100"),
        side=PositionSide.long,
    )

    def _boom(*_a, **_k):
        raise AssertionError("evaluator must be inert when forecast_exit_enabled=False")

    monkeypatch.setattr(forecast_gate, "evaluate_open_position", _boom)

    await runner._check_sl_tp(datetime.now(timezone.utc).timestamp())

    assert closes == []


@pytest.mark.asyncio
async def test_sl_hit_short_circuits_forecast_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A protective SL fires first; the forecast evaluator must never be asked
    # whether to close an already-hit position (the ``continue`` in the SL
    # branch is load-bearing).
    runner, pos, closes = _runner_with_position(
        forecast_exit_enabled=True,
        hold_seconds=120,
        mark=Decimal("100"),
        side=PositionSide.long,
    )
    # Move the mark below the booked stop so check_stop_trigger fires.
    pos = Position(
        position_id=pos.position_id,
        account_id=pos.account_id,
        day_id=pos.day_id,
        side=PositionSide.long,
        status=PositionStatus.open,
        qty=pos.qty,
        avg_entry_price=pos.avg_entry_price,
        isolated_margin=pos.isolated_margin,
        stop_loss=Decimal("105"),
        take_profit=Decimal("130"),
        opened_at=pos.opened_at,
    )
    runner._positions[pos.position_id] = pos

    def _boom(*_a, **_k):
        raise AssertionError("SL hit must short-circuit the forecast-exit check")

    monkeypatch.setattr(forecast_gate, "evaluate_open_position", _boom)

    # Mark price below the new SL ⇒ SL hit path.
    async def _snapshot(*_a, **_k):
        return SimpleNamespace(
            mark=Decimal("100"), bid=Decimal("100"), ask=Decimal("100"),
            last=Decimal("100"), index=Decimal("100"),
        )

    runner._fetch_snapshot = _snapshot  # type: ignore[method-assign]

    await runner._check_sl_tp(datetime.now(timezone.utc).timestamp())

    assert closes == [(str(pos.position_id), "stop_loss")]


@pytest.mark.asyncio
async def test_tp_hit_short_circuits_forecast_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mirror of the SL short-circuit test on the profitable side.
    runner, pos, closes = _runner_with_position(
        forecast_exit_enabled=True,
        hold_seconds=120,
        mark=Decimal("135"),
        side=PositionSide.long,
    )
    # Rebuild position with a TP below mark so the TP branch fires.
    pos = Position(
        position_id=pos.position_id,
        account_id=pos.account_id,
        day_id=pos.day_id,
        side=PositionSide.long,
        status=PositionStatus.open,
        qty=pos.qty,
        avg_entry_price=pos.avg_entry_price,
        isolated_margin=pos.isolated_margin,
        stop_loss=Decimal("130"),
        take_profit=Decimal("131"),
        opened_at=pos.opened_at,
    )
    runner._positions[pos.position_id] = pos

    def _boom(*_a, **_k):
        raise AssertionError("TP hit must short-circuit the forecast-exit check")

    monkeypatch.setattr(forecast_gate, "evaluate_open_position", _boom)

    await runner._check_sl_tp(datetime.now(timezone.utc).timestamp())

    assert closes == [(str(pos.position_id), "take_profit")]


@pytest.mark.asyncio
async def test_bars_fetch_failure_keeps_position_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # market_data.fetch_bars raising on the exit path must not close anything;
    # the runner catches the exception, logs a warning and skips this cycle.
    cfg = BaselineV1Config(
        forecast_exit_enabled=True, forecast_exit_min_hold_seconds=0.0
    )
    runner = PaperTradingRunner(strategy=BaselineV1Strategy(cfg))

    class _Boom:
        async def fetch_bars(self, tf, count):  # noqa: ARG002
            raise RuntimeError("redis unavailable")

    runner.market_data = _Boom()

    async def _snapshot(*_a, **_k):
        m = Decimal("100")
        return SimpleNamespace(mark=m, bid=m, ask=m, last=m, index=m)

    runner._fetch_snapshot = _snapshot  # type: ignore[method-assign]

    closes: list[tuple[str, str]] = []

    async def _record_close(pos: Position, _snap, exit_reason: str) -> None:  # noqa: ARG001
        closes.append((str(pos.position_id), exit_reason))

    runner._execute_position_close = _record_close  # type: ignore[method-assign]

    def _boom(*_a, **_k):
        raise AssertionError("if bars fetch failed, evaluator must not run")

    monkeypatch.setattr(forecast_gate, "evaluate_open_position", _boom)

    now = datetime.now(timezone.utc)
    runner._positions[uuid4()] = Position(
        position_id=uuid4(),
        account_id=uuid4(),
        day_id=uuid4(),
        side=PositionSide.long,
        status=PositionStatus.open,
        qty=Decimal("1"),
        avg_entry_price=Decimal("100"),
        isolated_margin=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profit=Decimal("120"),
        opened_at=now - timedelta(seconds=120),
    )

    await runner._check_sl_tp(now.timestamp())

    assert closes == []

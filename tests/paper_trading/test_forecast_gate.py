"""Pure unit tests for the forecast EV gate (④b).

No DB, no clock, no market feed — the gate is a deterministic function of
``(bars, signal, cfg)``, so each test builds a synthetic price path and a signal
and asserts the accept/reject verdict plus the fail-closed paths.
"""
from __future__ import annotations

from decimal import Decimal

from paper_trading.forecast_gate import evaluate, evaluate_open_position
from paper_trading.strategy import Bar, BaselineV1Config, Signal


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


def _signal(side: str, entry: float, stop: float, tp: float) -> Signal:
    e = Decimal(str(entry))
    return Signal(
        side=side,
        close_price=e,
        bar_timestamp="t",
        ema20_at_trigger=Decimal(str(tp)),  # unused by the gate
        atr_at_trigger=Decimal("1"),
        stop_loss=Decimal(str(stop)),
        take_profit=Decimal(str(tp)),
        entry_zone_low=e,
        entry_zone_high=e,
        net_rr=Decimal("4"),
        created_at_epoch=0.0,
    )


def _cfg(**over) -> BaselineV1Config:
    base = dict(
        fee_rate=Decimal("0.0006"),
        forecast_gate_enabled=True,
        forecast_gate_model="B1_QUANTILE",
        forecast_gate_horizon_steps=3,
        forecast_gate_min_ev=Decimal("0"),
        forecast_exit_enabled=True,
        forecast_exit_min_ev=Decimal("0"),
        forecast_exit_min_hold_seconds=0.0,
    )
    base.update(over)
    return BaselineV1Config(**base)


UPTREND = [100, 102, 101, 104, 103, 106, 105, 108, 107, 110]
DOWNTREND = [110, 108, 109, 106, 107, 104, 105, 102, 103, 100]


def test_long_with_favourable_drift_is_accepted() -> None:
    res = evaluate(_bars(UPTREND), _signal("long", 110, 105, 130), _cfg())
    assert res.accept is True
    assert res.ev > 0
    # A rising drift must push the hit-probability above the fair 1/(rr+1)≈0.2.
    assert res.p_hit > Decimal("0.2")


def test_long_against_adverse_drift_is_rejected() -> None:
    res = evaluate(_bars(DOWNTREND), _signal("long", 100, 95, 120), _cfg())
    assert res.accept is False
    assert res.ev < 0
    # Falling drift drives the long hit-probability below its fair value.
    assert res.p_hit < Decimal("0.2")


def test_short_with_favourable_drift_is_accepted() -> None:
    # The same falling market is favourable for a short: drift sign is flipped.
    res = evaluate(_bars(DOWNTREND), _signal("short", 100, 105, 80), _cfg())
    assert res.accept is True
    assert res.ev > 0


def test_higher_min_ev_rejects_a_marginal_signal() -> None:
    # The uptrend long clears min_ev=0 but not an absurdly high bar.
    ok = evaluate(_bars(UPTREND), _signal("long", 110, 105, 130), _cfg())
    assert ok.accept is True
    strict = evaluate(
        _bars(UPTREND),
        _signal("long", 110, 105, 130),
        _cfg(forecast_gate_min_ev=Decimal("1000")),
    )
    assert strict.accept is False


def test_b0_naive_zero_band_fails_closed() -> None:
    # B0 predicts high==low==close, so the band has zero width: no dispersion to
    # gate on, and the gate must reject rather than pass.
    res = evaluate(
        _bars(UPTREND),
        _signal("long", 110, 105, 130),
        _cfg(forecast_gate_model="B0_NAIVE"),
    )
    assert res.accept is False
    assert "zero width" in res.detail


def test_unknown_model_fails_closed() -> None:
    res = evaluate(
        _bars(UPTREND),
        _signal("long", 110, 105, 130),
        _cfg(forecast_gate_model="GLM-5.2"),
    )
    assert res.accept is False
    assert "unknown forecast model" in res.detail


def test_insufficient_bars_fails_closed() -> None:
    res = evaluate(_bars([100.0]), _signal("long", 100, 95, 120), _cfg())
    assert res.accept is False
    assert "insufficient bars" in res.detail


def test_non_positive_reward_rejected() -> None:
    # TP on the wrong side of entry for a long → reward <= 0 → reject.
    res = evaluate(_bars(UPTREND), _signal("long", 110, 105, 108), _cfg())
    assert res.accept is False
    assert "non-positive reward" in res.detail


# ── Forecast-driven exit (⑤): evaluate_open_position ────────────────────
# accept=True on this evaluator means *do not close* — the semantics are the
# mirror image of the entry gate, which returns accept=False on any error.


def test_open_long_in_uptrend_is_kept() -> None:
    # Rising drift + already part-way toward TP: remaining edge is still
    # positive, so accept=True means the exit gate keeps the position open.
    res = evaluate_open_position(
        _bars(UPTREND), "long", Decimal("110"), Decimal("105"), Decimal("130"), _cfg()
    )
    assert res.accept is True
    assert res.ev > 0
    assert "[hold]" in res.detail


def test_open_long_in_downtrend_closes() -> None:
    # Rising prices were wrong; drift is now negative, remaining EV of holding
    # is negative, so the evaluator returns accept=False and the runner
    # force-closes at market before the booked SL is touched.
    res = evaluate_open_position(
        _bars(DOWNTREND), "long", Decimal("100"), Decimal("95"), Decimal("120"), _cfg()
    )
    assert res.accept is False
    assert res.ev < 0


def test_open_short_in_downtrend_is_kept() -> None:
    # Falling market is favourable for a short, so hold.
    res = evaluate_open_position(
        _bars(DOWNTREND), "short", Decimal("100"), Decimal("105"), Decimal("80"), _cfg()
    )
    assert res.accept is True
    assert res.ev > 0


def test_exit_fail_open_on_unknown_model() -> None:
    # Unlike the entry gate, an unknown model here returns accept=True so a
    # typo in the forecast config never force-closes a live position.
    res = evaluate_open_position(
        _bars(UPTREND),
        "long",
        Decimal("110"),
        Decimal("105"),
        Decimal("130"),
        _cfg(forecast_gate_model="GLM-5.2"),
    )
    assert res.accept is True
    assert "unknown forecast model" in res.detail


def test_exit_fail_open_on_b0_zero_band() -> None:
    # B0 predicts high==low==close, so the band is zero-width. The exit
    # evaluator keeps the position (fail-open) whereas the entry evaluator
    # rejects it (fail-closed) — the two evaluators share the formula but not
    # the error discipline.
    res = evaluate_open_position(
        _bars(UPTREND),
        "long",
        Decimal("110"),
        Decimal("105"),
        Decimal("130"),
        _cfg(forecast_gate_model="B0_NAIVE"),
    )
    assert res.accept is True
    assert "zero width" in res.detail


def test_exit_fail_open_on_barrier_already_crossed() -> None:
    # Current price past TP for a long → reward <= 0. The TP trigger in the
    # same cycle will close the position; the forecast evaluator must not
    # attempt a third close path.
    res = evaluate_open_position(
        _bars(UPTREND), "long", Decimal("131"), Decimal("105"), Decimal("130"), _cfg()
    )
    assert res.accept is True
    assert "non-positive remaining" in res.detail


def test_high_exit_min_ev_rejects_a_marginal_hold() -> None:
    # Same uptrend the default 0-threshold accepts, but an absurdly strict
    # min_ev forces the runner to close the position even in a rising market.
    keep = evaluate_open_position(
        _bars(UPTREND), "long", Decimal("110"), Decimal("105"), Decimal("130"), _cfg()
    )
    assert keep.accept is True
    strict = evaluate_open_position(
        _bars(UPTREND),
        "long",
        Decimal("110"),
        Decimal("105"),
        Decimal("130"),
        _cfg(forecast_exit_min_ev=Decimal("10000")),
    )
    assert strict.accept is False

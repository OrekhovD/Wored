"""Block E.2 — Hedge-weighted forecast ensemble tests.

Acceptance: on a regime-switching sequence the Hedge ensemble's rolling pinball
loss must beat the best *static* single member, weights must stay on the simplex,
and a member that stops predicting well must be faded out over time.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from forecast_engine.contracts import OHLCVBar
from forecast_engine.ensemble import (
    HedgeEnsemble,
    OnlineLogistic,
    QuantileForecast,
    member_b0,
    member_b1,
    member_momentum,
)

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _switching_series(seed: int = 7, n: int = 800) -> list[OHLCVBar]:
    """Phase A: steady uptrend (quantile/drift models win).
    Phase B: sine reversal (persistence wins; drift models overshoot)."""
    rng = random.Random(seed)
    bars: list[OHLCVBar] = []
    px = Decimal("100")
    for i in range(n):
        if i < n // 2:
            px = px * (Decimal(1) + Decimal(str(rng.gauss(0.001, 0.0002))))
        else:
            base = 120 + 15 * math.sin((i - n // 2) / 30.0)
            px = Decimal(str(base + rng.gauss(0, 0.5)))
        bars.append(OHLCVBar(
            _T0 + timedelta(minutes=i), px, px * Decimal("1.001"),
            px * Decimal("0.999"), px, Decimal("1"),
        ))
    return bars


def test_weights_on_simplex() -> None:
    ens = HedgeEnsemble()
    w = ens.weights()
    assert len(w) == 4
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert all(0.0 <= v <= 1.0 for v in w.values())


def test_predict_band_is_monotone_and_prob_bounded() -> None:
    bars = _switching_series()
    ens = HedgeEnsemble()
    f = ens.predict(bars[:50])
    assert isinstance(f, QuantileForecast)
    assert f.q10 <= f.q50 <= f.q90
    assert Decimal(0) <= f.p_up <= Decimal(1)
    assert Decimal(0) <= f.confidence <= Decimal(1)


def test_ensemble_beats_best_static_member() -> None:
    bars = _switching_series()
    ens = HedgeEnsemble(eta=0.5, decay=0.97)
    res = ens.walk(bars, warmup=25)
    best_single = min(v for k, v in res.items() if k != "ensemble")
    assert res["ensemble"] < best_single, f"{res['ensemble']} !< {best_single}"


def test_bad_member_faded_over_time() -> None:
    # momentum is a fixed drift rule; on a pure flat-noise series persistence
    # should dominate and momentum's weight must collapse below b0's.
    rng = random.Random(1)
    bars = [OHLCVBar(_T0 + timedelta(minutes=i), Decimal("100"), Decimal("100.1"),
                     Decimal("99.9"), Decimal(str(100 + rng.gauss(0, 0.05))), Decimal("1"))
            for i in range(400)]
    ens = HedgeEnsemble(eta=0.5, decay=0.97)
    ens.walk(bars, warmup=25)
    w = ens.weights()
    assert w["momentum"] < w["b0"]


def test_logistic_starts_neutral() -> None:
    lg = OnlineLogistic()
    bars = _switching_series()
    f = lg.predict(bars[:60])
    assert f.p_up == Decimal("0.5")  # zero weights → sigmoid(0) → 0.5


def test_members_are_callable() -> None:
    bars = _switching_series()
    for fn in (member_b0, member_b1, member_momentum):
        f = fn(bars[:40])
        assert isinstance(f, QuantileForecast)
        assert f.q10 <= f.q50 <= f.q90

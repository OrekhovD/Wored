"""paper_trading.forecast_gate — probability-weighted EV gate for entry (④b).

The BaselineV1 trigger decides *where* to trade; this module decides *whether the
edge is real after cost*.  It runs the deterministic forecast baseline over the
same 1m bars the strategy already fetched, extracts a per-bar return drift and
volatility from the step-1 prediction (that model's own output, not re-derived),
and converts them into a *barrier-hitting probability* — the chance the price
touches the take-profit before it touches the stop, using the closed form for a
Brownian motion with drift.  The signal is accepted only when the
probability-weighted round trip clears ``min_ev``.  With ``min_ev = 0`` that is
exactly the project rule *a winning trade must exceed the round-trip commission*.

Design constraints (AGENTS.md / project rules):
  * Decimal in, Decimal out — the money math matches the strategy and the
    execution engine; floats are confined to the exponent helper.
  * Single source of truth — the fee comes from ``BaselineV1Config.fee_rate``, the
    entry/SL/TP from the strategy's emitted ``Signal``; the gate never re-derives
    a level.
  * Fail-closed — an unknown model, an exception, an empty forecast or a
    degenerate (zero-width) band all yield a REJECT, never a silent pass.  The
    runner's whole discipline is fail-closed, and a gate that opens on error is
    not a gate.
  * No wall-clock dependence — it is a pure function of ``(bars, signal, cfg)``,
    so it is deterministic and trivially unit-testable.

Why a barrier formula and not just a terminal-CDF: the strategy places the stop
at ``sl_atr_fraction · ATR`` and the target at ``tp_rr · risk``.  With ``tp_rr=4``
the TP sits 4 risk-units above the entry, well beyond the 1-σ move of a 3-minute
forecast.  A terminal-CDF therefore prints ``p_hit ≈ 0`` for every reasonable
signal; that is not a real edge verdict, it is the wrong model.  The hitting
probability of a drifted Brownian motion, by contrast, is ``risk / (reward+risk)``
at zero drift — the fair value for a ``tp_rr`` trade — and only exceeds that when
the forecast's drift actually points the way.  That is precisely what an "is there
edge?" gate is supposed to measure.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

from forecast_engine.contracts import ModelVersion, OHLCVBar
from forecast_engine.models import Z_90, predict
from paper_trading.strategy import Bar, BaselineV1Config, Signal

ZERO = Decimal(0)
ONE = Decimal(1)
TWO = Decimal(2)

# Synthetic, monotonic 1-minute stamps.  The gate reads only the price path
# (predicted_close / band); the model's open_time/close_time are irrelevant here,
# so we avoid parsing the strategy's string timestamps entirely and keep the
# function pure and clock-free.
_EPOCH0 = datetime(1970, 1, 1, tzinfo=timezone.utc)

# Numerical guard: math.expm1 saturates around |x|=713 on IEEE-754 doubles.
# Beyond 700 we substitute the limiting value, which is a 1-epsilon saturation.
_EXP_CLAMP = 700.0
# Below this |θ|, the drift is negligible and we use the drift-free limit
# x/L so the ratio never divides through expm1 of a near-zero argument.
_THETA_EPS = 1e-9


@dataclass(frozen=True)
class GateResult:
    """Outcome of one gate evaluation.

    ``accept`` is the only field the runner must act on; ``p_hit``/``ev``/
    ``detail`` are for the decision log and the ``paper_v2_signals`` audit so a
    human can see *why* a real signal was let go.
    """

    accept: bool
    p_hit: Decimal
    ev: Decimal
    detail: str


def _to_ohlcv(bars: Sequence[Bar]) -> list[OHLCVBar]:
    out: list[OHLCVBar] = []
    for i, b in enumerate(bars):
        out.append(
            OHLCVBar(
                time=_EPOCH0 + timedelta(minutes=i),
                open=Decimal(str(b.open)),
                high=Decimal(str(b.high)),
                low=Decimal(str(b.low)),
                close=Decimal(str(b.close)),
                volume=Decimal(str(b.volume)),
            )
        )
    return out


def _reject(reason: str) -> GateResult:
    return GateResult(accept=False, p_hit=ZERO, ev=ZERO, detail=reason)


def _hit_prob_return_space(theta: float, x: float, L: float) -> float:
    """P(X hits +upper before −lower) for a drifted Brownian motion.

    Coordinates are return-space: start at ``x`` (distance to the lower barrier
    = risk), upper barrier at ``L`` (= reward + risk).  For ``θ = 2μ/σ²`` the
    closed form is ``P = expm1(−θx) / expm1(−θL)``.  The ``θ→0`` limit reduces to
    the fair barrier ratio ``x/L``.  Clamped arguments keep ``expm1`` in range.
    """
    if L <= 0:
        return 0.0
    if abs(theta) < _THETA_EPS:
        return x / L
    tx = max(-_EXP_CLAMP, min(_EXP_CLAMP, -theta * x))
    tL = max(-_EXP_CLAMP, min(_EXP_CLAMP, -theta * L))
    num = math.expm1(tx)
    den = math.expm1(tL)
    if den == 0.0:
        # Degenerate: θ·L so small that expm1 underflowed to zero. Use the
        # θ→0 limit which is always safe.
        return x / L
    return num / den


def evaluate(
    bars: Sequence[Bar],
    signal: Signal,
    cfg: BaselineV1Config,
) -> GateResult:
    """Return whether *signal* clears the forecast EV gate over *bars*.

    Fail-closed on any error path.  Never raises into the caller — a rejected
    :class:`GateResult` is the answer to "should we trade this?", including when
    the forecast itself could not be computed.
    """
    try:
        model = ModelVersion(cfg.forecast_gate_model)
    except ValueError:
        return _reject(f"unknown forecast model {cfg.forecast_gate_model!r}")

    horizon = int(cfg.forecast_gate_horizon_steps)
    if horizon < 1:
        return _reject(f"forecast horizon steps must be >= 1, got {horizon}")
    if len(bars) < 2:
        return _reject("insufficient bars to run a forecast")

    try:
        ohlcv = _to_ohlcv(bars)
        candles = predict(ohlcv, model, 1)
    except Exception as exc:  # noqa: BLE001 — fail-closed by contract
        return _reject(f"forecast failed: {exc}")
    if not candles:
        return _reject("forecast produced no candles")

    entry = signal.close_price
    if entry <= 0:
        return _reject(f"non-positive entry price {entry}")
    if signal.side == "long":
        reward = signal.take_profit - entry
        risk = entry - signal.stop_loss
    elif signal.side == "short":
        reward = entry - signal.take_profit
        risk = signal.stop_loss - entry
    else:
        return _reject(f"unknown signal side {signal.side!r}")
    if reward <= 0 or risk <= 0:
        return _reject(f"non-positive reward/risk (reward={reward}, risk={risk})")

    # Extract per-bar return drift and volatility from the step-1 forecast, which
    # is what B1 exposes as predicted_close and the q10/q90 band.  Using the
    # step-1 candle keeps the barrier inputs as one-bar quantities so the barrier
    # formula and the return-space distances stay dimensionally consistent.
    step1 = candles[0]
    if step1.predicted_close <= 0:
        return _reject("forecast predicted_close is non-positive")
    mu = step1.predicted_close / entry - ONE  # per-bar return drift
    band = step1.predicted_high - step1.predicted_close  # q90 − q50 in price units
    if Z_90 <= 0 or band <= 0:
        return _reject("forecast band has zero width; no dispersion to gate on")
    sigma = band / (Z_90 * step1.predicted_close)  # per-bar return vol

    # Return-space barrier geometry.
    reward_ret = reward / entry
    risk_ret = risk / entry

    # The barrier formula measures progress toward the +reward (favourable) leg.
    # For a long that is a positive price return; for a short the position gains
    # when price falls, so the favourable drift is the negation of the price
    # drift.  Volatility is direction-symmetric and needs no sign change.
    favourable_drift = float(mu) if signal.side == "long" else -float(mu)
    theta_f = (2.0 * favourable_drift) / (float(sigma) ** 2) if float(sigma) > 0 else 0.0
    p_hit_f = _hit_prob_return_space(theta_f, float(risk_ret), float(reward_ret + risk_ret))
    # Clamp to a valid probability (numerical safety at extreme θ).
    p_hit_f = max(0.0, min(1.0, p_hit_f))
    p_hit = Decimal(str(p_hit_f))

    # Expected value per unit in quote currency.  Fill quantity scales both the
    # gross leg and the fee by the same factor, so the per-unit sign equals the
    # total sign — the gate is quantity-independent and deterministic.
    fee_cost = entry * cfg.fee_rate * TWO
    ev = p_hit * reward - (ONE - p_hit) * risk - fee_cost
    accept = ev >= cfg.forecast_gate_min_ev
    detail = (
        f"{signal.side} p_hit={p_hit:.4f} mu={mu:.6f} sigma={sigma:.6f} "
        f"reward={reward:.2f} risk={risk:.2f} fee={fee_cost:.2f} "
        f"ev={ev:.4f} min_ev={cfg.forecast_gate_min_ev} "
        f"model={model.value} steps={horizon}"
    )
    return GateResult(accept=accept, p_hit=p_hit, ev=ev, detail=detail)

"""forecast_engine.ensemble — Hedge-weighted multi-model forecast ensemble (block E.2).

Fast-ring and deterministic — no LLM anywhere near this path (ADR-03).  It pools
four cheap one-step-ahead forecasters:

* ``b0``       — naive persistence (the non-negotiable floor),
* ``b1``       — the EWMA quantile baseline from :mod:`forecast_engine.models`,
* ``momentum`` — an EMA-slope / ATR-band rule model,
* ``logistic`` — an online (SGD) logistic regression on
  :func:`forecast_engine.features.extract_features` that emits an up-probability.

Weights are maintained by **exponentiated gradient / Hedge** driven by each
member's *rolling* quantile (pinball) loss, refreshed on every closed bar.  A
forgetting factor keeps the cumulative loss windowed so a member that stops
predicting well is faded out.  Direction confidence comes from the
:mod:`forecast_engine.calibration` isotonic calibrator, not a constant.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Dict, List, Sequence, Tuple

from forecast_engine.calibration import IsotonicCalibrator
from forecast_engine.contracts import OHLCVBar
from forecast_engine.metrics import QUANTILES, _pinball_single
from forecast_engine.models import predict_b1

__all__ = ["QuantileForecast", "HedgeEnsemble", "OnlineLogistic", "member_b0",
           "member_b1", "member_momentum"]

ZERO = Decimal(0)
ONE = Decimal(1)
HALF = Decimal("0.5")
# z-scores for the q10/q90 band of a symmetric normal.
_Z90 = Decimal("1.2815515655446004")


@dataclass(frozen=True)
class QuantileForecast:
    """One-step-ahead q10/q50/q90 band plus a directional probability."""

    q10: Decimal
    q50: Decimal
    q90: Decimal
    p_up: Decimal
    confidence: Decimal = field(default=Decimal("0.5"))

    def triple(self) -> Tuple[Decimal, Decimal, Decimal]:
        return (self.q10, self.q50, self.q90)


def _ret_series(bars: Sequence[OHLCVBar]) -> List[Decimal]:
    out: List[Decimal] = []
    for i in range(1, len(bars)):
        prev = bars[i - 1].close
        if prev != ZERO:
            out.append(bars[i].close / prev - ONE)
    return out


def _atr(bars: Sequence[OHLCVBar], period: int = 14) -> Decimal:
    window = bars[-period:] if len(bars) >= period else bars
    trs = [(b.high - b.low) for b in window]
    return (sum(trs, ZERO) / Decimal(len(trs))) if trs else ZERO


def _ema_slope(bars: Sequence[OHLCVBar], fast: int = 5, slow: int = 20) -> Decimal:
    closes = [b.close for b in bars]
    if len(closes) < slow:
        return ZERO

    def _ema(vals: List[Decimal], period: int) -> Decimal:
        alpha = Decimal(2) / Decimal(period + 1)
        seed = sum(vals[:period], ZERO) / Decimal(period)
        e = seed
        for v in vals[period:]:
            e = alpha * v + (ONE - alpha) * e
        return e

    return _ema(closes, fast) - _ema(closes, slow)


# --------------------------------------------------------------------------- #
# Members (bars -> one-step QuantileForecast)
# --------------------------------------------------------------------------- #

def member_b0(bars: Sequence[OHLCVBar]) -> QuantileForecast:
    last = bars[-1].close
    return QuantileForecast(last, last, last, HALF, HALF)


def member_b1(bars: Sequence[OHLCVBar]) -> QuantileForecast:
    fc = predict_b1(bars, horizon=1)
    if not fc:
        return member_b0(bars)
    c = fc[0]
    return QuantileForecast(c.predicted_low, c.predicted_close, c.predicted_high, c.p_up, c.confidence)


def member_momentum(bars: Sequence[OHLCVBar]) -> QuantileForecast:
    last = bars[-1]
    slope = _ema_slope(bars)
    denom = last.close if last.close != ZERO else ONE
    # Bounded momentum return so a runaway trend cannot dominate the pool.
    ret = slope / denom
    ret = max(Decimal("-0.02"), min(Decimal("0.02"), ret))
    q50 = last.close * (ONE + ret)
    band = _atr(bars) * _Z90
    q90 = q50 + band
    q10 = q50 - band
    p_up = HALF + max(Decimal("-0.4"), min(Decimal("0.4"), ret * Decimal(20)))
    return QuantileForecast(q10, q50, q90, p_up, HALF)


class OnlineLogistic:
    """SGD logistic regression on the forecast feature vector → up-probability.

    Feature names are discovered lazily from the first non-empty feature dict
    (numeric values only).  Weights start at zero, so the member is neutral
    (``p_up = 0.5``) until it has been trained — it cannot hallucinate edge.
    """

    def __init__(self, lr: float = 0.05, l2: float = 1e-3) -> None:
        self.lr = lr
        self.l2 = l2
        self.w: Dict[str, float] = {}
        self.trained = 0

    def _vec(self, bars: Sequence[OHLCVBar]) -> Dict[str, float]:
        feats = _extract(bars)
        vec: Dict[str, float] = {}
        for k, v in feats.items():
            if k in ("features_hash", "n_bars"):
                continue
            if isinstance(v, bool) or v is None:
                continue
            if isinstance(v, (int, float, Decimal)):
                fv = float(v)
                if math.isfinite(fv):
                    # Clip to a bounded range: features include price-level
                    # magnitudes; without this the online weights saturate.
                    vec[k] = max(-5.0, min(5.0, fv))
        return vec

    def _raw_p(self, vec: Dict[str, float]) -> float:
        z = sum(self.w.get(k, 0.0) * x for k, x in vec.items())
        z = max(-30.0, min(30.0, z))
        return 1.0 / (1.0 + math.exp(-z))

    def predict(self, bars: Sequence[OHLCVBar]) -> QuantileForecast:
        last = bars[-1]
        vec = self._vec(bars)
        p = self._raw_p(vec)
        p_up = Decimal(str(round(p, 6)))
        # Map the (p - 0.5) tilt into a small bounded drift around the last close,
        # clamped so a saturated probability cannot throw the point far off.
        ret = Decimal(str((p - 0.5) * 0.02))
        ret = max(Decimal("-0.005"), min(Decimal("0.005"), ret))
        q50 = last.close * (ONE + ret)
        # Widen the band by the tilt so mis-signed drift is not punished as hard.
        band = _atr(bars) * _Z90 * (ONE + abs(Decimal(str((p - 0.5) * 2))) * Decimal("3"))
        return QuantileForecast(q50 - band, q50, q50 + band, p_up, HALF)

    def update(self, bars: Sequence[OHLCVBar], label_up: int) -> None:
        vec = self._vec(bars)
        if not vec:
            return
        y = 1.0 if label_up >= 0.5 else 0.0
        p = self._raw_p(vec)
        err = p - y
        for k, x in vec.items():
            self.w[k] = self.w.get(k, 0.0) - self.lr * (err * x + self.l2 * self.w.get(k, 0.0))
        self.trained += 1


def _extract(bars: Sequence[OHLCVBar]) -> Dict[str, object]:
    from forecast_engine.features import extract_features
    return extract_features(bars)


# --------------------------------------------------------------------------- #
# Hedge ensemble
# --------------------------------------------------------------------------- #

@dataclass
class _Member:
    name: str
    fn: Callable[[Sequence[OHLCVBar]], QuantileForecast]
    cum_loss: float = 0.0  # forgetting-weighted cumulative pinball loss


class HedgeEnsemble:
    """Linear-pooled quantile ensemble with exponentiated-gradient (Hedge) weights.

    ``eta`` is the Hedge learning rate; ``decay`` (``gamma``) is the forgetting
    factor applied to cumulative loss so recent performance dominates.  The
    weight of member *i* is ``exp(-eta * cum_loss_i)`` normalised to the simplex.
    """

    def __init__(
        self,
        members: Sequence[Tuple[str, Callable[[Sequence[OHLCVBar]], QuantileForecast]]] | None = None,
        *,
        eta: float = 0.5,
        decay: float = 0.97,
        calibrator: IsotonicCalibrator | None = None,
    ) -> None:
        if members is None:
            self.logistic = OnlineLogistic()
            members = [
                ("b0", member_b0),
                ("b1", member_b1),
                ("momentum", member_momentum),
                ("logistic", self.logistic.predict),
            ]
        else:
            self.logistic = None
        self.eta = float(eta)
        self.decay = float(decay)
        self.calibrator = calibrator or IsotonicCalibrator()
        self._members: List[_Member] = [_Member(name, fn) for name, fn in members]
        if not self._members:
            raise ValueError("HedgeEnsemble requires at least one member")

    # -- weights ----------------------------------------------------------

    def weights(self) -> Dict[str, float]:
        exponents = [math.exp(-self.eta * m.cum_loss) for m in self._members]
        total = sum(exponents)
        if total <= 0:
            uniform = 1.0 / len(self._members)
            return {m.name: uniform for m in self._members}
        return {m.name: e / total for m, e in zip(self._members, exponents)}

    # -- prediction -------------------------------------------------------

    def predict(self, bars: Sequence[OHLCVBar]) -> QuantileForecast:
        if len(bars) < 2:
            return member_b0(bars)
        ws = self.weights()
        q10 = q50 = q90 = p_up = 0.0
        for m in self._members:
            f = m.fn(bars)
            w = ws[m.name]
            q10 += w * float(f.q10)
            q50 += w * float(f.q50)
            q90 += w * float(f.q90)
            p_up += w * float(f.p_up)
        # keep pooled band monotone
        if q10 > q50:
            q10 = q50
        if q90 < q50:
            q90 = q50
        p_up_dec = Decimal(str(round(max(0.0, min(1.0, p_up)), 6)))
        cal = Decimal(str(round(self.calibrator.calibrate(p_up), 6)))
        confidence = max(cal, ONE - cal)
        return QuantileForecast(
            Decimal(str(round(q10, 8))),
            Decimal(str(round(q50, 8))),
            Decimal(str(round(q90, 8))),
            p_up_dec,
            confidence,
        )

    # -- online update on a closed bar ------------------------------------

    def observe(self, past_bars: Sequence[OHLCVBar], closed_bar: OHLCVBar) -> Dict[str, float]:
        """Score every member against ``closed_bar`` and refresh Hedge weights.

        ``past_bars`` are the bars available *before* ``closed_bar`` (no
        look-ahead); ``closed_bar`` is the newly closed bar whose ``close`` is
        the realised outcome.  Returns the per-member pinball losses for this
        step.  Also trains the logistic member and the calibrator.
        """
        if len(past_bars) < 2:
            return {}
        actual = closed_bar.close
        realized_up = 1 if closed_bar.close > closed_bar.open else 0
        losses: Dict[str, float] = {}
        taus = [float(t) for t in QUANTILES]
        for m in self._members:
            f = m.fn(past_bars)
            a = float(actual)
            loss = sum(_pinball_single(t, a, float(p)) for t, p in zip(taus, f.triple())) / len(taus)
            # forgetting-weighted cumulative pinball loss (price units); the
            # exponent below is what makes the good members dominate the simplex.
            m.cum_loss = self.decay * m.cum_loss + loss
            losses[m.name] = loss
        if self.logistic is not None:
            self.logistic.update(past_bars, realized_up)
        raw_p = self._raw_pooled_p(past_bars)
        self.calibrator.add(raw_p, realized_up)
        return losses

    def _raw_pooled_p(self, bars: Sequence[OHLCVBar]) -> float:
        ws = self.weights()
        p = 0.0
        for m in self._members:
            p += ws[m.name] * float(m.fn(bars).p_up)
        return max(0.0, min(1.0, p))

    # -- bulk walk --------------------------------------------------------

    def walk(self, bars: Sequence[OHLCVBar], warmup: int = 20) -> Dict[str, float]:
        """Replay a bar sequence, updating weights online; return mean pinball
        loss per member and for the pooled ensemble (for acceptance checks)."""
        pooled_sq: List[float] = []
        member_sq: Dict[str, List[float]] = {m.name: [] for m in self._members}
        taus = [float(t) for t in QUANTILES]
        for i in range(warmup, len(bars)):
            past = bars[:i]
            cur = bars[i]
            a = float(cur.close)
            ens = self.predict(past)
            pooled_sq.append(sum(_pinball_single(t, a, float(p)) for t, p in zip(taus, ens.triple())) / len(taus))
            for m in self._members:
                f = m.fn(past)
                member_sq[m.name].append(sum(_pinball_single(t, a, float(p)) for t, p in zip(taus, f.triple())) / len(taus))
            self.observe(past, cur)
        return {
            "ensemble": (sum(pooled_sq) / len(pooled_sq)) if pooled_sq else 0.0,
            **{k: (sum(v) / len(v)) if v else 0.0 for k, v in member_sq.items()},
        }

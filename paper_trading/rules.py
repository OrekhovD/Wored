"""paper_trading.rules — bounded consumer of ``active`` strategy rules (block E.4).

This is the missing *consumer* link of D2: rules were written to ``strategy_rules``
but never read back at runtime.  At strategy start we read the latest
``status='active'`` ruleset and overlay it onto :class:`BaselineV1Config` through a
**whitelist with hard bounds** — an LLM/heuristic candidate can only ever nudge a
fixed set of indicator parameters inside fixed limits, never reach risk or sizing
(ADR-03: LLM proposes, deterministic code disposes).

The core :func:`apply_rules_to_config` is pure (no I/O) so replay and tests can
feed it any ruleset; :func:`load_active_rules` is the thin async DB adapter.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence as _SequenceABC
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, List, Optional, Tuple

from paper_trading.strategy import BaselineV1Config, BaselineV1Strategy

log = logging.getLogger(__name__)

__all__ = ["RULE_WHITELIST", "apply_rules_to_config", "build_strategy", "load_active_rules"]


# parameter -> (config field, parser, hard low, hard high).
# ``parser`` normalises a JSON scalar to Decimal/float/int/bool; anything the
# candidate did not spell out, or that is not a strategy knob (e.g. the risk-layer
# ``max_leverage``), is silently ignored rather than guessed at.
def _dec(v: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {"true", "1", "yes", "on"}:
            return True
        if s in {"false", "0", "no", "off"}:
            return False
    return None


Parser = Callable[[Any], Any]
RULE_WHITELIST: Dict[str, Tuple[str, Parser, Any, Any]] = {
    "min_net_rr": ("min_net_rr", _dec, Decimal("0.5"), Decimal("3.0")),
    "tp_rr": ("tp_rr", _dec, Decimal("1.0"), Decimal("5.0")),
    "sl_atr_fraction": ("sl_atr_fraction", _dec, Decimal("0.1"), Decimal("1.5")),
    "entry_atr_fraction": ("entry_atr_fraction", _dec, Decimal("0.05"), Decimal("1.0")),
    "cooldown_seconds": ("cooldown_seconds", _float, 60.0, 7200.0),
    "signal_ttl_seconds": ("signal_ttl_seconds", _float, 5.0, 600.0),
    "ema_fast_period": ("ema_fast_period", _int, 5, 60),
    "ema_slow_period": ("ema_slow_period", _int, 20, 240),
    "atr_period": ("atr_period", _int, 5, 60),
    "sl_lookback_bars": ("sl_lookback_bars", _int, 2, 40),
    "no_entry_last_minutes_of_day": ("no_entry_last_minutes_of_day", _int, 0, 120),
    "enable_long": ("enable_long", _bool, False, True),
    "enable_short": ("enable_short", _bool, False, True),
}


def _coerce_clamp(raw: Any, parser: Parser, lo: Any, hi: Any) -> Optional[Any]:
    val = parser(raw)
    if val is None:
        return None
    # ``enable_*`` are booleans — no numeric clamp.
    if isinstance(val, bool):
        return val
    if val < lo:
        return lo
    if val > hi:
        return hi
    return val


def apply_rules_to_config(
    base: BaselineV1Config,
    rules: Optional[Dict[str, Any]],
) -> Tuple[BaselineV1Config, List[Dict[str, Any]]]:
    """Overlay whitelisted ``rules['adjustments']`` onto a copy of *base*.

    Returns ``(config, applied)`` where ``applied`` records every change actually
    made (parameter, from, to, whether it was clamped).  Unknown parameters, a
    missing/unparseable ``new`` value, or an out-of-``[lo, hi]`` value that cannot
    be coerced are all skipped — a bad rule can never crash the strategy nor
    widen a parameter past its hard bound.
    """
    cfg = base
    applied: List[Dict[str, Any]] = []
    if not rules:
        return cfg, applied

    adjustments = rules.get("adjustments")
    if not isinstance(adjustments, _SequenceABC) or isinstance(adjustments, (str, bytes)):
        return cfg, applied

    for adj in adjustments:
        if not isinstance(adj, dict):
            continue
        name = str(adj.get("parameter", "")).strip()
        spec = RULE_WHITELIST.get(name)
        if spec is None:
            continue  # risk-layer or unknown parameter — not the strategy's to apply
        field, parser, lo, hi = spec
        value = _coerce_clamp(adj.get("new"), parser, lo, hi)
        if value is None:
            log.debug("rules: skipping adjustment %r (unparseable new value)", name)
            continue
        current = getattr(cfg, field)
        if value == current:
            continue
        cfg = replace(cfg, **{field: value})
        applied.append({
            "parameter": name,
            "field": field,
            "from": str(current),
            "to": str(value),
        })
    return cfg, applied


def build_strategy(
    rules: Optional[Dict[str, Any]],
    *,
    base_config: Optional[BaselineV1Config] = None,
) -> Tuple[BaselineV1Strategy, List[Dict[str, Any]]]:
    """Construct a :class:`BaselineV1Strategy` with active rules applied.

    Indicators are built from the *final* config inside the strategy's ``__init__``,
    so rules must be applied before construction (which this helper guarantees).
    Returns ``(strategy, applied)``.
    """
    cfg, applied = apply_rules_to_config(base_config or BaselineV1Config(), rules)
    return BaselineV1Strategy(cfg), applied


async def load_active_rules(pool: Any) -> Optional[Dict[str, Any]]:
    """Read the latest ``status='active'`` ruleset's ``rules`` JSON, if any.

    A thin, defensive DB read — failures (or no pool) degrade to ``None`` so the
    strategy always falls back to its coded defaults rather than blocking startup.
    """
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT rules FROM strategy_rules WHERE status = 'active' "
                "ORDER BY id DESC LIMIT 1"
            )
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("rules: failed to load active rules: %s", exc)
        return None
    if not row:
        return None
    raw = row["rules"]
    if isinstance(raw, str):
        import json
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None
    return raw

#!/usr/bin/env python3
"""Independent read-only verifier for the verified 24h paper experiment (TZ §4.4).

Usage:
    python scripts/verify_paper_experiment.py \\
        --experiment-id <UUID> --day-id <UUID> --strict --output-dir <existing-dir>

What this is and what it is not
------------------------------

The trading runtime may only ever write ``PENDING_VERIFICATION``.  This script is
the only component allowed to decide whether a fill's price claim is true, and it
decides by **re-deriving the fill from the immutable capture**, not by trusting any
number the runtime stored:

* the raw HTX payload is re-read from ``paper_v2_market_events``, its SHA-256 is
  recomputed, and the *bytes themselves* are parsed by this module's own
  version-dispatched parser.  The collector's parsers are deliberately never
  imported for that decision: a parser that confirms its own output proves
  nothing, which is exactly the defect this gate removes;
* every normalized column — source, channel, contract, venue time, sequence,
  version, bid/ask price and size, and both depth sides level by level **in
  published order** — is compared against what the raw payload states, so a
  normalization or storage defect fails as ``RAW_NORMALIZED_*_MISMATCH`` rather
  than passing on its own claim;
* the depth is walked by a separate Decimal implementation of the stored policy
  version, replaying what *earlier* fills already consumed, so contracts, touch
  price, used levels in order, VWAP, base quantity, notional, fee basis and
  measured slippage are recomputed from raw levels.  ``evaluate_book_fill`` is
  re-run as a **diagnostic only** and can never turn an independent FAIL into a
  PASS;
* causality is re-tested against the stored timestamps, because the runtime's
  ``quote_age_ms`` is a claim, and ``received_at - source_event_at`` is a fact.
  A receive-time-only capture replays numerically but never claims venue-time
  proof: its ``strict_pass`` stays false.

Read-only by construction: the session is forced to
``SET TRANSACTION READ ONLY`` for every query.  ``--record-verdicts`` is the single
opt-in exception, and it can only touch the verification columns — the database
itself refuses anything else (see the immutability triggers in
``migrations/20261023_verified_paper_market.sql``).

Tolerances are not negotiable, and they are asymmetric on purpose:
``MONEY_TOLERANCE`` (1e-8 USDT) applies to money after the versioned rounding
policy; contracts and price levels get **zero** tolerance, because "one contract
short" is never a rounding artefact.

Exit codes
----------
``0``  verification ran; under ``--strict`` no FAIL/UNVERIFIABLE was found.
``1``  ``--strict`` and at least one FAIL/UNVERIFIABLE (or an unreconciled ledger).
``2``  usage or environment error (bad UUID, missing output dir, DB unreachable,
       missing substrate tables) — never reported as a trading verdict.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

# Make the package importable when run as a script from any working directory.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_trading.book_execution import (  # noqa: E402
    CAPTURE_MODE_MIXED,
    CAPTURE_MODE_UNKNOWN,
    CAPTURE_MODE_WS,
    BookLevel,
    ExecutionPolicy,
    QuoteEvent,
    TradeRequest,
    capture_mode_for_source,
    consumption_key,
    evaluate_book_fill,
    quote_reference_for,
)
from paper_trading.contract_rules import (  # noqa: E402
    ContractRule,
    LIVE_SIZING_STATUSES,
    canonical_pair_hash,
)

EXIT_OK = 0
EXIT_STRICT_FAILURE = 1
EXIT_USAGE_ERROR = 2

MONEY_TOLERANCE = Decimal("0.00000001")
COUNT_TOLERANCE = Decimal(0)
#: Recomputed timings are quantized exactly like the engine does (ms, floor), so a
#: claim-vs-fact comparison needs no fuzzy tolerance.
TIME_QUANTUM = Decimal("0.001")
ROUNDING_POLICY_VERSION = "verifier_rounding_v1"
TIME_POLICY_VERSION = "ms_floor_v1__matches_engine"
REPLAY_ORDER_POLICY_VERSION = "executed_at_then_fill_id_v1"
#: The independent implementation of the price policy.  Bumped whenever the walk
#: changes, so an old report can be told apart from one produced by this code.
INDEPENDENT_WALK_VERSION = "verifier_book_v1_independent_walk"
#: Which side of the walked depth is consumed first.  Declared rather than
#: implied: ``book_v1`` sorts best price first and never trusts published order
#: for the *take*, while still requiring the published order to be best-first.
DECISION_LEVEL_ORDER = "best_price_first_v1"
VERIFIER_VERSION = "verify_paper_experiment_v2_independent_price_proof"

PASS = "PASS"
FAIL = "FAIL"
UNVERIFIABLE = "UNVERIFIABLE"
NOT_APPLICABLE = "N/A"

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


# ---------------------------------------------------------------------------
# Arithmetic primitives with the declared tolerances
# ---------------------------------------------------------------------------


def dec(value: Any) -> Decimal:
    if value is None:
        return Decimal(0)
    return value if isinstance(value, Decimal) else Decimal(str(value))


def money(value: Decimal) -> Decimal:
    """The versioned money rounding policy: 8dp half-up, then compare."""
    return dec(value).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)


def money_matches(a: Any, b: Any, tolerance: Decimal = MONEY_TOLERANCE) -> bool:
    """Compare after the rounding policy; a bare subtraction would hide drift."""
    return abs(money(dec(a)) - money(dec(b))) <= tolerance


def exact_matches(a: Any, b: Any) -> bool:
    """Zero tolerance for contracts and price levels."""
    return dec(a) == dec(b)


def is_whole(value: Any) -> bool:
    return dec(value) == dec(value).to_integral_value()


def sha256_of_payload(payload: Any) -> str:
    """Re-derive the capture digest from the payload *content*, not its storage format.

    ``raw_payload`` lives in a JSONB column, so PostgreSQL re-serialises it on the
    way in: key order, spacing and even ``1E+2``-style numbers come back different
    from the bytes the store hashed.  Hashing the returned string unchanged would
    fail every real row, so a JSON document is parsed back and canonicalised first;
    the digest then proves the content is untouched, which is the claim the TZ asks
    to re-verify.  A value that is not JSON (a legacy TEXT capture) is hashed as-is.
    """
    if isinstance(payload, (str, bytes)):
        text = payload if isinstance(payload, str) else payload.decode("utf-8")
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            return hashlib.sha256(text.encode("utf-8")).hexdigest()
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Contract-rule provenance re-derivation
# ---------------------------------------------------------------------------


def rule_provenance_matches(rule_row: Mapping[str, Any]) -> tuple[bool, str]:
    """Re-derive a rule row's provenance claim from the bytes stored with it.

    Two shapes are legitimate, and both are *recomputed* rather than trusted:

    * a **provenance document** written by the collector
      (:func:`collector.htx.book_capture.build_rule_provenance`): ``contract_spec``
      plus, when the lot step was attested, the ``trade_tape`` evidence.  The
      verifier re-hashes the spec bytes, re-hashes the tape rows, checks each inner
      claim against what the document itself states, and finally checks that the
      pair commitment equals ``source_payload_hash``;
    * a **single payload** (an archived probe capture or a legacy row), where the
      claimed hash must be the digest of the stored payload itself.

    Anything else fails closed: a placeholder payload, a document whose bytes no
    longer hash to their own digests, or a rule the runtime built and then edited.
    A row that cannot reproduce its own provenance cannot prove a fill either.

    The returned detail is a fixed phrase plus the digests involved, so a report can
    be read without re-running the hash and a test can assert the reason precisely.
    """
    claimed = str(rule_row.get("source_payload_hash") or "")
    raw: Any = rule_row.get("raw_payload")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return True, "no payload stored with the row (legacy); digest claim unchecked"
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            matched = sha256_of_payload(raw)
            return _provenance_single_payload(matched, claimed)
    if isinstance(raw, Mapping) and "contract_spec" in raw:
        matched = sha256_of_payload(raw["contract_spec"])
        stored_spec = str(raw.get("contract_spec_sha256") or "")
        if not stored_spec:
            return False, "document omits the contract_spec_sha256 it claims"
        if matched != stored_spec:
            return False, (
                "contract_spec bytes do not hash to the digest the document states "
                f"({matched[:16]}… ≠ {stored_spec[:16]}…)"
            )
        tape = raw.get("trade_tape")
        if not isinstance(tape, Mapping) or not tape.get("rows"):
            if matched == claimed:
                return True, f"spec-only document: digest matches ({matched[:16]}…)"
            return False, (
                f"spec-only document digest {matched[:16]}… ≠ source_payload_hash"
            )
        rows = [
            [
                str(row.get("amount_contracts")),
                str(row.get("quantity_base")),
                str(row.get("price_usdt")),
            ]
            for row in tape["rows"]
        ]
        tape_digest = sha256_of_payload({"rows": rows})
        if tape_digest != str(tape.get("digest") or ""):
            return False, (
                "trade_tape rows do not hash to the digest the document states "
                f"({tape_digest[:16]}… ≠ {str(tape.get('digest') or '')[:16]}…)"
            )
        pair = canonical_pair_hash(matched, tape_digest)
        if pair == claimed:
            return True, f"pair(spec,tape): commitment matches ({pair[:16]}…)"
        return False, (
            f"pair(spec,tape) {pair[:16]}… ≠ source_payload_hash: the row's rule was "
            "not produced from these spec and tape bytes"
        )
    return _provenance_single_payload(sha256_of_payload(raw), claimed)


def _provenance_single_payload(digest: str, claimed: str) -> tuple[bool, str]:
    if digest == claimed:
        return True, "single-payload digest"
    return False, (
        f"single-payload digest does not reproduce source_payload_hash "
        f"({digest[:16]}… ≠ {claimed[:16]}…)"
    )


# ---------------------------------------------------------------------------
# Verdict model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    """One named re-verification with concrete reason codes."""

    name: str
    verdict: str
    reason_codes: tuple[str, ...] = ()
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "verdict": self.verdict,
            "reason_codes": list(self.reason_codes),
            "detail": self.detail,
        }


def _check(name: str, ok: bool, fail_code: str, *, detail: str = "",
           extra: Sequence[str] = ()) -> Check:
    return Check(
        name=name,
        verdict=PASS if ok else FAIL,
        reason_codes=() if ok else (fail_code, *extra),
        detail=detail,
    )


def _unverifiable(name: str, code: str, *, detail: str = "") -> Check:
    """Missing proof is never a PASS and never a FAIL: it is UNVERIFIABLE.

    The distinction matters for the day report — an operator must be able to tell
    "the price was wrong" from "we cannot tell at all".
    """
    return Check(name=name, verdict=UNVERIFIABLE, reason_codes=(code,), detail=detail)


def _na(name: str, code: str, *, detail: str = "") -> Check:
    return Check(name=name, verdict=NOT_APPLICABLE, reason_codes=(code,), detail=detail)


def worst(checks: Iterable[Check]) -> str:
    """Aggregate a verdict, with ``N/A`` ranked *below* ``PASS``.

    "Not applicable" must never outrank a check that genuinely passed, otherwise a
    fill that reproduced completely would read as N/A merely because it has no
    closing leg — and the day would then look unverifiable when it is not.  N/A
    items are still listed in their own report section.
    """
    order = {NOT_APPLICABLE: 0, PASS: 1, UNVERIFIABLE: 2, FAIL: 3}
    verdicts = [c.verdict for c in checks]
    if not verdicts:
        return UNVERIFIABLE
    return max(verdicts, key=lambda v: order.get(v, 3))


@dataclass(frozen=True)
class FillVerdict:
    fill_id: str
    verdict: str
    checks: tuple[Check, ...]
    row: Mapping[str, Any]
    #: Grade re-derived from the stored event sources, so a report can count how
    #: many fills were socket-proven and how many were a modelled reconstruction.
    capture_mode: str = ""
    #: Both captures published a venue timestamp that the raw payload reproduces.
    #: A numeric replay on receive time alone can never claim this.
    venue_time_proven: bool = False

    def reason_codes(self) -> tuple[str, ...]:
        codes: list[str] = []
        for check in self.checks:
            if check.verdict in (FAIL, UNVERIFIABLE):
                codes.extend(check.reason_codes)
        return tuple(dict.fromkeys(codes))

    @property
    def strict_pass(self) -> bool:
        """A venue-time, socket-captured, fully reproduced price — nothing less.

        Kept separate from ``verdict`` because the two answer different questions:
        a modelled reconstruction may replay correctly (``PASS``) while never
        being strict proof of what the venue said at that instant.
        """
        return (
            self.verdict == PASS
            and self.venue_time_proven
            and self.capture_mode == CAPTURE_MODE_WS
        )


# ---------------------------------------------------------------------------
# Raw capture parsers, dispatched on the version the row declares
# ---------------------------------------------------------------------------

#: Capture versions this verifier can read, keyed by
#: ``(parser_version, source, channel)``.  The table is written out here and the
#: collector modules are never imported: a verifier that reuses the capturer's
#: code inherits the capturer's bugs, which is the one thing this gate has to
#: rule out.  Only versions whose raw shape can be *specified and tested* are
#: listed, and each entry names the shape it reads.
RAW_PARSER_VERSIONS: Mapping[tuple[str, str, str], str] = {
    ("htx_linear_swap_ws_v1", "htx_public_ws", "bbo"): "ws_bbo_tick_pair_v1",
    ("htx_linear_swap_ws_v1", "htx_public_ws", "depth"): "ws_depth_snapshot_v1",
    ("htx_linear_swap_rest_v1", "htx_public_rest", "bbo"): "rest_bbo_ticks_v1",
    ("htx_linear_swap_rest_v1", "htx_public_rest", "depth"): "rest_depth_tick_v1",
}
#: ``depth.size_20`` publishes at most twenty levels per side and both collectors
#: truncate to it, so a longer raw array was not stored as published.
RAW_DEPTH_LEVELS = 20
RAW_SOURCE_WS = "htx_public_ws"
RAW_SOURCE_REST = "htx_public_rest"
SHAPE_SOURCE = {
    "ws_bbo_tick_pair_v1": RAW_SOURCE_WS,
    "ws_depth_snapshot_v1": RAW_SOURCE_WS,
    "rest_bbo_ticks_v1": RAW_SOURCE_REST,
    "rest_depth_tick_v1": RAW_SOURCE_REST,
}


class RawPayloadError(ValueError):
    """The stored bytes cannot be read under the version the row declares.

    Carries the verdict it deserves: a payload that contradicts its own declared
    shape is a ``FAIL``, while an absent or unversioned capture is
    ``UNVERIFIABLE`` — "we cannot look" is not "the price was wrong".
    """

    def __init__(self, code: str, detail: str, verdict: str = FAIL) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.verdict = verdict


@dataclass(frozen=True)
class RawLevel:
    """One published level: price in USDT, size in contracts, nothing derived."""

    price: Decimal
    size_contracts: Decimal


@dataclass(frozen=True)
class RawCapture:
    """What the stored bytes say, before any normalized column is consulted.

    ``venue_ms`` stays ``None`` when the venue published no timestamp — notably
    REST depth.  Substituting the receive clock here would manufacture the very
    venue time the report is supposed to prove, so nothing in this module ever
    does.
    """

    shape: str
    channel: str
    contract_code: str
    bid_price: Decimal | None
    bid_size_contracts: Decimal | None
    ask_price: Decimal | None
    ask_size_contracts: Decimal | None
    bid_levels: tuple[RawLevel, ...]
    ask_levels: tuple[RawLevel, ...]
    venue_ms: int | None
    sequence_no: int | None
    version_no: int | None
    mrid: str | None

    @property
    def source(self) -> str:
        return SHAPE_SOURCE[self.shape]

    @property
    def ordered_levels(self) -> tuple[tuple[str, RawLevel], ...]:
        """Published order, side by side, exactly as the collectors emit it."""
        return (
            tuple(("bid", level) for level in self.bid_levels)
            + tuple(("ask", level) for level in self.ask_levels)
        )

    @property
    def depth_levels(self) -> tuple[RawLevel, ...]:
        return self.bid_levels + self.ask_levels


def _raw_dec(value: Any, field: str) -> Decimal:
    if value is None:
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", f"{field}: value is missing")
    try:
        result = dec(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", f"{field}: not a decimal") from exc
    if not result.is_finite():
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", f"{field}: non-finite decimal")
    return result


def _raw_pair(raw: Any, field: str) -> tuple[Decimal, Decimal]:
    """One ``[price, size_in_contracts]`` array, read without coercion tricks."""
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", f"{field}: expected [price, size]")
    price = _raw_dec(raw[0], f"{field}[0]")
    size = _raw_dec(raw[1], f"{field}[1]")
    if price <= 0:
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", f"{field}: price must be positive")
    if size <= 0:
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", f"{field}: size must be positive")
    return price, size


def _raw_int(value: Any, field: str) -> int:
    if value is None:
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", f"{field}: value is missing")
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", f"{field}: not an integer") from exc


def _raw_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _raw_mrid(tick: Mapping[str, Any]) -> str | None:
    value = tick.get("mrid")
    return None if value is None else str(value)


def _json_object(raw_payload: Any) -> Mapping[str, Any]:
    """Decode a stored JSONB capture; a TEXT column is parsed, never guessed."""
    payload = raw_payload
    if isinstance(payload, (str, bytes)):
        text = payload if isinstance(payload, str) else payload.decode("utf-8")
        try:
            payload = json.loads(text)
        except (ValueError, TypeError) as exc:
            raise RawPayloadError(
                "RAW_PAYLOAD_MALFORMED", "stored capture is not JSON"
            ) from exc
    if not isinstance(payload, Mapping):
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", "stored capture is not an object")
    return payload


def _topic_parts(topic: str) -> list[str]:
    return [part for part in str(topic or "").split(".") if part]


def _ws_topic(payload: Mapping[str, Any], channel: str) -> tuple[str, str]:
    """``(contract, topic)`` from ``ch``; the subscription is the instrument claim."""
    topic = str(payload.get("ch") or "")
    parts = _topic_parts(topic)
    suffix = ".bbo" if channel == "bbo" else ".depth.size_20"
    if len(parts) < 3 or parts[0] != "market" or not topic.endswith(suffix):
        raise RawPayloadError(
            "RAW_CHANNEL_NOT_DECLARED_BY_VERSION",
            f"ch={topic!r} is not a {channel} channel",
        )
    return parts[1].upper(), topic


def _depth_levels(rows: Any, side: str) -> tuple[RawLevel, ...]:
    """Read one published side, best first, refusing duplicates or disorder."""
    if not isinstance(rows, list) or not rows:
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", f"{side}: expected a non-empty array")
    if len(rows) > RAW_DEPTH_LEVELS:
        raise RawPayloadError(
            "RAW_PAYLOAD_MALFORMED",
            f"{side}: {len(rows)} levels published, more than the {RAW_DEPTH_LEVELS} "
            "the channel stores",
        )
    levels: list[RawLevel] = []
    for index, row in enumerate(rows):
        price, size = _raw_pair(row, f"{side}[{index}]")
        levels.append(RawLevel(price=price, size_contracts=size))
    prices = [level.price for level in levels]
    if len(set(prices)) != len(prices):
        raise RawPayloadError("RAW_LEVEL_DUPLICATE_PRICE", f"{side}: a price is published twice")
    expected = sorted(prices, reverse=(side == "bid"))
    if prices != expected:
        raise RawPayloadError(
            "RAW_LEVEL_ORDER_NOT_BEST_FIRST",
            f"{side}: published order {prices[:3]}… is not best-first",
        )
    return tuple(levels)


def _parse_ws_bbo(payload: Mapping[str, Any], shape: str) -> RawCapture:
    contract, _topic = _ws_topic(payload, "bbo")
    tick = payload.get("tick")
    if not isinstance(tick, Mapping):
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", "bbo: tick object is missing")
    bid_price, bid_size = _raw_pair(tick.get("bid"), "bbo.bid")
    ask_price, ask_size = _raw_pair(tick.get("ask"), "bbo.ask")
    if bid_price > ask_price:
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", "bbo: bid exceeds ask (crossed)")
    # A socket BBO without a venue time cannot prove *when* it was true; the
    # capturer refuses it, so a stored one was not produced by that capturer.
    venue_ms = _raw_int(tick.get("ts"), "bbo.ts")
    version = _raw_optional_int(tick.get("version"))
    sequence = _raw_optional_int(tick.get("id"))
    if sequence is None:
        sequence = version
    return RawCapture(
        shape=shape,
        channel="bbo",
        contract_code=contract,
        bid_price=bid_price,
        bid_size_contracts=bid_size,
        ask_price=ask_price,
        ask_size_contracts=ask_size,
        bid_levels=(RawLevel(bid_price, bid_size),),
        ask_levels=(RawLevel(ask_price, ask_size),),
        venue_ms=venue_ms,
        sequence_no=sequence,
        version_no=version,
        mrid=_raw_mrid(tick),
    )


def _parse_ws_depth(payload: Mapping[str, Any], shape: str) -> RawCapture:
    contract, _topic = _ws_topic(payload, "depth")
    tick = payload.get("tick")
    if not isinstance(tick, Mapping):
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", "depth: tick object is missing")
    bid_levels = _depth_levels(tick.get("bids"), "bid")
    ask_levels = _depth_levels(tick.get("asks"), "ask")
    if bid_levels[0].price > ask_levels[0].price:
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", "depth: bid exceeds ask (crossed)")
    version = _raw_optional_int(tick.get("version"))
    sequence = _raw_optional_int(tick.get("id"))
    if sequence is None:
        sequence = version
    return RawCapture(
        shape=shape,
        channel="depth",
        contract_code=contract,
        bid_price=bid_levels[0].price,
        bid_size_contracts=bid_levels[0].size_contracts,
        ask_price=ask_levels[0].price,
        ask_size_contracts=ask_levels[0].size_contracts,
        bid_levels=bid_levels,
        ask_levels=ask_levels,
        # REST depth legitimately omits ``ts``; the absence is preserved.
        venue_ms=(
            None if tick.get("ts") is None else _raw_int(tick.get("ts"), "depth.ts")
        ),
        sequence_no=sequence,
        version_no=version,
        mrid=_raw_mrid(tick),
    )


def _rest_tick(payload: Mapping[str, Any], channel: str) -> Mapping[str, Any]:
    """The single tick a REST capture documents; several is not an answer."""
    if payload.get("status") != "ok":
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", f"{channel}: status is not ok")
    if channel == "bbo":
        ticks = payload.get("ticks")
        if not isinstance(ticks, list) or not ticks:
            raise RawPayloadError("RAW_PAYLOAD_MALFORMED", "bbo: ticks array is missing")
        rows = [row for row in ticks if isinstance(row, Mapping)]
        if not rows:
            raise RawPayloadError("RAW_PAYLOAD_MALFORMED", "bbo: no tick objects")
        if len(rows) > 1:
            raise RawPayloadError(
                "RAW_CONTRACT_AMBIGUOUS",
                f"bbo: {len(rows)} contracts in one capture, so the priced one is "
                "not identifiable from the bytes",
                UNVERIFIABLE,
            )
        return rows[0]
    tick = payload.get("tick")
    if not isinstance(tick, Mapping):
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", "depth: tick object is missing")
    return tick


def _rest_contract(payload: Mapping[str, Any], tick: Mapping[str, Any]) -> str:
    code = str(tick.get("contract_code") or "").upper()
    if code:
        return code
    parts = _topic_parts(str(payload.get("ch") or ""))
    if len(parts) >= 3 and parts[0] == "market":
        return parts[1].upper()
    raise RawPayloadError(
        "RAW_CONTRACT_UNRECORDED",
        "the payload names no contract, so the instrument cannot be proved",
        UNVERIFIABLE,
    )


def _parse_rest_bbo(payload: Mapping[str, Any], shape: str) -> RawCapture:
    tick = _rest_tick(payload, "bbo")
    contract = _rest_contract(payload, tick)
    bid_price, bid_size = _raw_pair(tick.get("bid"), "bbo.bid")
    ask_price, ask_size = _raw_pair(tick.get("ask"), "bbo.ask")
    if bid_price > ask_price:
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", "bbo: bid exceeds ask (crossed)")
    venue_ms = _raw_int(tick.get("ts"), "bbo.ts")
    sequence = _raw_optional_int(tick.get("mrid"))
    return RawCapture(
        shape=shape,
        channel="bbo",
        contract_code=contract,
        bid_price=bid_price,
        bid_size_contracts=bid_size,
        ask_price=ask_price,
        ask_size_contracts=ask_size,
        bid_levels=(RawLevel(bid_price, bid_size),),
        ask_levels=(RawLevel(ask_price, ask_size),),
        venue_ms=venue_ms,
        sequence_no=sequence,
        version_no=_raw_optional_int(tick.get("version")),
        mrid=_raw_mrid(tick),
    )


def _parse_rest_depth(payload: Mapping[str, Any], shape: str) -> RawCapture:
    tick = _rest_tick(payload, "depth")
    contract = _rest_contract(payload, tick)
    bid_levels = _depth_levels(tick.get("bids"), "bid")
    ask_levels = _depth_levels(tick.get("asks"), "ask")
    if bid_levels[0].price > ask_levels[0].price:
        raise RawPayloadError("RAW_PAYLOAD_MALFORMED", "depth: bid exceeds ask (crossed)")
    sequence = _raw_optional_int(tick.get("mrid"))
    return RawCapture(
        shape=shape,
        channel="depth",
        contract_code=contract,
        bid_price=bid_levels[0].price,
        bid_size_contracts=bid_levels[0].size_contracts,
        ask_price=ask_levels[0].price,
        ask_size_contracts=ask_levels[0].size_contracts,
        bid_levels=bid_levels,
        ask_levels=ask_levels,
        venue_ms=(
            None if tick.get("ts") is None else _raw_int(tick.get("ts"), "depth.ts")
        ),
        sequence_no=sequence,
        version_no=_raw_optional_int(tick.get("version")),
        mrid=_raw_mrid(tick),
    )


_RAW_PARSERS: Mapping[str, Callable[[Mapping[str, Any], str], RawCapture]] = {
    "ws_bbo_tick_pair_v1": _parse_ws_bbo,
    "ws_depth_snapshot_v1": _parse_ws_depth,
    "rest_bbo_ticks_v1": _parse_rest_bbo,
    "rest_depth_tick_v1": _parse_rest_depth,
}


def read_raw_capture(
    row: Mapping[str, Any], label: str
) -> tuple[RawCapture | None, Check]:
    """Parse one stored capture under its own declared version.

    Fail-closed on every axis: an unknown version, a known version pointing at a
    transport it never ran on, a missing payload and a malformed payload each
    produce their own named reason code instead of a silent ``None``.
    """
    name = f"raw_parse_{label}"
    version = str(row.get("parser_version") or "")
    source = str(row.get("source") or "")
    channel = str(row.get("channel") or "")
    shape = RAW_PARSER_VERSIONS.get((version, source, channel))
    if shape is None:
        known_versions = sorted({entry[0] for entry in RAW_PARSER_VERSIONS})
        if version in known_versions:
            declared = sorted(
                f"{entry[1]}/{entry[2]}"
                for entry in RAW_PARSER_VERSIONS
                if entry[0] == version
            )
            return None, _check(
                name,
                False,
                "RAW_PARSER_VERSION_AMBIGUOUS",
                detail=(
                    f"{version} was only declared for {declared}, but the row says "
                    f"source={source or '-'} channel={channel or '-'}"
                ),
            )
        return None, _unverifiable(
            name,
            "RAW_PARSER_VERSION_UNKNOWN",
            detail=(
                f"parser_version={version or '-'} source={source or '-'} "
                f"channel={channel or '-'}; readable versions={known_versions}"
            ),
        )
    payload = row.get("raw_payload")
    if payload is None or payload == "" or payload == {}:
        return None, _unverifiable(
            name,
            "RAW_PAYLOAD_MISSING",
            detail=f"{version} row stores no capture bytes to parse",
        )
    try:
        raw = _RAW_PARSERS[shape](_json_object(payload), shape)
    except RawPayloadError as exc:
        return None, Check(name=name, verdict=exc.verdict, reason_codes=(exc.code,),
                           detail=exc.detail)
    return raw, _check(
        name,
        True,
        "",
        detail=f"{shape}: contract={raw.contract_code} levels="
               f"{len(raw.bid_levels)}/{len(raw.ask_levels)} venue_ms={raw.venue_ms}",
    )


def _ms_to_utc(milliseconds: int) -> datetime:
    """Exact venue millisecond → UTC, without a float division."""
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=int(milliseconds))


def _row_levels(row: Mapping[str, Any]) -> tuple[tuple[str, Decimal, Decimal], ...]:
    """The normalized level column as an ordered sequence of claims."""
    raw_levels = row.get("levels")
    if isinstance(raw_levels, (str, bytes)):
        decoded = json.loads(raw_levels) if raw_levels else []
    else:
        decoded = list(raw_levels or [])
    levels: list[tuple[str, Decimal, Decimal]] = []
    for item in decoded:
        if not isinstance(item, Mapping):
            raise ValueError("stored level is not an object")
        levels.append(
            (str(item.get("side")), _raw_dec(item.get("price"), "levels.price"),
             _raw_dec(item.get("size_contracts"), "levels.size"))
        )
    return tuple(levels)


def raw_normalized_checks(
    label: str, raw: RawCapture, row: Mapping[str, Any]
) -> list[Check]:
    """Compare every normalized claim with the bytes, one claim at a time.

    A single ``raw_hashes`` check would prove the bytes are the bytes; it says
    nothing about whether the columns the engine priced from say the same thing.
    So source, channel, contract, venue time, sequence, best quotes and the
    ordered levels are each re-derived here.
    """
    checks: list[Check] = []
    checks.append(
        _check(
            f"raw_source_{label}",
            str(row.get("source") or "") == raw.source,
            "RAW_SOURCE_NOT_DECLARED_BY_VERSION",
            detail=f"row={row.get('source')} version={raw.shape} says {raw.source}",
        )
    )
    checks.append(
        _check(
            f"raw_channel_{label}",
            str(row.get("channel") or "") == raw.channel,
            "RAW_CHANNEL_NOT_DECLARED_BY_VERSION",
            detail=f"row={row.get('channel')} payload={raw.channel}",
        )
    )
    checks.append(
        _check(
            f"raw_contract_{label}",
            str(row.get("contract_code") or "").upper() == raw.contract_code.upper(),
            "RAW_CONTRACT_MISMATCH",
            detail=f"row={row.get('contract_code')} payload={raw.contract_code}",
        )
    )

    # Venue time is a separate claim from the receive time, and its absence must
    # stay absent: a normalized timestamp the payload does not contain was made up.
    row_venue = row.get("source_event_at")
    if raw.venue_ms is None:
        if row_venue is None:
            checks.append(
                _na(
                    f"raw_venue_time_{label}",
                    "VENUE_TIME_ABSENT_IN_RAW_AND_ROW__RECEIVE_TIME_ONLY",
                    detail="numeric replay is possible, venue-time proof is not",
                )
            )
        else:
            checks.append(
                _check(
                    f"raw_venue_time_{label}",
                    False,
                    "VENUE_TIME_INVENTED_FOR_RAW_PAYLOAD",
                    detail=f"row says {_iso(row_venue)}, payload publishes none",
                )
            )
    elif row_venue is None:
        checks.append(
            _check(
                f"raw_venue_time_{label}",
                False,
                "RAW_VENUE_TIME_NOT_STORED",
                detail=f"payload publishes ts={raw.venue_ms} ms, row has no venue time",
            )
        )
    else:
        stored = _utc(row_venue) if isinstance(row_venue, datetime) else None
        matches = (
            stored is not None
            and int(stored.timestamp() * 1000) == raw.venue_ms
        )
        checks.append(
            _check(
                f"raw_venue_time_{label}",
                matches,
                "RAW_VENUE_TIME_MISMATCH",
                detail=f"row={_iso(row_venue)} payload_ts_ms={raw.venue_ms}",
            )
        )
        if row.get("source_event_at_ms") is not None:
            checks.append(
                _check(
                    f"raw_venue_time_ms_{label}",
                    int(row["source_event_at_ms"]) == raw.venue_ms,
                    "RAW_VENUE_TIME_MISMATCH",
                    detail=f"row_ms={row['source_event_at_ms']} payload_ts_ms={raw.venue_ms}",
                )
            )

    for field, value in (
        ("sequence", raw.sequence_no),
        ("version", raw.version_no),
    ):
        stored_value = row.get(f"{field}_no")
        if value is None:
            checks.append(
                _na(
                    f"raw_{field}_{label}",
                    f"{field.upper()}_ABSENT_IN_RAW_PAYLOAD",
                    detail="ordering cannot be proved from this payload alone",
                )
                if stored_value is None
                else _check(
                    f"raw_{field}_{label}",
                    False,
                    f"{field.upper()}_INVENTED_FOR_RAW_PAYLOAD",
                    detail=f"row={stored_value}, payload publishes none",
                )
            )
        else:
            checks.append(
                _check(
                    f"raw_{field}_{label}",
                    stored_value is not None and int(stored_value) == value,
                    f"RAW_{field.upper()}_MISMATCH",
                    detail=f"row={stored_value} payload={value}",
                )
            )

    # Best quotes: every one of the six normalized numbers is re-derived.
    mismatched: list[str] = []
    for field, expected in (
        ("bid_price", raw.bid_price),
        ("bid_size_contracts", raw.bid_size_contracts),
        ("ask_price", raw.ask_price),
        ("ask_size_contracts", raw.ask_size_contracts),
    ):
        stored_value = row.get(field)
        if expected is None:
            if stored_value is not None:
                mismatched.append(f"{field}: row={stored_value} payload=none")
            continue
        if stored_value is None or not money_matches(stored_value, expected):
            mismatched.append(f"{field}: row={stored_value} payload={expected}")
    checks.append(
        _check(
            f"raw_bbo_values_{label}",
            not mismatched,
            "RAW_NORMALIZED_BBO_MISMATCH",
            detail=";".join(mismatched),
        )
    )

    # Ordered levels, on both sides.  The engine sorts, so the *order* is not
    # decisive for the take — but a row whose order disagrees with the payload is
    # not the payload, and the take must come from the payload.
    published = tuple(
        (side, level.price, level.size_contracts) for side, level in raw.ordered_levels
    )
    try:
        normalized = _row_levels(row)
    except (ValueError, RawPayloadError, json.JSONDecodeError) as exc:
        checks.append(
            _check(
                f"raw_levels_{label}",
                False,
                "RAW_NORMALIZED_LEVELS_MALFORMED",
                detail=str(exc),
            )
        )
        return checks
    if len(normalized) != len(published):
        same = False
        detail = f"rows={len(normalized)} payload={len(published)} levels"
    else:
        same = all(
            want_side == have_side
            and exact_matches(want_price, have_price)
            and exact_matches(want_size, have_size)
            for (want_side, want_price, want_size), (have_side, have_price, have_size)
            in zip(published, normalized)
        )
        detail = "" if same else f"published={_raw_levels_text(published)} row={_raw_levels_text(normalized)}"
    checks.append(
        _check(
            f"raw_levels_{label}",
            same,
            "RAW_NORMALIZED_DEPTH_MISMATCH",
            detail=detail,
        )
    )
    return checks


def _raw_levels_text(levels: Sequence[tuple[str, Decimal, Decimal]]) -> str:
    return ";".join(f"{side}@{price}x{size}" for side, price, size in levels)


def _event_from_capture(row: Mapping[str, Any], raw: RawCapture) -> QuoteEvent:
    """The captured event as the *payload* describes it, on the row's local clock.

    Prices, sizes, level order and venue time come from the bytes; only the local
    receive time and the storage labels come from the row, because those are the
    facts the bytes cannot contain.
    """
    return QuoteEvent(
        quote_event_id=str(row["quote_event_id"]),
        source=raw.source,
        channel=raw.channel,
        contract_code=raw.contract_code,
        received_at=row["received_at"],
        source_event_at=(
            None if raw.venue_ms is None else _ms_to_utc(raw.venue_ms)
        ),
        source_time_reason=row.get("source_time_reason"),
        connection_epoch=row.get("connection_epoch"),
        sequence_no=raw.sequence_no,
        version_no=raw.version_no,
        mrid=raw.mrid,
        bid_price=raw.bid_price,
        bid_size_contracts=raw.bid_size_contracts,
        ask_price=raw.ask_price,
        ask_size_contracts=raw.ask_size_contracts,
        levels=tuple(
            BookLevel(side=side, price=level.price, size_contracts=level.size_contracts)
            for side, level in raw.ordered_levels
        ),
        raw_payload_sha256=str(row["raw_payload_sha256"]),
        parser_version=str(row.get("parser_version", "unknown")),
        quality_status=str(row.get("quality_status", "captured")),
    )


# ---------------------------------------------------------------------------
# Row reconstruction (pure, so tests can feed plain dicts)
# ---------------------------------------------------------------------------


def level_from_json(item: Mapping[str, Any]) -> BookLevel:
    return BookLevel(
        side=str(item["side"]),
        price=dec(item["price"]),
        size_contracts=dec(item["size_contracts"]),
    )


def event_from_row(row: Mapping[str, Any]) -> QuoteEvent:
    """Rebuild the captured event from its stored row, levels included.

    Deliberately uses the *stored* payload-derived fields and nothing the runtime
    claimed about the fill.
    """
    raw_levels = row.get("levels")
    if isinstance(raw_levels, str):
        raw_levels = json.loads(raw_levels)
    levels = tuple(level_from_json(item) for item in (raw_levels or []))
    return QuoteEvent(
        quote_event_id=str(row["quote_event_id"]),
        source=str(row["source"]),
        channel=str(row["channel"]),
        contract_code=str(row["contract_code"]),
        received_at=row["received_at"],
        source_event_at=row.get("source_event_at"),
        source_time_reason=row.get("source_time_reason"),
        connection_epoch=row.get("connection_epoch"),
        sequence_no=row.get("sequence_no"),
        version_no=row.get("version_no"),
        mrid=row.get("mrid"),
        bid_price=_opt_dec(row.get("bid_price")),
        bid_size_contracts=_opt_dec(row.get("bid_size_contracts")),
        ask_price=_opt_dec(row.get("ask_price")),
        ask_size_contracts=_opt_dec(row.get("ask_size_contracts")),
        levels=levels,
        raw_payload_sha256=str(row["raw_payload_sha256"]),
        parser_version=str(row.get("parser_version", "unknown")),
        quality_status=str(row.get("quality_status", "captured")),
    )


def _opt_dec(value: Any) -> Decimal | None:
    return None if value is None else dec(value)


def policy_from_snapshot(snapshot: Mapping[str, Any], version: str) -> ExecutionPolicy:
    """Rebuild the policy that was in force, from the stored snapshot.

    Using today's defaults instead would verify the past against a policy that was
    never applied, which is how a verifier silently passes bad data.
    """
    return ExecutionPolicy(
        version=version,
        latency_ms=int(snapshot.get("latency_ms", 250)),
        max_wait_ms=int(snapshot.get("max_wait_ms", 2000)),
        quote_age_max_ms=int(snapshot.get("quote_age_max_ms", 1000)),
        depth_skew_max_ms=int(snapshot.get("depth_skew_max_ms", 500)),
        participation_cap=dec(snapshot.get("participation_cap", "0.1")),
        slippage_mode=str(snapshot.get("slippage_mode", "depth_vwap")),
        modelled_slippage_bps=int(snapshot.get("modelled_slippage_bps", 0)),
        rounding_policy=str(snapshot.get("rounding_policy", "contracts_floor_v1")),
        require_tick_aligned_levels=bool(snapshot.get("require_tick_aligned_levels", True)),
        allow_receive_time_ordering=bool(snapshot.get("allow_receive_time_ordering", True)),
        # Default ``False`` for rows recorded before capture provenance existed:
        # a historical run is not retroactively declared strict.
        require_ws_capture=bool(snapshot.get("require_ws_capture", False)),
    )


def rule_from_row(row: Mapping[str, Any]) -> ContractRule:
    return ContractRule.from_dict(
        {
            "venue": row["venue"],
            "market": row["market"],
            "contract_code": row["contract_code"],
            "contract_size_btc_per_contract": str(row["contract_size_btc_per_contract"]),
            "price_tick_usdt": str(row["price_tick_usdt"]),
            "order_qty_unit": row["order_qty_unit"],
            "order_qty_step_contracts": str(row["order_qty_step_contracts"]),
            "order_qty_min_contracts": str(row["order_qty_min_contracts"]),
            "book_qty_unit": row["book_qty_unit"],
            "effective_at": _iso(row.get("effective_at")),
            "observed_at": _iso(row["observed_at"]),
            "source_uri": row["source_uri"],
            "source_payload_hash": row["source_payload_hash"],
            "validation_status": row["validation_status"],
            "rule_version": row["rule_version"],
        }
    )


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _utc(value: datetime) -> datetime:
    """Normalise to aware UTC so a naive stored timestamp cannot skew a delta."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _ms(delta: timedelta) -> Decimal:
    """Signed milliseconds from a timedelta, exact and floored at 0.001 ms.

    Built from whole microseconds rather than a float division: the engine records
    the same way, so a claim-vs-fact comparison has no fuzzy edge to hide behind.
    """
    micros = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    return (Decimal(micros) / 1000).quantize(TIME_QUANTUM, rounding=ROUND_DOWN)


# ---------------------------------------------------------------------------
# The independent price walk (Decimal, no executor helper)
# ---------------------------------------------------------------------------

#: Evidence versions this verifier can price, and the inputs each version is able
#: to represent.  ``book_v1`` — the version every stored row was written under —
#: has no IOC-limit column, so the limit is N/A *by version*; a version that does
#: claim a limit must store it.  Assuming market semantics instead would be the
#: silent repair this gate forbids.
EVIDENCE_POLICY_VERSIONS: Mapping[str, tuple[str, ...]] = {
    "book_v1": (),
    "book_v2_ioc_limit_v1": ("ioc_limit_price",),
}

_ZERO = Decimal(0)
#: One basis point, as the fraction the engine divides by.
_BPS = Decimal("0.0001")


@dataclass(frozen=True)
class IndependentWalk:
    """The price this verifier computes on its own, plus what it needed to do it.

    ``status`` is ``filled`` / ``unfilled`` / ``unsupported``: the last one means
    the stored policy version cannot be priced here at all, which is
    ``UNVERIFIABLE`` rather than a mismatch.
    """

    status: str
    filled_contracts: Decimal = _ZERO
    fill_vwap: Decimal | None = None
    touch_price: Decimal | None = None
    levels_used: tuple[BookLevel, ...] = ()
    base_qty_btc: Decimal | None = None
    notional_usdt: Decimal | None = None
    slippage_bps: Decimal | None = None
    allowed_contracts: Decimal = _ZERO
    visible_contracts: Decimal = _ZERO
    cap_contracts: Decimal = _ZERO
    double_spent: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    detail: str = ""


def evidence_policy_checks(ev: Mapping[str, Any], *, side: str) -> list[Check]:
    """Re-read the evidence policy version, and the limit it may or may not carry."""
    version = str(ev.get("model_version") or "")
    if version not in EVIDENCE_POLICY_VERSIONS:
        return [
            _unverifiable(
                "evidence_policy_version",
                "EVIDENCE_POLICY_VERSION_UNKNOWN",
                detail=(
                    f"model_version={version or '-'} cannot be priced by this verifier; "
                    f"readable versions={sorted(EVIDENCE_POLICY_VERSIONS)}"
                ),
            )
        ]
    checks = [
        _check(
            "evidence_policy_version",
            True,
            "",
            detail=f"model_version={version}",
        )
    ]
    required = EVIDENCE_POLICY_VERSIONS[version]
    missing = [name for name in required if ev.get(name) is None]
    if missing:
        checks.append(
            _unverifiable(
                "ioc_limit",
                "EVIDENCE_POLICY_INPUT_MISSING",
                detail=f"{version} requires {missing}; market semantics are not assumed",
            )
        )
    elif "ioc_limit_price" in required:
        limit = dec(ev["ioc_limit_price"])
        vwap = dec(ev["fill_vwap"])
        breached = (side == "buy" and vwap > limit) or (side == "sell" and vwap < limit)
        checks.append(
            _check(
                "ioc_limit",
                not breached,
                "IOC_LIMIT_BREACHED",
                detail=f"limit={limit} vwap={vwap} side={side}",
            )
        )
    else:
        checks.append(
            _na(
                "ioc_limit",
                "IOC_LIMIT_NOT_IN_EVIDENCE_VERSION",
                detail=(
                    f"{version} stores no IOC limit, so a limit is neither assumed nor "
                    "proven by this version"
                ),
            )
        )
    return checks


def independent_depth_walk(
    *,
    levels: Sequence[RawLevel],
    side: str,
    requested_contracts: Decimal,
    prior_consumption: Mapping[tuple[str, str, str], Decimal],
    ledger_id: str,
    policy: ExecutionPolicy,
    rule: ContractRule,
) -> IndependentWalk:
    """Walk the *raw* depth for one IOC order and price it from first principles.

    This is deliberately a second implementation of the ``book_v1`` arithmetic:
    lot floor, visible size less what earlier fills already took from the same
    quote, participation cap on the visible total, whole-contract takes, VWAP over
    the levels actually used, base quantity via the contract size, notional, and
    measured adverse slippage from the touch.  ``evaluate_book_fill`` is never
    called, so an engine mistake cannot launder itself into a PASS.

    ``ledger_id`` is the quote reference of the depth row and ``prior_consumption``
    the ledger keyed by it; both are identities of the stored capture, and the raw
    checks have already tied that capture back to its bytes.
    """
    if policy.slippage_mode != "depth_vwap":
        return IndependentWalk(
            status="unsupported",
            reason_codes=("SLIPPAGE_MODE_NOT_INDEPENDENTLY_SPECIFIED",),
            detail=(
                f"slippage_mode={policy.slippage_mode}: a modelled assumption cannot be "
                "reproduced from depth"
            ),
        )
    if policy.rounding_policy != "contracts_floor_v1":
        return IndependentWalk(
            status="unsupported",
            reason_codes=("ROUNDING_POLICY_NOT_INDEPENDENTLY_SPECIFIED",),
            detail=f"rounding_policy={policy.rounding_policy}",
        )

    step = dec(rule.order_qty_step_contracts)
    minimum = dec(rule.order_qty_min_contracts)
    if step <= 0:
        return IndependentWalk(
            status="unsupported",
            reason_codes=("CONTRACT_STEP_NOT_PROVEN",),
            detail=f"order_qty_step_contracts={rule.order_qty_step_contracts}",
        )
    units = (dec(requested_contracts) / step).to_integral_value(rounding=ROUND_DOWN)
    allowed = units * step
    if allowed < minimum:
        return IndependentWalk(
            status="unfilled",
            allowed_contracts=allowed,
            reason_codes=("REQUEST_BELOW_CONTRACT_MINIMUM",),
            detail=f"requested={requested_contracts} floors to {allowed}, minimum={minimum}",
        )

    wanted = "ask" if side == "buy" else "bid"
    book = sorted(levels, key=lambda level: level.price, reverse=(wanted == "bid"))
    if not book:
        return IndependentWalk(
            status="unfilled",
            allowed_contracts=allowed,
            reason_codes=("REJECTED_DEPTH_MISSING",),
            detail=f"the raw depth publishes no {wanted} level",
        )
    touch = book[0].price
    visible = sum((level.size_contracts for level in book), _ZERO)
    cap = (visible * dec(policy.participation_cap)).to_integral_value(rounding=ROUND_DOWN)

    remaining_request = allowed
    remaining_cap = cap
    used: list[BookLevel] = []
    notional = _ZERO
    double_spent: list[str] = []
    for level in book:
        if remaining_request <= 0 or remaining_cap <= 0:
            break
        if policy.require_tick_aligned_levels and not _is_on_tick(
            level.price, rule.price_tick_usdt
        ):
            return IndependentWalk(
                status="unfilled",
                allowed_contracts=allowed,
                touch_price=touch,
                visible_contracts=visible,
                cap_contracts=cap,
                reason_codes=("LEVEL_OFF_TICK",),
                detail=f"raw level {wanted}@{level.price} is not on tick "
                       f"{rule.price_tick_usdt}",
            )
        taken_before = dec(
            prior_consumption.get(consumption_key(ledger_id, side, level.price), _ZERO)
        )
        if taken_before > level.size_contracts:
            double_spent.append(
                f"{wanted}@{level.price}: prior={taken_before} raw={level.size_contracts}"
            )
        available = level.size_contracts - taken_before
        if available <= 0:
            continue
        take = min(available, remaining_request, remaining_cap).to_integral_value(
            rounding=ROUND_DOWN
        )
        if take <= 0:
            continue
        used.append(BookLevel(side=wanted, price=level.price, size_contracts=take))
        notional += level.price * take
        remaining_request -= take
        remaining_cap -= take

    filled = sum((level.size_contracts for level in used), _ZERO)
    vwap = None if filled <= 0 else notional / filled
    slip = _ZERO
    if vwap is not None and touch > 0:
        deviation = (vwap - touch) / touch / _BPS
        if side == "sell":
            deviation = -deviation
        slip = deviation if deviation > 0 else _ZERO
    base_qty = None if vwap is None else filled * dec(rule.contract_size_btc_per_contract)
    return IndependentWalk(
        status="filled" if filled > 0 else "unfilled",
        filled_contracts=filled,
        fill_vwap=vwap,
        touch_price=touch,
        levels_used=tuple(used),
        base_qty_btc=base_qty,
        notional_usdt=None if vwap is None else base_qty * vwap,
        slippage_bps=None if vwap is None else slip,
        allowed_contracts=allowed,
        visible_contracts=visible,
        cap_contracts=cap,
        double_spent=tuple(double_spent),
        detail=(
            f"{INDEPENDENT_WALK_VERSION}: side={side} order={DECISION_LEVEL_ORDER} "
            f"levels={len(book)} visible={visible} cap={cap} took={filled}"
        ),
    )


# ---------------------------------------------------------------------------
# The per-fill verification
# ---------------------------------------------------------------------------

#: Columns the per-fill verification cannot work without.  A legacy row missing
#: any of them is *unverifiable*, never a crash and never a PASS.
_REQUIRED_EVIDENCE_FIELDS = (
    "quote_event_id", "depth_event_id", "contract_rule_id", "side", "open_or_close",
    "order_id", "requested_contracts", "contract_size_btc", "filled_contracts",
    "base_qty_btc", "fill_vwap", "touch_price", "slippage_bps", "fee_rate",
    "fee_amount", "decision_at", "submitted_at", "eligible_at", "executed_at",
    "raw_event_sha256",
)


def verify_fill(
    *,
    fill: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
    events: Mapping[str, Mapping[str, Any]],
    rules: Mapping[str, Mapping[str, Any]],
    prior_consumption: Mapping[Any, Decimal],
    policy: ExecutionPolicy,
    gaps: Sequence[Mapping[str, Any]] = (),
) -> FillVerdict:
    """Re-derive one fill from the capture and compare it with what was recorded."""
    fill_id = str(fill["fill_id"])
    checks: list[Check] = []

    if evidence is None:
        checks.append(
            _unverifiable(
                "evidence_exists",
                "NO_EVIDENCE_ROW",
                detail="this fill has no paper_v2_execution_evidence row",
            )
        )
        return FillVerdict(fill_id, UNVERIFIABLE, tuple(checks), fill)

    ev = evidence
    if str(ev.get("verification_status", "")).upper() == "LEGACY_UNVERIFIED":
        checks.append(
            _unverifiable("evidence_exists", "LEGACY_UNVERIFIED",
                          detail="row predates the capture substrate")
        )
        return FillVerdict(fill_id, UNVERIFIABLE, tuple(checks), fill)

    missing_fields = sorted(name for name in _REQUIRED_EVIDENCE_FIELDS if ev.get(name) is None)
    if missing_fields:
        checks.append(
            _unverifiable(
                "evidence_complete",
                "EVIDENCE_ROW_INCOMPLETE",
                detail="no value for: " + ",".join(missing_fields),
            )
        )
        return FillVerdict(fill_id, UNVERIFIABLE, tuple(checks), fill)

    # 1. The capture itself must still hash to what was stored.
    quote_row = events.get(str(ev.get("quote_event_id")))
    depth_row = events.get(str(ev.get("depth_event_id")))
    if quote_row is None or depth_row is None:
        checks.append(
            _unverifiable(
                "raw_quote_present",
                "EVIDENCE_POINTS_AT_MISSING_QUOTE",
                detail=(
                    f"bbo={'missing' if quote_row is None else 'ok'}, "
                    f"depth={'missing' if depth_row is None else 'ok'}"
                ),
            )
        )
        return FillVerdict(fill_id, UNVERIFIABLE, tuple(checks), fill)

    for label, row in (("bbo", quote_row), ("depth", depth_row)):
        payload = row.get("raw_payload")
        if payload is None or payload == "" or payload == {}:
            # Hashing nothing proves nothing: an absent capture is *missing* proof,
            # not a tampered one.  ``read_raw_capture`` names the absence again.
            checks.append(
                _unverifiable(
                    f"raw_hash_{label}",
                    "RAW_PAYLOAD_MISSING",
                    detail=f"{label}: the row stores no capture bytes to hash",
                )
            )
            continue
        stored_hash = str(row["raw_payload_sha256"])
        recomputed = sha256_of_payload(payload)
        checks.append(
            _check(
                f"raw_hash_{label}",
                recomputed == stored_hash,
                "RAW_HASH_MISMATCH",
                detail=f"stored={stored_hash[:12]}… recomputed={recomputed[:12]}…",
            )
        )
    checks.append(
        _check(
            "raw_hash_evidence",
            _hash_covers_pair(ev, quote_row, depth_row),
            "EVIDENCE_HASH_NOT_A_CAPTURE_HASH",
            detail="evidence must reference a stored event hash, not a derived label",
        )
    )

    # 1a. Capture provenance, re-derived from the stored sources.  The grade is
    # never read from the fill's own claim: a capturer that labels a polled REST
    # snapshot as verified must not be able to promote itself here.  Graded
    # before the bytes are parsed, so an unreadable capture still reports which
    # transport the runtime claimed instead of hiding it behind the parse error.
    bbo_source = str(quote_row.get("source") or "")
    depth_source = str(depth_row.get("source") or "")
    bbo_mode = capture_mode_for_source(bbo_source)
    depth_mode = capture_mode_for_source(depth_source)
    capture_mode = CAPTURE_MODE_MIXED if bbo_mode != depth_mode else bbo_mode
    checks.append(
        _check(
            "capture_sources_agree",
            bbo_mode == depth_mode and capture_mode != CAPTURE_MODE_UNKNOWN,
            "CAPTURE_SOURCE_UNPROVEN",
            detail=f"bbo.source={bbo_source or '-'} depth.source={depth_source or '-'}",
        )
    )
    if capture_mode == CAPTURE_MODE_WS:
        checks.append(
            _check(
                "capture_grade",
                True,
                "CAPTURE_SOURCE_UNPROVEN",
                detail="both events arrived over the venue socket",
            )
        )
    elif policy.require_ws_capture:
        # The day's own policy demanded socket evidence, so a modelled price is a
        # FAIL and not a softer grade: admitting it would rewrite the rule after
        # the fact.
        checks.append(
            _check(
                "capture_grade",
                False,
                "REST_MODELLED_IN_STRICT_RUN",
                detail=f"strict run priced from {capture_mode}",
            )
        )
    else:
        checks.append(
            _na(
                "capture_grade",
                "REST_MODELLED",
                detail=f"{capture_mode}: modelled reconstruction, not socket evidence",
            )
        )

    # 1b. Read the stored bytes with this verifier's own parser, then compare
    #     every normalized claim with what they say.  A hash proves the bytes are
    #     the bytes; it says nothing about the columns the engine priced from.
    raw_captures: dict[str, RawCapture | None] = {}
    for label, row in (("bbo", quote_row), ("depth", depth_row)):
        raw, raw_check = read_raw_capture(row, label)
        checks.append(raw_check)
        raw_captures[label] = raw
        if raw is not None:
            checks.extend(raw_normalized_checks(label, raw, row))
    raw_bbo = raw_captures["bbo"]
    raw_depth = raw_captures["depth"]
    if raw_bbo is None or raw_depth is None:
        # Nothing can be priced from bytes that could not be read, and a numeric
        # replay of normalized columns is exactly what this gate replaced.
        checks.append(
            _unverifiable(
                "independent_price_proof",
                "RAW_CAPTURE_NOT_READABLE",
                detail="at least one capture cannot be parsed, so no independent "
                       "price is possible",
            )
        )
        return FillVerdict(fill_id, worst(checks), tuple(checks), fill, capture_mode)

    # 2. Instrument and side must agree across fill / evidence / capture.
    instrument = str(fill.get("instrument", ""))
    checks.append(
        _check(
            "instrument",
            instrument == str(quote_row["contract_code"]) == str(depth_row["contract_code"])
            == str(ev.get("contract_code", instrument) or instrument),
            "INSTRUMENT_MISMATCH",
            detail=f"fill={instrument} quote={quote_row['contract_code']}",
        )
    )
    checks.append(
        _check(
            "order_side",
            str(fill["side"]) == str(ev["side"])
            and str(ev["open_or_close"]) == ("close" if fill.get("is_close") else "open"),
            "SIDE_OR_INTENT_MISMATCH",
            detail=f"fill.side={fill['side']} evidence.side={ev['side']} "
                   f"is_close={fill.get('is_close')}",
        )
    )

    # 3. Contract rules: units, lot step, tick, provenance.
    rule_row = rules.get(str(ev.get("contract_rule_id")))
    if rule_row is None:
        checks.append(_unverifiable("contract_rule", "NO_CONTRACT_RULE_ROW"))
        return FillVerdict(fill_id, UNVERIFIABLE, tuple(checks), fill)
    rule = rule_from_row(rule_row)
    rule_status_proven = rule_row["validation_status"] in LIVE_SIZING_STATUSES
    rule_hash_matches, provenance_detail = rule_provenance_matches(rule_row)
    checks.append(
        _check(
            "contract_rule_provenance",
            rule_status_proven and rule_hash_matches,
            "CONTRACT_RULE_NOT_PROVEN",
            detail=f"status={rule_row['validation_status']} "
                   f"payload_hash_matches={rule_hash_matches} {provenance_detail}",
        )
    )
    checks.append(
        _check(
            "contract_size_recorded",
            exact_matches(fill.get("contract_size"), rule.contract_size_btc_per_contract)
            and exact_matches(ev["contract_size_btc"], rule.contract_size_btc_per_contract),
            "CONTRACT_SIZE_DISAGREES_WITH_RULE",
            detail=f"fill={fill.get('contract_size')} rule={rule.contract_size_btc_per_contract}",
        )
    )
    filled = dec(ev["filled_contracts"])
    checks.append(
        _check(
            "whole_lot",
            is_whole(filled) and is_whole(rule.order_qty_step_contracts),
            "FRACTIONAL_LOT",
            detail=f"filled_contracts={filled}",
        )
    )
    checks.append(
        _check(
            "units_identity",
            exact_matches(dec(ev["base_qty_btc"]), filled * dec(ev["contract_size_btc"])),
            "BASE_QTY_NOT_CONTRACTS_TIMES_SIZE",
            detail=f"base={ev['base_qty_btc']} expected={filled * dec(ev['contract_size_btc'])}",
        )
    )
    # ``fills.qty`` is a count of *contracts* in the book path, and the evidence
    # carries both units: compare the contract count directly, then re-derive the
    # base quantity through the multiplier so a row that mixed units (the old
    # 0.839-contracts defect) fails here instead of footings quietly in the ledger.
    checks.append(
        _check(
            "fill_qty_matches_contracts",
            exact_matches(fill["qty"], filled),
            "FILL_QTY_DISAGREES_WITH_EVIDENCE",
            detail=f"fills.qty={fill['qty']} evidence.contracts={filled}",
        )
    )
    checks.append(
        _check(
            "fill_units_convention",
            exact_matches(
                dec(fill["qty"]) * dec(ev["contract_size_btc"]), dec(ev["base_qty_btc"])
            ),
            "FILL_QTY_NOT_IN_CONTRACT_UNITS",
            detail=f"fills.qty={fill['qty']} x size={ev['contract_size_btc']} "
                   f"!= evidence.base={ev['base_qty_btc']}",
        )
    )

    # 4. Every consumed level must exist in the capture, on the tick, untouched.
    used_raw = ev.get("levels_used")
    if isinstance(used_raw, str):
        used_raw = json.loads(used_raw)
    used_levels = [level_from_json(item) for item in (used_raw or [])]
    # The capture's liquidity comes from the *raw* levels: a normalized size the
    # payload does not contain is the defect this verifier exists to catch.
    captured = {
        (side, level.price): level.size_contracts
        for side, level in raw_depth.ordered_levels
    }
    off_tick = [
        level for level in used_levels
        if not _is_on_tick(level.price, rule.price_tick_usdt)
    ]
    checks.append(
        _check(
            "tick_alignment",
            not off_tick,
            "LEVEL_OFF_TICK",
            detail=";".join(str(level.price) for level in off_tick),
        )
    )
    invented = [level for level in used_levels if (level.side, level.price) not in captured]
    checks.append(
        _check(
            "levels_exist_in_capture",
            not invented,
            "LEVEL_NOT_IN_CAPTURE",
            detail=";".join(f"{level.side}@{level.price}" for level in invented),
        )
    )

    # 5. Causality, recomputed from stored timestamps rather than echoed numbers.
    bbo_event = _event_from_capture(quote_row, raw_bbo)
    depth_event = _event_from_capture(depth_row, raw_depth)
    submitted = ev["submitted_at"]
    if not isinstance(submitted, datetime) or not isinstance(ev["decision_at"], datetime):
        checks.append(_unverifiable("causality", "UNPARSEABLE_TIMESTAMP"))
        return FillVerdict(fill_id, UNVERIFIABLE, tuple(checks), fill)

    # The order as the strategy issued it, rebuilt from stored facts only.
    request = TradeRequest(
        decision_at=ev["decision_at"],
        submitted_at=submitted,
        contract_code=str(quote_row["contract_code"]),
        direction=_direction_for(ev),
        action=str(ev["open_or_close"]),
        requested_contracts=dec(ev["requested_contracts"]),
        order_id=str(ev["order_id"]),
    )
    eligible_at = request.eligible_at(policy)
    window_end = eligible_at + timedelta(milliseconds=policy.max_wait_ms)
    checks.append(
        _check(
            "eligible_at",
            exact_time(ev["eligible_at"], eligible_at),
            "ELIGIBLE_AT_NOT_SUBMITTED_PLUS_LATENCY",
            detail=f"stored={_iso(ev['eligible_at'])} recomputed={_iso(eligible_at)}",
        )
    )
    checks.append(
        _check(
            "quote_is_causal",
            eligible_at <= bbo_event.received_at <= window_end,
            "QUOTE_OUTSIDE_ELIGIBLE_WINDOW",
            detail=f"received={_iso(bbo_event.received_at)} window=[{_iso(eligible_at)}, "
                   f"{_iso(window_end)}]",
        )
    )
    checks.append(
        _check(
            "time_ordering",
            _monotonic(
                ev["decision_at"], ev["submitted_at"], ev["eligible_at"], ev["executed_at"]
            ),
            "TIMESTAMPS_NOT_MONOTONIC",
            detail=(
                f"decision={_iso(ev['decision_at'])} submitted={_iso(ev['submitted_at'])} "
                f"eligible={_iso(ev['eligible_at'])} executed={_iso(ev['executed_at'])}"
            ),
        )
    )
    checks.append(
        _check(
            "timestamps_are_distinct",
            len({_iso(ev[k]) for k in ("decision_at", "submitted_at", "eligible_at",
                                       "executed_at")}) == 4,
            "TIMESTAMPS_ARE_THE_SAME_INSTANT",
            detail="the old defect wrote one now() into every column",
        )
    )
    age = age_ms(bbo_event, ev["executed_at"])
    recorded_age = ev.get("quote_age_ms")
    if age is None:
        checks.append(
            _unverifiable(
                "quote_age",
                "QUOTE_TIMESTAMP_UNRECORDED",
                detail="neither venue nor receive time available, so age cannot be recomputed",
            )
        )
    else:
        checks.append(
            _check(
                "quote_age",
                age <= policy.quote_age_max_ms,
                "QUOTE_STALE",
                detail=f"recomputed_age_ms={age} budget={policy.quote_age_max_ms}",
            )
        )
        checks.append(
            _check(
                "quote_age_matches_recorded_claim",
                exact_matches(age, recorded_age),
                "QUOTE_AGE_CLAIM_DIFFERS_FROM_TIMESTAMPS",
                detail=f"recorded={recorded_age} recomputed={age}",
            )
            if recorded_age is not None
            else _unverifiable(
                "quote_age_matches_recorded_claim",
                "QUOTE_AGE_CLAIM_UNRECORDED",
                detail="the row stores no age, so the timestamps were never checked at run time",
            )
        )
    skew, skew_basis = skew_ms(bbo_event, depth_event)
    checks.append(
        _check(
            "depth_skew",
            abs(skew) <= policy.depth_skew_max_ms,
            "DEPTH_SKEW_EXCEEDS_BUDGET",
            detail=f"recomputed_skew_ms={skew} budget={policy.depth_skew_max_ms} "
                   f"basis={skew_basis}",
        )
    )
    recorded_skew = ev.get("depth_skew_ms")
    checks.append(
        _check(
            "depth_skew_matches_recorded_claim",
            abs(skew) == dec(recorded_skew),
            "DEPTH_SKEW_CLAIM_DIFFERS_FROM_TIMESTAMPS",
            detail=f"recorded={recorded_skew} recomputed={abs(skew)} "
                   f"basis={skew_basis}",
        )
        if recorded_skew is not None
        else _unverifiable(
            "depth_skew_matches_recorded_claim",
            "DEPTH_SKEW_CLAIM_UNRECORDED",
            detail=f"recomputed={abs(skew)} basis={skew_basis}",
        )
    )
    checks.append(
        _check(
            "same_connection_epoch",
            str(quote_row.get("connection_epoch")) == str(depth_row.get("connection_epoch")),
            "BBO_AND_DEPTH_FROM_DIFFERENT_CONNECTIONS",
            detail=f"bbo={quote_row.get('connection_epoch')} "
                   f"depth={depth_row.get('connection_epoch')}",
        )
    )
    # A socket epoch is the only thing that ties two channels to one stream.  A
    # pair with no epoch has no such tie, so it cannot be called a verified book.
    checks.append(
        _check(
            "connection_epoch_recorded",
            bool(str(quote_row.get("connection_epoch") or "").strip())
            and bool(str(depth_row.get("connection_epoch") or "").strip()),
            "CAPTURE_EPOCH_UNRECORDED",
            detail=f"bbo={quote_row.get('connection_epoch')!r} "
                   f"depth={depth_row.get('connection_epoch')!r}",
        )
    )
    # Venue time and receive time are separate claims: a venue stamp *after* we
    # received the frame is not a slow clock, it is a time that did not happen.
    clock_budget = timedelta(milliseconds=policy.depth_skew_max_ms)
    future_stamps = [
        label
        for label, event in (("bbo", bbo_event), ("depth", depth_event))
        if event.source_event_at is not None
        and event.source_event_at > event.received_at + clock_budget
    ]
    checks.append(
        _check(
            "venue_time_not_after_receipt",
            not future_stamps,
            "VENUE_TIME_AFTER_RECEIPT",
            detail="post-dated captures: " + ",".join(future_stamps)
            if future_stamps else f"allowance={clock_budget.total_seconds() * 1000} ms",
        )
    )
    checks.append(
        _check(
            "capture_not_after_execution",
            bbo_event.received_at <= ev["executed_at"]
            and depth_event.received_at <= ev["executed_at"],
            "CAPTURE_AFTER_EXECUTION",
            detail=f"bbo={_iso(bbo_event.received_at)} depth={_iso(depth_event.received_at)} "
                   f"executed={_iso(ev['executed_at'])}",
        )
    )
    recomputed_execution = max(
        eligible_at, bbo_event.received_at, depth_event.received_at
    )
    checks.append(
        _check(
            "execution_instant_recomputed",
            exact_time(ev["executed_at"], recomputed_execution),
            "EXECUTED_AT_NOT_DERIVED_FROM_CAPTURES",
            detail=f"stored={_iso(ev['executed_at'])} "
                   f"max(eligible, captures)={_iso(recomputed_execution)}",
        )
    )

    # 6. Independent price walk over the *raw* levels.  ``evaluate_book_fill`` is
    #    not consulted for the verdict; it is replayed afterwards as a diagnostic.
    side = str(ev["side"])
    ledger_id = quote_reference_for(
        source=str(depth_row["source"]),
        channel=str(depth_row["channel"]),
        contract_code=str(depth_row["contract_code"]),
        connection_epoch=depth_row.get("connection_epoch"),
        sequence_no=depth_row.get("sequence_no"),
    )
    walk = independent_depth_walk(
        levels=raw_depth.ask_levels if side == "buy" else raw_depth.bid_levels,
        side=side,
        requested_contracts=dec(ev["requested_contracts"]),
        prior_consumption=prior_consumption,
        ledger_id=ledger_id,
        policy=policy,
        rule=rule,
    )
    if walk.double_spent:
        checks.append(
            _check(
                "prior_consumption_within_raw_depth",
                False,
                "RAW_LEVEL_OVER_CONSUMED_BY_PRIOR_FILLS",
                detail=";".join(walk.double_spent),
            )
        )
    if walk.status == "unsupported":
        checks.append(
            _unverifiable(
                "independent_walk",
                walk.reason_codes[0],
                detail=walk.detail,
            )
        )
    elif walk.status == "unfilled":
        # The recorded fill cannot be reproduced from the raw capture at all:
        # every downstream number is then unverifiable, which is worse than a
        # mismatch in one of them.
        checks.append(
            _check(
                "reproduction",
                False,
                "INDEPENDENT_WALK_REJECTS_THE_RECORDED_FILL",
                detail=walk.detail,
                extra=walk.reason_codes,
            )
        )
    else:
        checks.append(_check("independent_walk", True, "", detail=walk.detail))
        checks.append(
            _check(
                "reproduced_contracts",
                exact_matches(walk.filled_contracts, filled),
                "CONTRACTS_NOT_REPRODUCIBLE",
                detail=f"walk={walk.filled_contracts} recorded={filled}",
            )
        )
        checks.append(
            _check(
                "reproduced_vwap",
                walk.fill_vwap is not None
                and money_matches(walk.fill_vwap, ev["fill_vwap"]),
                "VWAP_NOT_REPRODUCIBLE",
                detail=f"walk={walk.fill_vwap} recorded={ev['fill_vwap']}",
            )
        )
        checks.append(
            _check(
                "reproduced_touch_price",
                walk.touch_price is not None
                and money_matches(walk.touch_price, ev["touch_price"]),
                "TOUCH_PRICE_NOT_REPRODUCIBLE",
                detail=f"walk={walk.touch_price} recorded={ev['touch_price']}",
            )
        )
        checks.append(
            _check(
                "reproduced_levels",
                _same_levels(walk.levels_used, used_levels),
                "DEPTH_CONSUMPTION_NOT_REPRODUCIBLE",
                detail=f"walk={_levels_text(walk.levels_used)} "
                       f"recorded={_levels_text(used_levels)}",
            )
        )
        checks.append(
            _check(
                "book_visible_matches_raw",
                exact_matches(walk.visible_contracts, ev.get("book_visible_contracts")),
                "BOOK_VISIBLE_NOT_REPRODUCIBLE",
                detail=f"walk={walk.visible_contracts} "
                       f"recorded={ev.get('book_visible_contracts')}",
            )
            if ev.get("book_visible_contracts") is not None
            else _unverifiable(
                "book_visible_matches_raw",
                "BOOK_VISIBLE_UNRECORDED",
                detail=f"walk={walk.visible_contracts}",
            )
        )
        checks.append(
            _check(
                "participation_cap_matches_raw",
                exact_matches(walk.cap_contracts, ev.get("book_capped_contracts")),
                "PARTICIPATION_CAP_NOT_REPRODUCIBLE",
                detail=f"walk={walk.cap_contracts} "
                       f"recorded={ev.get('book_capped_contracts')}",
            )
            if ev.get("book_capped_contracts") is not None
            else _unverifiable(
                "participation_cap_matches_raw",
                "PARTICIPATION_CAP_UNRECORDED",
                detail=f"walk={walk.cap_contracts}",
            )
        )
        checks.append(
            _check(
                "fill_price_matches_fills_table",
                money_matches(dec(fill["price"]), ev["fill_vwap"]),
                "FILL_PRICE_DISAGREES_WITH_EVIDENCE",
                detail=f"fills.price={fill['price']} evidence.vwap={ev['fill_vwap']}",
            )
        )
        # 7. Slippage is derived, and zero is only honest at the touch.
        checks.append(
            _check(
                "slippage_is_measured",
                walk.slippage_bps is not None
                and money_matches(walk.slippage_bps, ev["slippage_bps"], MONEY_TOLERANCE),
                "SLIPPAGE_NOT_REPRODUCIBLE",
                detail=f"recomputed_bps={walk.slippage_bps} recorded={ev['slippage_bps']}",
            )
        )
        checks.append(
            _check(
                "zero_slippage_only_at_touch",
                not (dec(ev["slippage_bps"]) == 0 and dec(ev["fill_vwap"]) != dec(ev["touch_price"])),
                "SLIPPAGE_ZERO_WITH_A_NON_TOUCH_PRICE",
                detail=f"vwap={ev['fill_vwap']} touch={ev['touch_price']}",
            )
        )

    checks.extend(evidence_policy_checks(ev, side=side))

    # 6b. The engine replay stays in the report, but as a *diagnostic*: it may
    #     confirm that the captured fill was what the runtime computed, and it may
    #     not turn this verdict green.  It is fed the normalized pair on purpose —
    #     that is what the runtime actually priced — so a disagreement with the
    #     raw walk is visible instead of averaged away.
    try:
        normalized_pair = [event_from_row(quote_row), event_from_row(depth_row)]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        checks.append(
            _na(
                "engine_diagnostic",
                "DIAGNOSTIC_ONLY__NOT_DECISIVE",
                detail=f"normalized columns are not rebuildable ({exc}), so there is "
                       "nothing to diagnose",
            )
        )
    else:
        engine = evaluate_book_fill(
            request=request,
            policy=policy,
            rule=rule,
            quotes=normalized_pair,
            already_used=prior_consumption,
            require_depth_vwap=policy.slippage_mode == "depth_vwap",
        )
        engine_agrees_with_walk = bool(engine.filled) and (
            walk.fill_vwap is not None
            and engine.fill_vwap is not None
            and exact_matches(engine.filled_contracts, walk.filled_contracts)
            and money_matches(engine.fill_vwap, walk.fill_vwap)
            and _same_levels(engine.levels_used, walk.levels_used)
        )
        checks.append(
            _na(
                "engine_diagnostic",
                "DIAGNOSTIC_ONLY__NOT_DECISIVE",
                detail=(
                    f"engine_status={engine.status} engine_terms={engine.filled_contracts}"
                    f"@{engine.fill_vwap} walk_terms={walk.filled_contracts}@{walk.fill_vwap} "
                    f"agrees_with_independent_walk={engine_agrees_with_walk} "
                    f"engine_reasons={list(engine.reason_codes)}"
                ),
            )
        )

    # 8. Fees and funding.
    notional = dec(ev["base_qty_btc"]) * dec(ev["fill_vwap"])
    if walk.status == "filled":
        checks.append(
            _check(
                "notional_identity",
                money_matches(notional, walk.notional_usdt),
                "NOTIONAL_NOT_REPRODUCIBLE",
                detail=f"recorded={notional} walk={walk.notional_usdt}",
            )
        )
        checks.append(
            _check(
                "base_qty_matches_walk",
                walk.base_qty_btc is not None
                and money_matches(dec(ev["base_qty_btc"]), walk.base_qty_btc),
                "BASE_QTY_NOT_REPRODUCIBLE",
                detail=f"recorded={ev['base_qty_btc']} walk={walk.base_qty_btc}",
            )
        )
    else:
        checks.append(_na("notional_identity", "NOT_REPRODUCIBLE_FROM_CAPTURE"))
        checks.append(_na("base_qty_matches_walk", "NOT_REPRODUCIBLE_FROM_CAPTURE"))
    checks.append(
        _check(
            "fee_amount",
            money_matches(notional * dec(ev["fee_rate"]), dec(ev["fee_amount"])),
            "FEE_NOT_NOTIONAL_TIMES_RATE",
            detail=f"expected={notional * dec(ev['fee_rate'])} recorded={ev['fee_amount']}",
        )
    )
    checks.append(
        _check(
            "fee_matches_fills_table",
            money_matches(dec(fill["fee"]), dec(ev["fee_amount"])),
            "FEE_DISAGREES_WITH_FILLS_TABLE",
            detail=f"fills.fee={fill['fee']} evidence.fee={ev['fee_amount']}",
        )
    )
    checks.append(
        _check(
            "fee_rate_recorded",
            exact_matches(fill.get("fee_rate"), ev["fee_rate"]) and dec(ev["fee_rate"]) > 0,
            "FEE_RATE_UNRECORDED_OR_ZERO",
            detail=f"fills.fee_rate={fill.get('fee_rate')} evidence={ev['fee_rate']}",
        )
    )
    funding = dec(ev.get("funding_amount", 0))
    checks.append(
        _na("funding", "FUNDING_APPLIES_ONLY_AT_SETTLEMENT",
            detail=f"fill-level funding={funding}")
        if funding == 0
        else _check("funding", True, "", detail=f"funding={funding}")
    )

    # 9. Gross/net: the verifier re-derives them and reports them; whether they
    # foot against the day's ledger is a position/posting question, checked in
    # verify_positions — an identity like "net == gross - fees" proves nothing on
    # its own, so it is never emitted as a PASS.
    fees = dec(ev["fee_amount"])
    funding = dec(ev.get("funding_amount", 0))
    checks.append(
        _na(
            "gross_net",
            "FOOTED_AGAINST_LEDGER_IN_POSITION_CHECKS",
            detail=f"notional={notional} fees={fees} funding={funding}; realized "
                   "gross/net is a position-level identity, not a per-fill one",
        )
    )

    # 10. Close reason and margin are only checkable on the closing leg.
    if str(ev["open_or_close"]) == "close":
        reason = str(fill.get("close_reason") or ev.get("verification_reason") or "")
        checks.append(
            _check(
                "close_reason",
                bool(reason.strip()),
                "CLOSE_WITHOUT_REASON",
                detail=f"close_reason={reason!r}",
            )
        )
    else:
        checks.append(_na("close_reason", "NOT_A_CLOSING_FILL"))
    margin = _opt_dec(fill.get("isolated_margin"))
    checks.append(
        _na("margin", "MARGIN_NOT_ON_THE_FILL_ROW",
            detail="margin lives on the position; reconciled in position checks")
        if margin is None
        else _check("margin", margin > 0, "NON_POSITIVE_MARGIN", detail=f"margin={margin}")
    )

    # 11. Feed gaps overlapping this fill's window invalidate the proof entirely.
    overlapping = [
        gap for gap in gaps
        if _gap_overlaps(gap, ev["decision_at"], ev["executed_at"])
    ]
    if overlapping:
        checks.append(
            _unverifiable(
                "feed_continuity",
                "FEED_GAP_DURING_DECISION_WINDOW",
                detail=";".join(str(g.get("reason")) for g in overlapping),
            )
        )
    else:
        checks.append(_check("feed_continuity", True, ""))

    verdict = worst(checks)
    # Strict proof needs a venue time for *both* captures, reproduced from their
    # bytes above.  A receive-time-only replay may PASS numerically and still not
    # be venue-time proof, so the two claims are reported separately.
    venue_time_proven = raw_bbo.venue_ms is not None and raw_depth.venue_ms is not None
    return FillVerdict(
        fill_id, verdict, tuple(checks), fill, capture_mode, venue_time_proven
    )


def _hash_covers_pair(ev: Mapping[str, Any], quote_row: Mapping[str, Any],
                      depth_row: Mapping[str, Any]) -> bool:
    """Evidence hash must bind *both* captured events, in order.

    The engine emits ``bbo_digest + depth_digest``; accepting anything else (a bare
    label, one of the two, or a derived value) would let evidence point at a capture
    it was not actually measured against.
    """
    bbo = str(quote_row["raw_payload_sha256"])
    depth = str(depth_row["raw_payload_sha256"])
    if not bbo or not depth:
        return False
    return str(ev["raw_event_sha256"]) == bbo + depth


def _direction_for(ev: Mapping[str, Any]) -> str:
    """Recover long/short from the recorded intent: buy+open == long."""
    buy = str(ev["side"]) == "buy"
    opening = str(ev["open_or_close"]) == "open"
    return "long" if (buy and opening) or (not buy and not opening) else "short"


def _is_on_tick(price: Decimal, tick: Decimal) -> bool:
    if tick <= 0:
        return False
    remainder = dec(price) % tick
    return remainder == 0 or remainder == tick


def exact_time(a: Any, b: Any) -> bool:
    if isinstance(a, datetime) and isinstance(b, datetime):
        return a.astimezone(timezone.utc) == b.astimezone(timezone.utc)
    return _iso(a) == _iso(b)


def _monotonic(*values: Any) -> bool:
    stamps = [v for v in values if isinstance(v, datetime)]
    if len(stamps) != len(values):
        return False
    return all(stamps[i] <= stamps[i + 1] for i in range(len(stamps) - 1))


def age_ms(event: QuoteEvent, executed_at: datetime) -> Decimal | None:
    """Age of the quote *at execution*, recomputed from stored times.

    Mirrors the engine's own reference choice: venue time when it was published,
    receive time otherwise.  Measuring ``received_at - source_event_at`` instead
    would report transport latency and silently pass a stale quote.
    """
    ref = event.source_event_at or event.received_at
    if ref is None or not isinstance(executed_at, datetime):
        return None
    return _ms(_utc(executed_at) - _utc(ref))


def skew_ms(bbo: QuoteEvent, depth: QuoteEvent) -> tuple[Decimal, str]:
    """Depth-vs-BBO skew recomputed the way the engine measures it.

    Returns ``(ms, basis)``.  Comparing a venue timestamp against a receive
    timestamp would mix two clocks, so the basis is reported rather than implied.
    """
    if bbo.source_event_at is not None and depth.source_event_at is not None:
        return _ms(depth.source_event_at - bbo.source_event_at), "source_event_at"
    if bbo.received_at is None or depth.received_at is None:
        return Decimal(0), "unrecorded"
    return _ms(depth.received_at - bbo.received_at), "received_at"


def expected_slippage_bps(touch: Any, vwap: Any, *, side: str = "buy") -> Decimal:
    """bps implied by the achieved VWAP, positive when the fill is adverse.

    Mirrors the engine's convention: for a sell, finishing below the touch is
    adverse, so the sign is flipped rather than reported as a negative number.
    """
    touch_price, fill_price = dec(touch), dec(vwap)
    if touch_price == 0:
        return Decimal(0)
    deviation = (fill_price - touch_price) / touch_price * Decimal("10000")
    if side == "sell":
        deviation = -deviation
    return deviation if deviation > 0 else Decimal(0)


def _same_levels(a: Sequence[BookLevel], b: Sequence[BookLevel]) -> bool:
    if len(a) != len(b):
        return False
    return all(
        x.side == y.side and dec(x.price) == dec(y.price)
        and dec(x.size_contracts) == dec(y.size_contracts)
        for x, y in zip(a, b)
    )


def _levels_text(levels: Sequence[BookLevel]) -> str:
    return ",".join(f"{level.side}@{level.price}:{level.size_contracts}" for level in levels)


def _gap_overlaps(gap: Mapping[str, Any], start: datetime, end: datetime) -> bool:
    gap_start = gap.get("gap_start_at") or gap.get("detected_at")
    resolved = gap.get("resolved_at")
    if not isinstance(gap_start, datetime):
        return False
    if resolved is None:
        return gap_start <= end
    return gap_start <= end and resolved >= start


# ---------------------------------------------------------------------------
# Position / ledger reconciliation (pure)
# ---------------------------------------------------------------------------


def fills_for_position(
    position: Mapping[str, Any], fills: Sequence[Mapping[str, Any]]
) -> tuple[list[Mapping[str, Any]], str | None]:
    """Attribute fills to one position, or say why it cannot be done.

    ``paper_v2_fills`` carries no ``position_id``, so lineage is derived from
    account + instrument inside the position's own time window.  When two positions
    overlap (they must not, and the partial unique index enforces it per
    account/instrument) the attribution is refused rather than guessed: a guessed
    lineage would reconcile the wrong numbers.
    """
    pid = str(position["position_id"])
    opened, closed = position.get("opened_at"), position.get("closed_at")
    if not isinstance(opened, datetime):
        return [], f"POSITION_WINDOW_UNRECORDED:{pid}"
    # An open position has no upper bound: compare against None rather than
    # datetime.max, whose +1s grace window overflows the date range.
    end = closed if isinstance(closed, datetime) else None
    matched: list[Mapping[str, Any]] = []
    for fill in fills:
        when = fill.get("execute_timestamp")
        if not isinstance(when, datetime):
            return [], f"FILL_TIMESTAMP_UNRECORDED:{fill.get('fill_id')}"
        if (
            str(fill.get("account_id")) == str(position.get("account_id"))
            and str(fill.get("instrument")) == str(position.get("instrument"))
            and opened <= when
            and (end is None or when <= end + timedelta(seconds=1))
        ):
            matched.append(fill)
    return matched, None


def verify_positions(
    *,
    positions: Sequence[Mapping[str, Any]],
    fills: Sequence[Mapping[str, Any]],
    evidence_by_fill: Mapping[str, Mapping[str, Any]],
    postings: Sequence[Mapping[str, Any]],
) -> list[Check]:
    """Net contracts per position must close exactly; the ledger must foot."""
    checks: list[Check] = []
    claimed: set[str] = set()
    # Attribution is expensive and position windows overlap-check each other, so
    # each leg is computed once and reused for the ambiguity scan.
    legs: dict[str, tuple[list[Mapping[str, Any]], str | None]] = {
        str(position["position_id"]): fills_for_position(position, fills)
        for position in positions
    }
    for position in positions:
        pid = str(position["position_id"])
        leg, problem = legs[pid]
        if problem:
            checks.append(_unverifiable("position_lineage", problem))
            continue
        leg_ids = {str(f["fill_id"]) for f in leg}
        ambiguous = [
            other_pid
            for other_pid, (other_leg, other_problem) in legs.items()
            if other_pid != pid
            and not other_problem
            and leg_ids
            and {str(f["fill_id"]) for f in other_leg} & leg_ids
        ]
        if ambiguous:
            checks.append(
                _unverifiable(
                    "position_lineage",
                    "POSITION_FILL_LINEAGE_AMBIGUOUS",
                    detail=f"position={pid} shares fills with {','.join(ambiguous)}",
                )
            )
            continue
        claimed.update(leg_ids)
        if not isinstance(position.get("closed_at"), datetime):
            # An open position has no upper time bound, so every later fill on the
            # same account/instrument lands here.  Worth saying out loud: the
            # attribution is one-sided, not proven.
            checks.append(
                _na(
                    "position_lineage_upper_bound",
                    "POSITION_STILL_OPEN_NO_UPPER_BOUND",
                    detail=f"position={pid} attributes fills after opened_at only",
                )
            )

        missing_evidence = [str(f["fill_id"]) for f in leg if str(f["fill_id"]) not in evidence_by_fill]
        if missing_evidence:
            checks.append(
                _unverifiable(
                    "position_net_contracts",
                    "POSITION_FILL_WITHOUT_EVIDENCE",
                    detail=f"position={pid} fills without evidence: "
                           f"{','.join(missing_evidence[:3])}",
                )
            )
        elif not leg:
            # Nothing to sum: the position row cannot be re-derived from captures.
            checks.append(_na("position_net_contracts", "NO_FILLS_ATTRIBUTED_TO_POSITION",
                              detail=f"position={pid}"))
        else:
            # Every fill of this position must agree on the multiplier: the netting
            # below is in contracts, but the P&L behind it is in USDT, and a
            # position whose fills disagree about the instrument cannot be audited
            # at all.
            sizes = {dec(evidence_by_fill[str(f["fill_id"])]["contract_size_btc"]) for f in leg}
            if len(sizes) > 1:
                checks.append(
                    _unverifiable(
                        "position_net_contracts",
                        "POSITION_MULTIPLIER_DISAGREES_ACROSS_FILLS",
                        detail=f"position={pid} sizes={sorted(format(s, 'f') for s in sizes)}",
                    )
                )
            else:
                opened = sum(
                    (dec(evidence_by_fill[str(f["fill_id"])]["filled_contracts"])
                     for f in leg if not f.get("is_close")),
                    Decimal(0),
                )
                closed_c = sum(
                    (dec(evidence_by_fill[str(f["fill_id"])]["filled_contracts"])
                     for f in leg if f.get("is_close")),
                    Decimal(0),
                )
                remaining = dec(position.get("qty", 0))
                # Both the position row and the fills are contract counts, so the
                # netting is unitless: multiplying by the multiplier here would
                # compare base BTC against contracts and fail every real position.
                expected = max(opened - closed_c, Decimal(0))
                checks.append(
                    _check(
                        "position_net_contracts",
                        exact_matches(expected, remaining),
                        "POSITION_CONTRACTS_DO_NOT_NET",
                        detail=f"position={pid} open={opened} close={closed_c} "
                               f"remaining_contracts={remaining} "
                               f"expected_contracts={expected}",
                    )
                )

        # The positions row carries its own P&L components, and they must foot.
        gross = dec(position.get("realized_gross_pnl", 0))
        net = dec(position.get("realized_net_pnl", 0))
        fees = dec(position.get("entry_fee", 0)) + dec(position.get("exit_fee", 0))
        funding = dec(position.get("funding_cashflow", 0))
        checks.append(
            _check(
                "position_pnl_identity",
                money_matches(net, gross - fees + funding),
                "POSITION_PNL_DOES_NOT_FOOT",
                detail=f"position={pid} net={net} expected={gross - fees + funding} "
                       f"(gross={gross} fees={fees} funding={funding})",
            )
        )

        status = str(position.get("status"))
        if status in ("closed", "liquidated"):
            checks.append(
                _check(
                    "closed_position_is_flat",
                    dec(position.get("qty", 0)) == 0
                    and isinstance(position.get("closed_at"), datetime),
                    "CLOSED_POSITION_NOT_FLAT",
                    detail=f"position={pid} qty={position.get('qty')} "
                           f"closed_at={_iso(position.get('closed_at'))}",
                )
            )
            checks.append(
                _check(
                    "close_price_recorded",
                    position.get("close_price") is not None,
                    "CLOSED_WITHOUT_CLOSE_PRICE",
                    detail=f"position={pid}",
                )
            )
            if status == "liquidated":
                checks.append(
                    _na("liquidation_evidence", "LIQUIDATION_REQUIRES_LIQUIDITY_PRICE_PROOF",
                        detail=f"position={pid}: check the liquidation quote event "
                               "against the capture before trusting this row")
                )

    unattributed = sorted({str(f["fill_id"]) for f in fills} - claimed)
    if positions and unattributed:
        checks.append(
            _check(
                "every_fill_belongs_to_a_position",
                False,
                "FILL_NOT_ATTRIBUTED_TO_ANY_POSITION",
                detail=";".join(unattributed[:5]),
            )
        )

    # Ledger: fee postings are keyed by fill_id, so they can be footed exactly.
    fee_postings = [
        p for p in postings
        if str(p.get("bucket")) in ("entry_fee", "exit_fee")
    ]
    if not postings:
        checks.append(_na("ledger_fees", "NO_POSTINGS_IN_SCOPE",
                          detail="no postings were exported for this day"))
    else:
        unproven = sorted(
            str(f["fill_id"]) for f in fills if str(f["fill_id"]) not in evidence_by_fill
        )
        if unproven:
            # The booked side is complete but the evidence side is not.  Summing the
            # known fees against a partially missing population would manufacture a
            # FAIL that says nothing about the ledger, so the comparison is refused.
            checks.append(
                _unverifiable(
                    "ledger_fees",
                    "LEDGER_FEES_EVIDENCE_INCOMPLETE",
                    detail=f"fills without evidence: {','.join(unproven[:3])}",
                )
            )
        else:
            booked = sum((abs(dec(p.get("amount", 0))) for p in fee_postings), Decimal(0))
            expected_fee = sum(
                (dec(e["fee_amount"]) for e in evidence_by_fill.values()), Decimal(0)
            )
            checks.append(
                _check(
                    "ledger_fees",
                    money_matches(booked, expected_fee),
                    "LEDGER_FEES_DO_NOT_FOOT",
                    detail=f"postings={booked} evidence={expected_fee} "
                           f"fee_rows={len(fee_postings)}",
                )
            )
        dup = duplicate_event_ids(
            [{"event_id": f"{p.get('source_type')}|{p.get('source_ref')}|{p.get('bucket')}"}
             for p in postings]
        )
        checks.append(
            _check(
                "ledger_no_duplicate_postings",
                not dup,
                "DUPLICATE_POSTING_KEY",
                detail=";".join(dup[:5]),
            )
        )
    return checks


def verify_day_shape(
    *,
    day: Mapping[str, Any],
    fills: Sequence[Mapping[str, Any]],
    positions: Sequence[Mapping[str, Any]],
) -> list[Check]:
    """Structural claims: coverage, open positions after close, orphan fills."""
    checks: list[Check] = []
    start, end = day.get("start_utc"), day.get("end_utc")
    if isinstance(start, datetime) and isinstance(end, datetime):
        outside = [
            str(f["fill_id"]) for f in fills
            if not (start <= (f.get("execute_timestamp") or start) < end)
        ]
        checks.append(
            _check(
                "fills_inside_day_window",
                not outside,
                "FILL_OUTSIDE_DAY_WINDOW",
                detail=";".join(outside[:5]),
            )
        )
    else:
        checks.append(_unverifiable("fills_inside_day_window", "DAY_WINDOW_UNRECORDED"))

    if str(day.get("state")) == "closed":
        still_open = [str(p["position_id"]) for p in positions
                      if str(p.get("status")) in ("open", "reducing")]
        checks.append(
            _check(
                "no_open_position_after_close",
                not still_open,
                "OPEN_POSITION_AFTER_DAY_CLOSE",
                detail=";".join(still_open[:5]),
            )
        )
    else:
        checks.append(_na("no_open_position_after_close", "DAY_NOT_CLOSED",
                          detail=f"state={day.get('state')}"))
    checks.append(
        _check(
            "day_has_activity",
            bool(fills) or bool(positions),
            "EMPTY_DAY",
            detail="a day with no fills is reported, not treated as success",
        )
    )
    return checks


def duplicate_event_ids(rows: Sequence[Mapping[str, Any]], key: str = "event_id") -> list[str]:
    seen: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key))
        seen[value] = seen.get(value, 0) + 1
    return sorted(k for k, v in seen.items() if v > 1)


# ---------------------------------------------------------------------------
# Read-only data loading
# ---------------------------------------------------------------------------


def redact_dsn(url: str) -> str:
    """Never write a password into a report artifact."""
    return re.sub(r"://([^:/@]+):[^@]+@", r"://\1:***@", url)


_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


async def open_read_only_session(database_url: str, *, search_path: str | None = None):
    """Connect and force the session read-only before anything is queried.

    ``search_path`` exists for QA/staging databases where the paper substrate lives
    in an isolated schema; it is validated as a plain identifier because it cannot
    be parameterized, and it is set *before* any query so no default-schema object
    can be mistaken for the substrate.
    """
    try:
        import asyncpg
    except ImportError as exc:  # pragma: no cover - среда без asyncpg
        raise RuntimeError("asyncpg is required to run the verifier") from exc
    conn = await asyncpg.connect(database_url)
    await conn.execute("SET default_transaction_read_only = on")
    await conn.execute("SET statement_timeout = '60s'")
    if search_path is not None:
        if not _SCHEMA_RE.match(search_path):
            await conn.close()
            raise RuntimeError(f"unsafe schema name: {search_path!r}")
        await conn.execute(f'SET search_path TO "{search_path}"')
    return conn


async def fetch_day_context(conn, *, day_id: str, experiment_id: str | None) -> dict[str, Any]:
    """Load every row the verification needs, scoped to this day and experiment."""
    day = await conn.fetchrow("SELECT * FROM paper_v2_days WHERE day_id = $1::uuid", day_id)
    if day is None:
        raise LookupError(f"day {day_id} does not exist")

    experiment = None
    if experiment_id:
        experiment = await conn.fetchrow(
            "SELECT * FROM paper_v2_experiments WHERE experiment_id = $1::uuid", experiment_id
        )
        if experiment is None:
            raise LookupError(f"experiment {experiment_id} does not exist")

    positions = await conn.fetch(
        "SELECT * FROM paper_v2_positions WHERE day_id = $1::uuid ORDER BY opened_at", day_id
    )
    account_ids = sorted({str(p["account_id"]) for p in positions})
    # Orders give the day its account scope independently of positions: a day whose
    # position rows went missing must still have its fills loaded and reported, not
    # silently collapse into "empty day".
    order_accounts = await conn.fetch(
        "SELECT DISTINCT account_id::text AS account_id FROM paper_v2_orders "
        "WHERE day_id = $1::uuid",
        day_id,
    )
    account_ids = sorted(set(account_ids) | {str(r["account_id"]) for r in order_accounts})
    if experiment is not None:
        scoped = str(experiment["account_id"])
        account_ids = [scoped] if not account_ids else account_ids
        if account_ids != [scoped]:
            # Mixing accounts inside one experiment day is a scoping failure, not
            # something to average over later.
            account_ids = sorted(set(account_ids) | {scoped})

    fills: list[dict[str, Any]] = []
    if account_ids:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(account_ids)))
        rows = await conn.fetch(
            f"SELECT * FROM paper_v2_fills WHERE account_id::text IN ({placeholders}) "
            "AND execute_timestamp >= $" f"{len(account_ids) + 1} "
            "AND execute_timestamp < $" f"{len(account_ids) + 2} "
            "ORDER BY execute_timestamp, fill_id",
            *account_ids,
            day["start_utc"],
            day["end_utc"],
        )
        fills = [dict(r) for r in rows]

    fill_ids = [str(f["fill_id"]) for f in fills]
    evidence_by_fill: dict[str, dict[str, Any]] = {}
    if fill_ids:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(fill_ids)))
        rows = await conn.fetch(
            f"SELECT * FROM paper_v2_execution_evidence WHERE fill_id::text IN ({placeholders})",
            *fill_ids,
        )
        evidence_by_fill = {str(r["fill_id"]): dict(r) for r in rows}

    quote_ids = sorted(
        {
            str(ev[key])
            for ev in evidence_by_fill.values()
            for key in ("quote_event_id", "depth_event_id")
            if ev.get(key)
        }
    )
    events: dict[str, dict[str, Any]] = {}
    if quote_ids:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(quote_ids)))
        rows = await conn.fetch(
            f"SELECT * FROM paper_v2_market_events WHERE quote_event_id::text IN ({placeholders})",
            *quote_ids,
        )
        events = {str(r["quote_event_id"]): dict(r) for r in rows}

    rule_ids = sorted({str(ev["contract_rule_id"]) for ev in evidence_by_fill.values()
                        if ev.get("contract_rule_id")})
    rules: dict[str, dict[str, Any]] = {}
    if rule_ids:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(rule_ids)))
        rows = await conn.fetch(
            f"SELECT * FROM paper_v2_contract_rules WHERE contract_rule_id::text "
            f"IN ({placeholders})",
            *rule_ids,
        )
        rules = {str(r["contract_rule_id"]): dict(r) for r in rows}

    # Consumption of every quote in scope, so earlier fills can be replayed.
    usage: list[dict[str, Any]] = []
    if quote_ids:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(quote_ids)))
        rows = await conn.fetch(
            "SELECT u.*, e.source, e.channel, e.contract_code, e.connection_epoch, "
            "e.sequence_no FROM paper_v2_market_book_usage u "
            "JOIN paper_v2_market_events e USING (quote_event_id) "
            f"WHERE u.quote_event_id::text IN ({placeholders})", 
            *quote_ids,
        )
        usage = [dict(r) for r in rows]

    gaps: list[dict[str, Any]] = []
    instrument = str(fills[0]["instrument"]) if fills else "BTC-USDT"
    rows = await conn.fetch(
        "SELECT * FROM paper_v2_feed_gaps WHERE contract_code = $1 ORDER BY detected_at",
        instrument,
    )
    gaps = [dict(r) for r in rows]

    postings: list[dict[str, Any]] = []
    if account_ids:
        placeholders = ", ".join(f"${i + 1}" for i in range(len(account_ids)))
        rows = await conn.fetch(
            f"SELECT * FROM paper_v2_postings WHERE account_id::text IN ({placeholders}) "
            "AND (day_id = $" f"{len(account_ids) + 1}::uuid OR day_id IS NULL)",
            *account_ids,
            day_id,
        )
        postings = [dict(r) for r in rows]

    domain_events: list[dict[str, Any]] = []
    try:
        rows = await conn.fetch(
            "SELECT * FROM paper_v2_domain_events WHERE day_id = $1::uuid ORDER BY occurred_at",
            day_id,
        )
        domain_events = [dict(r) for r in rows]
    except Exception:  # noqa: BLE001 - P3 table may not exist yet
        domain_events = []

    return {
        "day": dict(day),
        "experiment": dict(experiment) if experiment else None,
        "positions": [dict(p) for p in positions],
        "fills": fills,
        "evidence_by_fill": evidence_by_fill,
        "events": events,
        "rules": rules,
        "usage": usage,
        "gaps": gaps,
        "postings": postings,
        "domain_events": domain_events,
        "instrument": instrument,
    }


def usage_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    """Ledger key of a stored consumption row, identity-derived like the engine's."""
    ref = quote_reference_for(
        source=row["source"],
        channel=row["channel"],
        contract_code=row["contract_code"],
        connection_epoch=row["connection_epoch"],
        sequence_no=row["sequence_no"],
    )
    return consumption_key(ref, row["trade_side"], row["price"])


def build_prior_consumption(
    usage: Sequence[Mapping[str, Any]], *, before_fill_id: str, executed_at: Any,
    fills: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, str], Decimal]:
    """Everything consumed by fills that happened strictly before this one.

    Order is the recorded replay order (``executed_at`` then ``fill_id``); using set
    order would make the verdict depend on row arrival.
    """
    fill_time = {str(f["fill_id"]): f.get("execute_timestamp") for f in fills}
    out: dict[tuple[str, str, str], Decimal] = {}
    for row in usage:
        fid = str(row.get("fill_id"))
        if fid == before_fill_id:
            continue
        when = fill_time.get(fid)
        if not isinstance(when, datetime) or not isinstance(executed_at, datetime):
            continue
        if (when, fid) < (executed_at, before_fill_id):
            key = usage_key(row)
            out[key] = out.get(key, Decimal(0)) + dec(row["contracts_used"])
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def verify_context(ctx: Mapping[str, Any]) -> dict[str, Any]:
    """Run every check over a loaded day and return the reconciliation payload."""
    fills = ctx["fills"]
    evidence_by_fill = ctx["evidence_by_fill"]
    # A day with no evidence rows must still produce a report: ``next`` without a
    # default would raise StopIteration and lose the verdict entirely.
    policy_version = next(
        (
            str(ev["model_version"]) for ev in evidence_by_fill.values()
            if ev.get("model_version")
        ),
        "book_v1",
    )
    snapshot = next(
        (
            (
                json.loads(ev["policy_snapshot"])
                if isinstance(ev.get("policy_snapshot"), str)
                else (ev.get("policy_snapshot") or {})
            )
            for ev in evidence_by_fill.values()
            if ev.get("policy_snapshot")
        ),
        {},
    ) or {}
    policy = policy_from_snapshot(snapshot, policy_version)

    verdicts: list[FillVerdict] = []
    for fill in fills:
        ev = evidence_by_fill.get(str(fill["fill_id"]))
        verdicts.append(
            verify_fill(
                fill=fill,
                evidence=ev,
                events=ctx["events"],
                rules=ctx["rules"],
                prior_consumption=build_prior_consumption(
                    ctx["usage"],
                    before_fill_id=str(fill["fill_id"]),
                    executed_at=(ev or {}).get("executed_at") or fill.get("execute_timestamp"),
                    fills=fills,
                ),
                policy=policy,
                gaps=ctx["gaps"],
            )
        )

    structural = verify_day_shape(
        day=ctx["day"], fills=fills, positions=ctx["positions"]
    ) + verify_positions(
        positions=ctx["positions"],
        fills=fills,
        evidence_by_fill=evidence_by_fill,
        postings=ctx["postings"],
    )

    dup_usage = duplicate_event_ids(
        [{"event_id": f"{u.get('fill_id')}|{u.get('quote_event_id')}|"
                  f"{u.get('trade_side')}|{u.get('price')}"} for u in ctx["usage"]]
    )
    if ctx["usage"]:
        structural.append(
            _check(
                "depth_consumption_no_duplicates",
                not dup_usage,
                "DUPLICATE_DEPTH_CONSUMPTION",
                detail=";".join(dup_usage[:5]),
            )
        )
    if ctx["domain_events"]:
        structural.append(
            _check(
                "domain_event_ids_unique",
                not duplicate_event_ids(ctx["domain_events"]),
                "DUPLICATE_DOMAIN_EVENT_ID",
                detail=";".join(duplicate_event_ids(ctx["domain_events"])[:5]),
            )
        )
    else:
        structural.append(_na("domain_event_ids_unique", "NO_DOMAIN_EVENTS_TABLE_OR_ROWS",
                              detail="P3 not present for this day"))

    counts: dict[str, int] = {}
    for verdict in verdicts:
        counts[verdict.verdict] = counts.get(verdict.verdict, 0) + 1
    structural_counts: dict[str, int] = {}
    for check in structural:
        structural_counts[check.verdict] = structural_counts.get(check.verdict, 0) + 1
    #: How many fills were priced from a socket pair and how many from a modelled
    #: reconstruction.  A single number for the day would hide a mixed run.
    capture_counts: dict[str, int] = {}
    for verdict in verdicts:
        key = verdict.capture_mode or CAPTURE_MODE_UNKNOWN
        capture_counts[key] = capture_counts.get(key, 0) + 1

    return {
        "verdicts": verdicts,
        "structural": structural,
        "policy": policy,
        "policy_version": policy_version,
        "fill_counts": counts,
        "structural_counts": structural_counts,
        "capture_counts": capture_counts,
        "overall": (
            FAIL if counts.get(FAIL, 0) or structural_counts.get(FAIL, 0)
            else UNVERIFIABLE if counts.get(UNVERIFIABLE, 0) or structural_counts.get(UNVERIFIABLE, 0)
            else PASS
        ),
    }


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


def git_context() -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        out["commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True, text=True,
            timeout=10,
        ).stdout.strip()
        out["dirty"] = bool(
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=str(ROOT), capture_output=True,
                text=True, timeout=20,
            ).stdout.strip()
        )
    except Exception:  # noqa: BLE001 - git is context, never a verdict
        out["commit"] = "unknown"
        out["dirty"] = "unknown"  # type: ignore[assignment]
    return out


def write_artifacts(
    output_dir: Path, *, ctx: Mapping[str, Any], result: Mapping[str, Any],
    day_id: str, experiment_id: str | None, database_url: str, strict: bool,
) -> dict[str, str]:
    verdicts: Sequence[FillVerdict] = result["verdicts"]
    policy: ExecutionPolicy = result["policy"]

    manifest = {
        "verifier_version": VERIFIER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "strict": strict,
        "day_id": day_id,
        "experiment_id": experiment_id,
        "instrument": ctx["instrument"],
        "database": redact_dsn(database_url),
        "server_version": ctx.get("server_version"),
        "git": git_context(),
        "tolerances": {
            "money_usdt": format(MONEY_TOLERANCE, "f"),
            "contracts_and_levels": "0",
            "time_ms": format(TIME_QUANTUM, "f"),
            "rounding_policy_version": ROUNDING_POLICY_VERSION,
            "time_policy_version": TIME_POLICY_VERSION,
            "replay_order_policy_version": REPLAY_ORDER_POLICY_VERSION,
        },
        "execution_policy": {
            "version": policy.version,
            "latency_ms": policy.latency_ms,
            "max_wait_ms": policy.max_wait_ms,
            "quote_age_max_ms": policy.quote_age_max_ms,
            "depth_skew_max_ms": policy.depth_skew_max_ms,
            "participation_cap": format(policy.participation_cap, "f"),
            "slippage_mode": policy.slippage_mode,
            "require_ws_capture": policy.require_ws_capture,
            "allow_receive_time_ordering": policy.allow_receive_time_ordering,
        },
        "counts": {
            "fills": len(ctx["fills"]),
            "evidence_rows": len(ctx["evidence_by_fill"]),
            "market_events": len(ctx["events"]),
            "positions": len(ctx["positions"]),
            "postings": len(ctx["postings"]),
            "feed_gaps": len(ctx["gaps"]),
            "domain_events": len(ctx["domain_events"]),
        },
        "verdict_counts": result["fill_counts"],
        "structural_verdict_counts": result["structural_counts"],
        "capture_mode_counts": result.get("capture_counts", {}),
        "overall": result["overall"],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )

    fill_columns = [
        "fill_id", "order_id", "instrument", "side", "price", "qty", "contract_size",
        "fee", "fee_rate", "is_close", "execute_timestamp", "verdict", "reason_codes",
        "fill_vwap", "touch_price", "slippage_bps", "filled_contracts", "base_qty_btc",
        "quote_event_id", "depth_event_id", "raw_event_sha256", "policy_hash",
    ]
    with (output_dir / "fills.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fill_columns)
        writer.writeheader()
        for verdict in verdicts:
            ev = ctx["evidence_by_fill"].get(verdict.fill_id) or {}
            row = {name: verdict.row.get(name) for name in fill_columns[:11]}
            row["verdict"] = verdict.verdict
            row["reason_codes"] = "|".join(verdict.reason_codes())
            for name in (
                "fill_vwap", "touch_price", "slippage_bps", "filled_contracts",
                "base_qty_btc", "quote_event_id", "depth_event_id", "raw_event_sha256",
                "policy_hash",
            ):
                row[name] = ev.get(name)
            writer.writerow(row)

    position_columns = [
        "position_id", "account_id", "instrument", "side", "qty", "avg_entry_price",
        "isolated_margin", "status", "opened_at", "closed_at", "close_price",
        "realized_gross_pnl", "realized_net_pnl", "entry_fee", "exit_fee",
        "funding_cashflow",
    ]
    with (output_dir / "positions.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=position_columns)
        writer.writeheader()
        for position in ctx["positions"]:
            writer.writerow({name: position.get(name) for name in position_columns})

    reconciliation = {
        "overall": result["overall"],
        "day_window": {
            "start_utc": _iso(ctx["day"].get("start_utc")),
            "end_utc": _iso(ctx["day"].get("end_utc")),
            "state": ctx["day"].get("state"),
        },
        "fills": [
            {
                "fill_id": v.fill_id,
                "verdict": v.verdict,
                "capture_mode": v.capture_mode or CAPTURE_MODE_UNKNOWN,
                "reason_codes": list(v.reason_codes()),
                "checks": [c.as_dict() for c in v.checks],
            }
            for v in verdicts
        ],
        "structural": [c.as_dict() for c in result["structural"]],
        "not_applicable": [
            c.as_dict()
            for c in result["structural"]
            if c.verdict == NOT_APPLICABLE
        ]
        + [
            {"name": check["name"], "verdict": check["verdict"],
             "reason_codes": check["reason_codes"], "detail": check["detail"]}
            for v in verdicts
            for check in [c.as_dict() for c in v.checks]
            if check["verdict"] == NOT_APPLICABLE
        ],
    }
    (output_dir / "reconciliation.json").write_text(
        json.dumps(reconciliation, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Verified paper experiment — reconciliation",
        "",
        f"* day `{day_id}`",
        f"* experiment `{experiment_id or 'n/a'}`",
        f"* instrument `{ctx['instrument']}`",
        f"* overall **{result['overall']}**",
        f"* fills {len(ctx['fills'])}, evidence rows {len(ctx['evidence_by_fill'])}, "
        f"captured events {len(ctx['events'])}",
        "* capture grades: "
        + (
            ", ".join(
                f"{name} {count}" for name, count in sorted(result.get("capture_counts", {}).items())
            )
            or "none"
        ),
        f"* verifier `{VERIFIER_VERSION}` (read-only), rounding `{ROUNDING_POLICY_VERSION}`",
        "",
        "## Fill verdicts",
        "",
        "| fill | verdict | reason codes |",
        "|---|---|---|",
    ]
    for verdict in verdicts:
        codes = "|".join(verdict.reason_codes()) or "-"
        lines.append(f"| `{verdict.fill_id[:8]}` | {verdict.verdict} | {codes} |")
    lines += ["", "## Structural checks", "", "| check | verdict | reason / detail |",
              "|---|---|---|"]
    for check in result["structural"]:
        detail = " ".join([*check.reason_codes, check.detail]) or "ok"
        lines.append(f"| {check.name} | {check.verdict} | {detail} |")
    n_a = [c for c in result["structural"] if c.verdict == NOT_APPLICABLE]
    lines += [
        "",
        "## Samples that could not be exercised (N/A, not PASS)",
        "",
    ]
    if n_a:
        for check in n_a:
            lines.append(f"* `{check.name}` — {' '.join(check.reason_codes)}: {check.detail}")
    else:
        lines.append("* none")
    lines += [
        "",
        "A PASS here means the capture reproduces the recorded price. It does not",
        "mean delivery worked, that a device UI was checked, or that a strategy is",
        "profitable; those gates live in the VP matrix.",
        "",
    ]
    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    return {
        "manifest": str(output_dir / "manifest.json"),
        "fills": str(output_dir / "fills.csv"),
        "positions": str(output_dir / "positions.csv"),
        "reconciliation": str(output_dir / "reconciliation.json"),
        "summary": str(output_dir / "summary.md"),
    }


async def record_verdicts(
    database_url: str, result: Mapping[str, Any], run_id: str, *, search_path: str | None = None
) -> int:
    """Write only the verification columns; the database blocks anything else."""
    conn = await asyncpg_connect(database_url, search_path=search_path)
    updated = 0
    try:
        for verdict in result["verdicts"]:
            status = PASS if verdict.verdict == PASS else (
                FAIL if verdict.verdict == FAIL else UNVERIFIABLE
            )
            # asyncpg returns a command tag like "UPDATE 3", not a row count.
            tag = await conn.execute(
                "UPDATE paper_v2_execution_evidence "
                "SET verification_status = $2, verification_reason = $3, "
                "    verifier_run_id = $4, verified_at = NOW() "
                "WHERE fill_id = $1::uuid AND verification_status = 'PENDING_VERIFICATION'",
                verdict.fill_id,
                status,
                "|".join(verdict.reason_codes()) or "all checks reproduced",
                run_id,
            )
            updated += int(str(tag).split()[-1])
    finally:
        await conn.close()
    return updated


async def asyncpg_connect(database_url: str, *, search_path: str | None = None):
    """Writable connection — used only by the opt-in ``--record-verdicts`` path."""
    try:
        import asyncpg
    except ImportError as exc:  # pragma: no cover - среда без asyncpg
        raise RuntimeError("asyncpg is required to record verdicts") from exc
    conn = await asyncpg.connect(database_url)
    if search_path is not None:
        if not _SCHEMA_RE.match(search_path):
            await conn.close()
            raise RuntimeError(f"unsafe schema name: {search_path!r}")
        await conn.execute(f'SET search_path TO "{search_path}"')
    return conn


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--day-id", required=True, help="paper_v2_days.day_id (UUID)")
    parser.add_argument("--experiment-id", default=None, help="paper_v2_experiments UUID")
    parser.add_argument("--output-dir", required=True, help="existing directory for artifacts")
    parser.add_argument("--strict", action="store_true",
                        help="exit non-zero on any FAIL or UNVERIFIABLE")
    parser.add_argument("--database-url", default=None,
                        help="defaults to DATABASE_URL, then WORED_TEST_DATABASE_URL")
    parser.add_argument("--record-verdicts", action="store_true",
                        help="opt-in write of the verification columns only")
    parser.add_argument("--search-path", default=None,
                        help="isolated schema to verify (QA/staging); refused for prod runs")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    if not output_dir.is_dir():
        print(f"output dir does not exist: {output_dir}", file=sys.stderr)
        return EXIT_USAGE_ERROR
    for name, value in (("--day-id", args.day_id), ("--experiment-id", args.experiment_id)):
        if value is not None and not _UUID_RE.match(value):
            print(f"{name} must be a UUID, got {value!r}", file=sys.stderr)
            return EXIT_USAGE_ERROR

    conn = await open_read_only_session(args.database_url, search_path=args.search_path)
    try:
        # Refuse the production database *before* reading anything from it.
        db_name = await conn.fetchval("SELECT current_database()")
        if db_name == "trading":
            print("refusing to verify against the production database name 'trading'",
                  file=sys.stderr)
            return EXIT_USAGE_ERROR
        ctx = await fetch_day_context(
            conn, day_id=args.day_id, experiment_id=args.experiment_id
        )
        ctx["server_version"] = await conn.fetchval("SELECT version()")
        row_count_before = await conn.fetchval(
            "SELECT count(*) FROM paper_v2_execution_evidence"
        )
    finally:
        await conn.close()

    result = verify_context(ctx)
    artifacts = write_artifacts(
        output_dir,
        ctx=ctx,
        result=result,
        day_id=args.day_id,
        experiment_id=args.experiment_id,
        database_url=args.database_url,
        strict=args.strict,
    )

    if args.record_verdicts:
        run_id = f"verify-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
        changed = await record_verdicts(
            args.database_url, result, run_id, search_path=args.search_path
        )
        artifacts["verdicts_recorded"] = f"{changed} rows ({run_id})"
        check = await asyncpg_connect(args.database_url, search_path=args.search_path)
        try:
            after = await check.fetchval("SELECT count(*) FROM paper_v2_execution_evidence")
        finally:
            await check.close()
        if after != row_count_before:
            print("verifier changed the evidence row count — abort", file=sys.stderr)
            return EXIT_USAGE_ERROR

    counts = result["fill_counts"]
    print(
        f"verdict={result['overall']} fills={sum(counts.values())} "
        f"PASS={counts.get(PASS, 0)} FAIL={counts.get(FAIL, 0)} "
        f"UNVERIFIABLE={counts.get(UNVERIFIABLE, 0)} "
        f"structural={result['structural_counts']}"
    )
    for name, path in artifacts.items():
        print(f"  {name}: {path}")

    if args.strict and result["overall"] != PASS:
        return EXIT_STRICT_FAILURE
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    args.database_url = (
        args.database_url or os.getenv("DATABASE_URL") or os.getenv("WORED_TEST_DATABASE_URL")
    )
    if not args.database_url:
        print("no database URL: pass --database-url or set DATABASE_URL", file=sys.stderr)
        return EXIT_USAGE_ERROR
    try:
        return asyncio.run(_run(args))
    except LookupError as exc:
        print(f"not found: {exc}", file=sys.stderr)
        return EXIT_USAGE_ERROR
    except Exception as exc:  # noqa: BLE001 - operational failure, never a verdict
        print(f"verifier could not run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_USAGE_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

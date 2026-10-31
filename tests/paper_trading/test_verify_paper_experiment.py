"""TZ §4.4 — the independent verifier must re-derive, never echo.

Every capture in this file starts as a **venue frame** — the socket JSON for
``market.BTC-USDT.bbo`` / ``depth.size_20`` or the REST body — is parsed by the
collector's own parser and then stored through
:mod:`paper_trading.quote_store`.  That way the bytes under test are bytes a real
capture would have produced, and the verifier is fed the same shapes the exchange
sends.  The verifier itself never imports those parsers: it carries its own, and
``test_the_verifier_parsers_are_not_the_collector_parsers`` says so.

Two failure modes are checked exhaustively:

* a *true* capture reproduces and yields PASS with no reason codes;
* every way the old runtime cheated (repriced fill, invented liquidity, one
  ``now()`` in all four timestamps, fractional lot, 1000× fee, a stale quote,
  a gapped feed, double-spent depth) — and every way the *capture row* could lie
  about its payload — yields a named FAIL/UNVERIFIABLE reason code.
"""
from __future__ import annotations

import asyncio
import ast
import hashlib
import json
import os
import sys
import uuid as _uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import verify_paper_experiment as vpe  # noqa: E402

from collector.htx.book_capture import (  # noqa: E402  - fixtures only
    parse_bbo_payload as parse_rest_bbo,
    parse_depth_payload as parse_rest_depth,
)
from collector.htx.ws_book_capture import (  # noqa: E402  - fixtures only
    parse_ws_bbo,
    parse_ws_depth,
)
from paper_trading.book_execution import (  # noqa: E402
    ExecutionPolicy,
    QuoteEvent,
    TradeRequest,
    evaluate_book_fill,
)
from paper_trading.contract_rules import (  # noqa: E402
    HTX_BTC_USDT_LINEAR_SWAP_V1 as BASE_RULE,
    canonical_pair_hash,
)
from tests.paper_trading.book_pg import (  # noqa: E402
    bbo_payload as rest_bbo_payload,
    captured_rule_provenance,
    depth_payload as rest_depth_payload,
)
from paper_trading.quote_store import (  # noqa: E402
    canonical_json,
    canonical_sha256,
    contract_rule_row,
    evidence_row,
    quote_event_row,
)

E = datetime(2026, 10, 23, 13, 7, 4, tzinfo=timezone.utc)
POLICY = ExecutionPolicy(
    latency_ms=250,
    max_wait_ms=2000,
    quote_age_max_ms=1000,
    depth_skew_max_ms=500,
    participation_cap=Decimal("0.1"),
)
FEE_RATE = Decimal("0.0005")

RULE_PAYLOAD = {
    "symbol": "BTC-USDT",
    "contractSize": 0.001,
    "priceTickSize": 0.1,
    "orderSizeStep": 1,
    "orderSizeMin": 1,
}

RULE = replace(
    BASE_RULE,
    source_payload_hash=canonical_sha256(RULE_PAYLOAD),
)

#: The versions the collectors actually declare, and the transports they name.
WS_PARSER_VERSION = "htx_linear_swap_ws_v1"
REST_PARSER_VERSION = "htx_linear_swap_rest_v1"
WS_SOURCE = "htx_public_ws"
REST_SOURCE = "htx_public_rest"
#: The fabricated label the old fixtures used; no collector ever emitted it.
LEGACY_FAKE_VERSION = "linear_swap_ws_v2"

#: One top-of-book and one two-level ask side, as the venue publishes them.
BBO_BID = ("81675.2", "6551")
BBO_ASK = ("81675.3", "1053")
DEPTH_BID = ("81675.2", "6551")
DEPTH_ASKS = (("81675.3", "100"), ("81675.4", "10000"))


def _venue_ms(offset_ms: int) -> int:
    return int((E + timedelta(milliseconds=offset_ms)).timestamp() * 1000)


def ws_bbo_frame(
    *,
    bid: tuple[str, str] = BBO_BID,
    ask: tuple[str, str] = BBO_ASK,
    sequence: int = 100,
    offset_ms: int = 300,
    venue_ms: int | None = None,
) -> dict:
    """``market.BTC-USDT.bbo`` exactly as the socket prints it."""
    stamp = _venue_ms(offset_ms) if venue_ms is None else venue_ms
    return {
        "ch": "market.BTC-USDT.bbo",
        "ts": stamp,
        "tick": {
            "id": sequence,
            "version": sequence,
            "mrid": f"bbo-{sequence}",
            "ts": stamp,
            "bid": [bid[0], bid[1]],
            "ask": [ask[0], ask[1]],
        },
    }


def ws_depth_frame(
    *,
    bids: tuple[tuple[str, str], ...] = (DEPTH_BID,),
    asks: tuple[tuple[str, str], ...] = DEPTH_ASKS,
    sequence: int = 101,
    offset_ms: int = 310,
    venue_ms: int | None = None,
) -> dict:
    """``market.BTC-USDT.depth.size_20`` — a full snapshot of both sides."""
    stamp = _venue_ms(offset_ms) if venue_ms is None else venue_ms
    return {
        "ch": "market.BTC-USDT.depth.size_20",
        "ts": stamp,
        "tick": {
            "id": sequence,
            "version": sequence,
            "mrid": f"depth-{sequence}",
            "ts": stamp,
            "bids": [[price, size] for price, size in bids],
            "asks": [[price, size] for price, size in asks],
        },
    }


def rest_bbo_frame(
    *, sequence: int = 100, offset_ms: int = 300, venue_ms: int | None = None
) -> dict:
    """``/linear-swap-ex/market/bbo`` — one ``ticks`` entry for this contract."""
    stamp = _venue_ms(offset_ms) if venue_ms is None else venue_ms
    return rest_bbo_payload(
        bid_price=BBO_BID[0],
        bid_size=BBO_BID[1],
        ask_price=BBO_ASK[0],
        ask_size=BBO_ASK[1],
        venue_ms=stamp,
        mrid=sequence,
    )


def rest_depth_frame(
    *, sequence: int = 101, offset_ms: int = 310, venue_ms: int | None = None
) -> dict:
    """``/linear-swap-ex/market/depth?type=step0`` — every level as published."""
    stamp = _venue_ms(offset_ms) if venue_ms is None else venue_ms
    return rest_depth_payload(
        bids=[DEPTH_BID],
        asks=list(DEPTH_ASKS),
        venue_ms=stamp,
        mrid=sequence,
    )


class _Frame:
    """A socket frame carrying the topic and receive time the capture needs."""

    def __init__(self, frame: dict, received_at: datetime) -> None:
        self.topic = frame["ch"]
        self.data = frame
        self.received_at = received_at


def captured_ws(
    frame: dict, *, received_ms: int, epoch: str = "conn-1"
) -> QuoteEvent:
    """Parse one socket frame exactly as the live capturer would."""
    message = _Frame(frame, E + timedelta(milliseconds=received_ms))
    parser = parse_ws_bbo if frame["ch"].endswith(".bbo") else parse_ws_depth
    return parser(message, contract_code="BTC-USDT", connection_epoch=epoch)


def captured_rest(
    frame: dict, *, received_ms: int, epoch: str = "conn-1"
) -> QuoteEvent:
    """Parse one REST body exactly as the polling capturer would."""
    parser = parse_rest_bbo if "ticks" in frame else parse_rest_depth
    return parser(
        frame,
        contract_code="BTC-USDT",
        received_at=E + timedelta(milliseconds=received_ms),
        connection_epoch=epoch,
    )


def _transport_of(source: str) -> str:
    """Which transport a declared source *claims*; unlabelled defaults to ws."""
    return "rest" if source == REST_SOURCE else "ws"


def _scenario(
    *,
    contracts: Decimal = Decimal("500"),
    direction: str = "long",
    fee_rate: Decimal = FEE_RATE,
    requested: Decimal | None = None,
    bbo_source: str = WS_SOURCE,
    depth_source: str = WS_SOURCE,
    bbo_frame: dict | None = None,
    depth_frame: dict | None = None,
    depth_levels: tuple[str, ...] | None = None,
):
    """Venue frames → real parsers → store builders → verifier inputs.

    Nothing here hand-writes a normalized row: the prices, sizes, levels, venue
time and sequence the verifier compares against all come out of the capture
parsers, which is what makes a mismatch *mean* something.

    ``depth_levels`` lets a test overwrite the *normalized* ask sizes after the
    frame was parsed — the capture-row defect (raw 100, row 1000) — while the
    stored bytes still say what the venue said.
    """
    bbo_transport = _transport_of(bbo_source)
    depth_transport = _transport_of(depth_source)
    frames = {
        "bbo": bbo_frame
        or (rest_bbo_frame() if bbo_transport == "rest" else ws_bbo_frame()),
        "depth": depth_frame
        or (rest_depth_frame() if depth_transport == "rest" else ws_depth_frame()),
    }
    bbo = (
        captured_rest(frames["bbo"], received_ms=320)
        if bbo_transport == "rest"
        else captured_ws(frames["bbo"], received_ms=320)
    )
    depth = (
        captured_rest(frames["depth"], received_ms=330)
        if depth_transport == "rest"
        else captured_ws(frames["depth"], received_ms=330)
    )
    if depth_levels is not None:
        # Rewrite the *normalized* levels (and the best-size columns that mirror
        # them) while the stored frame keeps saying what the venue said.  This is
        # the capture-row defect: raw 100 contracts, row 1000.
        asks = [level for level in depth.levels if level.side == "ask"]
        bids = [level for level in depth.levels if level.side == "bid"]
        if len(depth_levels) != len(asks):
            raise AssertionError("depth_levels must cover every ask level")
        asks = [
            replace(level, size_contracts=Decimal(size))
            for level, size in zip(asks, depth_levels)
        ]
        depth = replace(
            depth,
            levels=tuple(bids + asks),
            bid_size_contracts=bids[0].size_contracts,
            ask_size_contracts=asks[0].size_contracts,
        )
    result = evaluate_book_fill(
        request=TradeRequest(
            decision_at=E,
            submitted_at=E + timedelta(milliseconds=20),
            contract_code="BTC-USDT",
            direction=direction,
            action="open",
            requested_contracts=requested if requested is not None else contracts,
        ),
        policy=POLICY,
        rule=RULE,
        quotes=[bbo, depth],
    )
    ids = {k: str(_uuid.uuid4()) for k in ("fill", "account", "order", "position", "bbo", "depth", "rule")}
    fee_amount = result.base_qty_btc * result.fill_vwap * fee_rate

    ev = evidence_row(
        fill_id=ids["fill"],
        result=result,
        account_id=ids["account"],
        order_id=ids["order"],
        position_id=ids["position"],
        quote_event_id=ids["bbo"],
        depth_event_id=ids["depth"],
        contract_rule_id=ids["rule"],
        fee_rate=fee_rate,
        fee_amount=fee_amount,
    )
    fill = {
        "fill_id": ids["fill"],
        "order_id": ids["order"],
        "account_id": ids["account"],
        "instrument": "BTC-USDT",
        "side": result.side,
        "price": result.fill_vwap,
        # ``fills.qty`` is a contract count in the book path, exactly as the
        # ledger stores it; the base quantity lives in the evidence row.
        "qty": result.filled_contracts,
        "contract_size": result.contract_size_btc,
        "fee": fee_amount,
        "fee_rate": fee_rate,
        "is_close": False,
        "execute_timestamp": result.executed_at,
    }
    events: dict[str, dict] = {}
    for channel, event, declared in (
        ("bbo", bbo, bbo_source),
        ("depth", depth, depth_source),
    ):
        version = (
            REST_PARSER_VERSION if event.source == REST_SOURCE else WS_PARSER_VERSION
        )
        row = quote_event_row(
            event, raw_payload=frames[channel], parser_version=version
        )
        # What the *runtime* labelled the row; a test may blank or mislabel it.
        row["source"] = declared
        row["quote_event_id"] = ids[channel]
        events[ids[channel]] = row
    return {
        "result": result,
        "ids": ids,
        "evidence": ev,
        "fill": fill,
        "fee_amount": fee_amount,
        "events": events,
        "frames": frames,
        "rules": {
            ids["rule"]: {
                **contract_rule_row(RULE, raw_payload=RULE_PAYLOAD),
                "contract_rule_id": ids["rule"],
            }
        },
        "bbo": bbo,
        "depth": depth,
    }


def _verdict(scenario, **overrides) -> vpe.FillVerdict:
    kwargs = {
        "fill": scenario["fill"],
        "evidence": scenario["evidence"],
        "events": scenario["events"],
        "rules": scenario["rules"],
        "prior_consumption": {},
        "policy": POLICY,
        "gaps": (),
    }
    kwargs.update(overrides)
    return vpe.verify_fill(**kwargs)


def _codes(verdict: vpe.FillVerdict) -> set[str]:
    return set(verdict.reason_codes())


def _non_pass(verdict: vpe.FillVerdict) -> list[vpe.Check]:
    return [c for c in verdict.checks if c.verdict not in (vpe.PASS,)]


# ---------------------------------------------------------------------------
# Tolerances and the verdict algebra
# ---------------------------------------------------------------------------


def test_money_tolerance_is_eight_decimal_places_and_counts_are_exact() -> None:
    assert vpe.money_matches("81675.380000001", "81675.38") is True
    assert vpe.money_matches("81675.38000002", "81675.38") is False
    # Zero tolerance for contracts: one contract short is never a rounding artefact.
    assert vpe.exact_matches(Decimal("499"), Decimal("500")) is False
    assert vpe.exact_matches(Decimal("0.50000000"), Decimal("0.5")) is True


def test_is_whole_rejects_fractional_lots() -> None:
    assert vpe.is_whole(Decimal("500")) is True
    assert vpe.is_whole(Decimal("500.5")) is False


def test_worst_orders_verdicts_and_empty_input_is_unverifiable() -> None:
    ok = vpe.Check("a", vpe.PASS)
    na = vpe.Check("b", vpe.NOT_APPLICABLE)
    unk = vpe.Check("c", vpe.UNVERIFIABLE)
    bad = vpe.Check("d", vpe.FAIL)
    # A non-applicable sample must not downgrade a real pass ...
    assert vpe.worst([na, ok]) == vpe.PASS
    # ... but on its own it is not a pass either.
    assert vpe.worst([na]) == vpe.NOT_APPLICABLE
    assert vpe.worst([unk, ok, na]) == vpe.UNVERIFIABLE
    assert vpe.worst([ok, bad]) == vpe.FAIL
    # No evidence at all is not a pass.
    assert vpe.worst([]) == vpe.UNVERIFIABLE


def test_slippage_is_adverse_for_both_sides() -> None:
    """The defect the verifier must not re-introduce: a sell below touch is slippage."""
    buy = vpe.expected_slippage_bps(Decimal("81675.3"), Decimal("81675.4"), side="buy")
    assert buy > 0
    sell = vpe.expected_slippage_bps(Decimal("81675.3"), Decimal("81675.2"), side="sell")
    assert sell > 0
    assert vpe.expected_slippage_bps(Decimal("81675.3"), Decimal("81675.3"), side="sell") == 0
    # A favourable price is recorded as zero slippage, never as a negative number.
    assert vpe.expected_slippage_bps(Decimal("81675.3"), Decimal("81675.1"), side="buy") == 0


def test_recomputed_ms_matches_the_engine_bit_for_bit() -> None:
    """Both sides quantize from whole microseconds, so claim-vs-fact needs no fudge."""
    for delta_ms in (0, 1, 20, 320, 999, 1000):
        earlier = E
        later = E + timedelta(milliseconds=delta_ms)
        expected = vpe._ms(later - earlier)
        assert expected == Decimal(delta_ms).quantize(Decimal("0.001"))
    assert vpe._ms(E - (E + timedelta(microseconds=1500))) == Decimal("-1.500")


def test_redact_dsn_hides_the_password_only() -> None:
    redacted = vpe.redact_dsn("postgresql://qa:s3cr3t-pw@postgres-qa:5432/wored_qa")
    assert "s3cr3t" not in redacted
    assert redacted.startswith("postgresql://qa:***@postgres-qa:5432/")


def test_duplicate_event_ids_finds_replays() -> None:
    rows = [{"event_id": "a"}, {"event_id": "b"}, {"event_id": "a"}]
    assert vpe.duplicate_event_ids(rows) == ["a"]
    assert vpe.duplicate_event_ids([{"event_id": "a"}, {"event_id": "b"}]) == []


# ---------------------------------------------------------------------------
# Contract-rule provenance: the stored bytes have to reproduce the claim
# ---------------------------------------------------------------------------


def test_rule_provenance_accepts_the_live_provenance_document() -> None:
    """What the collector writes must be what the rule hash commits to.

    Built by the collector's own :func:`build_rule_provenance`, so this is the live
    shape under test rather than a fixture that happens to look like one — the first
    end-to-end trace failed exactly here, with every fill inheriting the bad row.
    """
    rule, payload = captured_rule_provenance()
    row = {"source_payload_hash": rule.source_payload_hash, "raw_payload": payload}
    ok, detail = vpe.rule_provenance_matches(row)
    assert ok is True, detail
    assert detail.startswith("pair(spec,tape)")


def test_rule_provenance_survives_the_jsonb_round_trip() -> None:
    """PostgreSQL re-serialises JSONB, so the check must be content-bound."""
    rule, payload = captured_rule_provenance()
    round_tripped = json.loads(json.dumps(payload))
    assert vpe.rule_provenance_matches(
        {"source_payload_hash": rule.source_payload_hash, "raw_payload": round_tripped}
    )[0] is True
    text = json.dumps(payload)
    assert vpe.rule_provenance_matches(
        {"source_payload_hash": rule.source_payload_hash, "raw_payload": text}
    )[0] is True


def test_rule_provenance_fails_on_tampered_or_placeholder_payloads() -> None:
    rule, payload = captured_rule_provenance()
    claimed = rule.source_payload_hash

    # the venue bytes were swapped after the rule was written
    edited = json.loads(json.dumps(payload))
    edited["contract_spec"]["data"][0]["contract_size"] = 0.01
    edited_spec = vpe.rule_provenance_matches(
        {"source_payload_hash": claimed, "raw_payload": edited}
    )
    assert edited_spec[0] is False
    assert edited_spec[1].startswith(
        "contract_spec bytes do not hash to the digest the document states"
    )

    # the tape was swapped, so the pair commitment no longer holds
    tape_edited = json.loads(json.dumps(payload))
    tape_edited["trade_tape"]["rows"][0]["amount_contracts"] = "0.5"
    edited_tape = vpe.rule_provenance_matches(
        {"source_payload_hash": claimed, "raw_payload": tape_edited}
    )
    assert edited_tape[0] is False
    assert edited_tape[1].startswith(
        "trade_tape rows do not hash to the digest the document states"
    )

    # a debug marker is not provenance, whatever the row claims
    placeholder = vpe.rule_provenance_matches(
        {"source_payload_hash": claimed, "raw_payload": {"fixture": True}}
    )
    assert placeholder[0] is False
    assert placeholder[1].startswith("single-payload digest does not reproduce")


def test_rule_provenance_accepts_a_single_payload_and_an_absent_one() -> None:
    """Legacy rows stored the hashed body directly; a row with no bytes is unchecked."""
    ok, detail = vpe.rule_provenance_matches(
        {"source_payload_hash": canonical_sha256(RULE_PAYLOAD), "raw_payload": RULE_PAYLOAD}
    )
    assert ok is True and detail == "single-payload digest"
    assert vpe.rule_provenance_matches(
        {"source_payload_hash": "0" * 64, "raw_payload": {"contract_spec": {"data": []}}}
    ) == (False, "document omits the contract_spec_sha256 it claims")
    ok, detail = vpe.rule_provenance_matches({"source_payload_hash": "0" * 64, "raw_payload": None})
    assert ok is True and "unchecked" in detail


def test_rule_provenance_document_without_a_tape_is_spec_only() -> None:
    """A documentary rule commits to the spec alone — and the gate blocks sizing."""
    spec = {"data": [{"contract_code": "BTC-USDT", "contract_size": 0.001}]}
    spec_digest = canonical_sha256(spec)
    ok, detail = vpe.rule_provenance_matches(
        {
            "source_payload_hash": spec_digest,
            "raw_payload": {"contract_spec": spec, "contract_spec_sha256": spec_digest},
        }
    )
    assert ok is True and detail.startswith("spec-only document")
    # the pair commitment of a tape-backed row must not be mistaken for a spec digest
    assert vpe.rule_provenance_matches(
        {
            "source_payload_hash": canonical_pair_hash(spec_digest, "a" * 64),
            "raw_payload": {"contract_spec": spec, "contract_spec_sha256": spec_digest},
        }
    )[0] is False


# ---------------------------------------------------------------------------
# The honest path: a real capture reproduces
# ---------------------------------------------------------------------------


def test_a_reproduced_fill_passes_every_check() -> None:
    scenario = _scenario()
    verdict = _verdict(scenario)
    offenders = [c for c in _non_pass(verdict) if c.verdict != vpe.NOT_APPLICABLE]
    assert verdict.verdict == vpe.PASS, [c.as_dict() for c in offenders]
    assert verdict.reason_codes() == ()
    # The fill really did pay depth VWAP, not the touch: the walk spans two levels.
    assert scenario["result"].fill_vwap == Decimal("81675.38")
    assert scenario["result"].touch_price == Decimal("81675.3")
    assert vpe.dec(scenario["evidence"]["slippage_bps"]) > 0


def test_sell_side_fill_also_passes() -> None:
    scenario = _scenario(direction="short")
    verdict = _verdict(scenario)
    offenders = [c for c in _non_pass(verdict) if c.verdict != vpe.NOT_APPLICABLE]
    assert verdict.verdict == vpe.PASS, [c.as_dict() for c in offenders]
    assert scenario["evidence"]["side"] == "sell"


def test_verifier_rederives_the_fee_instead_of_trusting_it() -> None:
    scenario = _scenario()
    assert vpe.money_matches(
        scenario["evidence"]["base_qty_btc"]
        * scenario["evidence"]["fill_vwap"]
        * FEE_RATE,
        scenario["evidence"]["fee_amount"],
    )


# ---------------------------------------------------------------------------
# Each historical cheat must produce a named reason code
# ---------------------------------------------------------------------------


def test_repriced_fill_fails_the_reproduction() -> None:
    """The old runtime could record the touch price as the fill price."""
    scenario = _scenario()
    tampered = dict(scenario["evidence"], fill_vwap=Decimal("81675.3"))
    verdict = _verdict(scenario, evidence=tampered)
    assert verdict.verdict == vpe.FAIL
    assert "VWAP_NOT_REPRODUCIBLE" in _codes(verdict)


def test_invented_liquidity_is_rejected() -> None:
    """TZ: the runtime used to consume levels the book never published."""
    scenario = _scenario()
    levels = json.loads(scenario["evidence"]["levels_used"])
    levels.append({"side": "ask", "price": "81676.0", "size_contracts": "400"})
    verdict = _verdict(
        scenario,
        evidence=dict(scenario["evidence"], levels_used=json.dumps(levels)),
    )
    assert "LEVEL_NOT_IN_CAPTURE" in _codes(verdict)


def test_tampered_capture_payload_changes_the_hash() -> None:
    scenario = _scenario()
    key = scenario["ids"]["depth"]
    events = dict(scenario["events"])
    events[key] = dict(events[key], raw_payload=canonical_json({"tick": {"asks": []}}))
    verdict = _verdict(scenario, events=events)
    assert "RAW_HASH_MISMATCH" in _codes(verdict)


def test_evidence_hash_must_bind_both_captured_events() -> None:
    scenario = _scenario()
    ev = dict(scenario["evidence"], raw_event_sha256="0" * 64)
    verdict = _verdict(scenario, evidence=ev)
    assert "EVIDENCE_HASH_NOT_A_CAPTURE_HASH" in _codes(verdict)


def test_one_now_in_every_timestamp_is_rejected() -> None:
    """The old defect wrote a single wall-clock value into all four columns."""
    scenario = _scenario()
    same = E
    ev = dict(
        scenario["evidence"],
        decision_at=same,
        submitted_at=same,
        eligible_at=same,
        executed_at=same,
    )
    verdict = _verdict(scenario, evidence=ev)
    assert "TIMESTAMPS_ARE_THE_SAME_INSTANT" in _codes(verdict)


def test_non_monotonic_timeline_is_rejected() -> None:
    scenario = _scenario()
    ev = dict(scenario["evidence"], executed_at=E)
    verdict = _verdict(scenario, evidence=ev)
    assert "TIMESTAMPS_NOT_MONOTONIC" in _codes(verdict)


def test_stale_quote_is_a_fail_not_an_absence() -> None:
    """A quote that aged past the budget is stale — never a missing proof.

    The age is measured against the venue stamp the frame published, so the
    budget, not the fixture, is what makes the quote old: the same capture is
    fresh under the day's policy and stale under a ten-millisecond one.
    """
    scenario = _scenario()
    assert "QUOTE_STALE" not in _codes(_verdict(scenario))

    verdict = _verdict(scenario, policy=replace(POLICY, quote_age_max_ms=10))
    assert verdict.verdict == vpe.FAIL
    assert "QUOTE_STALE" in _codes(verdict)
    stale = [c for c in verdict.checks if c.name == "quote_age"]
    assert [c.verdict for c in stale] == [vpe.FAIL]


def test_a_normalized_venue_time_the_payload_does_not_contain_is_rejected() -> None:
    """The row's timestamp is a *claim* about the bytes, not a fact of its own."""
    scenario = _scenario()
    events = dict(scenario["events"])
    row = dict(events[scenario["ids"]["bbo"]])
    row["source_event_at"] = row["source_event_at"] - timedelta(milliseconds=5000)
    row["source_event_at_ms"] = int(row["source_event_at"].timestamp() * 1000)
    events[scenario["ids"]["bbo"]] = row
    verdict = _verdict(scenario, events=events)
    assert "RAW_VENUE_TIME_MISMATCH" in _codes(verdict)
    assert verdict.verdict == vpe.FAIL


def test_recorded_age_that_does_not_match_the_timestamps_is_caught() -> None:
    """The claim is evidence too: an inflated ``quote_age_ms`` must not survive."""
    scenario = _scenario()
    ev = dict(scenario["evidence"], quote_age_ms=Decimal("1.000"))
    verdict = _verdict(scenario, evidence=ev)
    assert "QUOTE_AGE_CLAIM_DIFFERS_FROM_TIMESTAMPS" in _codes(verdict)


def test_fractional_lot_is_rejected() -> None:
    scenario = _scenario()
    ev = dict(
        scenario["evidence"],
        filled_contracts=Decimal("500.5"),
        base_qty_btc=Decimal("0.5005"),
    )
    verdict = _verdict(scenario, evidence=ev)
    assert "FRACTIONAL_LOT" in _codes(verdict)


def test_base_qty_that_is_not_contracts_times_size_is_rejected() -> None:
    scenario = _scenario()
    ev = dict(scenario["evidence"], base_qty_btc=Decimal("0.5"))
    ev["filled_contracts"] = Decimal("600")  # 600 * 0.001 == 0.6, not 0.5
    verdict = _verdict(scenario, evidence=ev)
    assert "BASE_QTY_NOT_CONTRACTS_TIMES_SIZE" in _codes(verdict)


def test_fill_qty_stored_in_base_units_is_rejected() -> None:
    """The units defect P1 fixed, caught from the ledger side.

    0.5 BTC is 500 contracts here.  A ``fills.qty`` that stores the base quantity
    is the legacy shape, and it must fail against its own evidence instead of
    netting quietly into the position row.
    """
    scenario = _scenario()
    verdict = _verdict(scenario, fill=dict(scenario["fill"], qty=Decimal("0.500")))
    codes = _codes(verdict)
    assert "FILL_QTY_DISAGREES_WITH_EVIDENCE" in codes
    assert "FILL_QTY_NOT_IN_CONTRACT_UNITS" in codes


def test_fill_qty_in_contracts_is_the_recognised_convention() -> None:
    """The contract count is what passes, and the identity is checked separately."""
    scenario = _scenario()
    verdict = _verdict(scenario)
    passed = {c.name for c in verdict.checks if c.verdict == vpe.PASS}
    assert {"fill_qty_matches_contracts", "fill_units_convention", "units_identity"} <= passed


def test_contract_size_disagreeing_with_the_proven_rule_is_rejected() -> None:
    scenario = _scenario()
    ev = dict(scenario["evidence"], contract_size_btc=Decimal("0.0001"))
    verdict = _verdict(scenario, evidence=ev)
    assert "CONTRACT_SIZE_DISAGREES_WITH_RULE" in _codes(verdict)


def test_fee_that_is_not_notional_times_rate_is_rejected() -> None:
    """The 1000x-scale fee bug, caught rather than reported as a profit."""
    scenario = _scenario()
    ev = dict(scenario["evidence"], fee_amount=Decimal("0.0204188"))
    verdict = _verdict(scenario, evidence=ev, fill=dict(scenario["fill"], fee=Decimal("0.0204188")))
    assert "FEE_NOT_NOTIONAL_TIMES_RATE" in _codes(verdict)


def test_fill_row_disagreeing_with_evidence_is_rejected() -> None:
    scenario = _scenario()
    verdict = _verdict(
        scenario,
        fill=dict(scenario["fill"], price=Decimal("81675.99"), fee=Decimal("0.01")),
    )
    assert {"FILL_PRICE_DISAGREES_WITH_EVIDENCE", "FEE_DISAGREES_WITH_FILLS_TABLE"} <= _codes(
        verdict
    )


def test_unproven_contract_rule_is_unverifiable_not_passed() -> None:
    scenario = _scenario()
    rules = {
        scenario["ids"]["rule"]: dict(
            scenario["rules"][scenario["ids"]["rule"]],
            validation_status="CONTRACT_RULE_UNVERIFIED",
        )
    }
    verdict = _verdict(scenario, rules=rules)
    assert "CONTRACT_RULE_NOT_PROVEN" in _codes(verdict)


def test_rule_payload_that_does_not_hash_to_its_provenance_is_rejected() -> None:
    scenario = _scenario()
    rules = {
        scenario["ids"]["rule"]: dict(
            scenario["rules"][scenario["ids"]["rule"]],
            raw_payload=canonical_json({"contractSize": 0.002}),
        )
    }
    verdict = _verdict(scenario, rules=rules)
    assert "CONTRACT_RULE_NOT_PROVEN" in _codes(verdict)


def test_missing_evidence_row_is_unverifiable() -> None:
    scenario = _scenario()
    verdict = _verdict(scenario, evidence=None)
    assert verdict.verdict == vpe.UNVERIFIABLE
    assert "NO_EVIDENCE_ROW" in _codes(verdict)


def test_legacy_row_is_unverifiable_rather_than_absolved() -> None:
    scenario = _scenario()
    verdict = _verdict(
        scenario, evidence=dict(scenario["evidence"], verification_status="LEGACY_UNVERIFIED")
    )
    assert verdict.verdict == vpe.UNVERIFIABLE
    assert "LEGACY_UNVERIFIED" in _codes(verdict)


def test_incomplete_evidence_row_never_crashes_the_verifier() -> None:
    """Bad data must produce a verdict, not an exception the operator works around."""
    scenario = _scenario()
    ev = {k: v for k, v in scenario["evidence"].items() if k != "fill_vwap"}
    verdict = _verdict(scenario, evidence=ev)
    assert verdict.verdict == vpe.UNVERIFIABLE
    assert "EVIDENCE_ROW_INCOMPLETE" in _codes(verdict)


def test_evidence_pointing_at_a_missing_quote_is_unverifiable() -> None:
    scenario = _scenario()
    verdict = _verdict(scenario, events={})
    assert verdict.verdict == vpe.UNVERIFIABLE
    assert "EVIDENCE_POINTS_AT_MISSING_QUOTE" in _codes(verdict)


def test_depth_consumed_by_an_earlier_order_cannot_be_taken_twice() -> None:
    """The double-spend defect: replay must see the same ledger the engine saw."""
    scenario = _scenario()
    depth_ref = vpe.quote_reference_for(
        source="htx_public_ws",
        channel="depth",
        contract_code="BTC-USDT",
        connection_epoch="conn-1",
        sequence_no=101,
    )
    consumed = vpe.consumption_key(depth_ref, "buy", Decimal("81675.3"))
    verdict = _verdict(scenario, prior_consumption={consumed: Decimal("100")})
    assert "DEPTH_CONSUMPTION_NOT_REPRODUCIBLE" in _codes(verdict)


def test_feed_gap_during_the_decision_window_invalidates_the_proof() -> None:
    scenario = _scenario()
    gaps = [
        {
            "gap_start_at": scenario["evidence"]["decision_at"] - timedelta(seconds=1),
            "resolved_at": None,
            "reason": "sequence_jump",
        }
    ]
    verdict = _verdict(scenario, gaps=gaps)
    assert verdict.verdict == vpe.UNVERIFIABLE
    assert "FEED_GAP_DURING_DECISION_WINDOW" in _codes(verdict)


def test_bbo_and_depth_from_different_connections_are_rejected() -> None:
    scenario = _scenario()
    events = dict(scenario["events"])
    row = dict(events[scenario["ids"]["depth"]], connection_epoch="conn-2")
    events[scenario["ids"]["depth"]] = row
    verdict = _verdict(scenario, events=events)
    assert (
        "BBO_AND_DEPTH_FROM_DIFFERENT_CONNECTIONS" in _codes(verdict)
        or "REPLAY_REJECTS_THE_RECORDED_FILL" in _codes(verdict)
    )


def test_zero_slippage_claim_on_a_walked_book_is_rejected() -> None:
    scenario = _scenario()
    ev = dict(scenario["evidence"], slippage_bps=Decimal("0"))
    verdict = _verdict(scenario, evidence=ev)
    assert "SLIPPAGE_ZERO_WITH_A_NON_TOUCH_PRICE" in _codes(verdict)
    assert "SLIPPAGE_NOT_REPRODUCIBLE" in _codes(verdict)


def test_close_fill_without_a_reason_is_rejected() -> None:
    scenario = _scenario()
    ev = dict(
        scenario["evidence"],
        open_or_close="close",
        side="sell",
        verification_reason="",
    )
    fill = dict(scenario["fill"], side="sell", is_close=True)
    verdict = _verdict(scenario, evidence=ev, fill=fill)
    assert "CLOSE_WITHOUT_REASON" in _codes(verdict)


# ---------------------------------------------------------------------------
# Position and ledger reconciliation
# ---------------------------------------------------------------------------


def _position(scenario, *, qty: Decimal, status: str = "closed") -> dict:
    closed = scenario["result"].executed_at + timedelta(seconds=1)
    return {
        "position_id": scenario["ids"]["position"],
        "account_id": scenario["ids"]["account"],
        "instrument": "BTC-USDT",
        "side": "long",
        "qty": qty,
        "status": status,
        "opened_at": scenario["result"].executed_at,
        "closed_at": closed if status == "closed" else None,
        "close_price": Decimal("81675.38") if status == "closed" else None,
        "realized_gross_pnl": Decimal("1.0"),
        "realized_net_pnl": Decimal("1.0") - 2 * scenario["fee_amount"],
        "entry_fee": scenario["fee_amount"],
        "exit_fee": scenario["fee_amount"],
        "funding_cashflow": Decimal("0"),
    }


def test_position_nets_and_the_fee_ledger_foots() -> None:
    scenario = _scenario()
    checks = vpe.verify_positions(
        positions=[_position(scenario, qty=Decimal("500"), status="open")],
        fills=[scenario["fill"]],
        evidence_by_fill={scenario["ids"]["fill"]: scenario["evidence"]},
        postings=[
            {"source_type": "fill", "source_ref": scenario["ids"]["fill"],
             "bucket": "entry_fee", "amount": -scenario["fee_amount"]},
        ],
    )
    named = {c.name: c for c in checks}
    assert named["position_net_contracts"].verdict == vpe.PASS
    assert named["position_pnl_identity"].verdict == vpe.PASS
    assert named["ledger_fees"].verdict == vpe.PASS


def test_a_closed_flat_position_also_nets() -> None:
    """open 500 − close 500 ⇒ 0 base left, and that must be PASS not silence."""
    scenario = _scenario()
    close = dict(
        scenario["fill"],
        fill_id=str(_uuid.uuid4()),
        is_close=True,
        # inside the position's own window (closed_at + 1s grace), as a real
        # closing fill would be
        execute_timestamp=scenario["fill"]["execute_timestamp"] + timedelta(milliseconds=500),
    )
    close_ev = dict(scenario["evidence"], fill_id=close["fill_id"], open_or_close="close")
    checks = vpe.verify_positions(
        positions=[_position(scenario, qty=Decimal("0"))],
        fills=[scenario["fill"], close],
        evidence_by_fill={
            scenario["ids"]["fill"]: scenario["evidence"],
            close["fill_id"]: close_ev,
        },
        postings=[],
    )
    named = {c.name: c for c in checks}
    assert named["position_net_contracts"].verdict == vpe.PASS
    assert named["closed_position_is_flat"].verdict == vpe.PASS
    assert named["close_price_recorded"].verdict == vpe.PASS


def test_position_that_does_not_net_is_a_fail() -> None:
    scenario = _scenario()
    checks = vpe.verify_positions(
        positions=[_position(scenario, qty=Decimal("100"), status="open")],
        fills=[scenario["fill"]],
        evidence_by_fill={scenario["ids"]["fill"]: scenario["evidence"]},
        postings=[],
    )
    named = {c.name: c for c in checks}
    assert named["position_net_contracts"].verdict == vpe.FAIL
    assert "POSITION_CONTRACTS_DO_NOT_NET" in named["position_net_contracts"].reason_codes


def test_pnl_that_does_not_foot_is_a_fail() -> None:
    scenario = _scenario()
    position = _position(scenario, qty=Decimal("500"), status="open")
    position["realized_net_pnl"] = position["realized_gross_pnl"]
    checks = vpe.verify_positions(
        positions=[position],
        fills=[scenario["fill"]],
        evidence_by_fill={scenario["ids"]["fill"]: scenario["evidence"]},
        postings=[],
    )
    named = {c.name: c for c in checks}
    assert "POSITION_PNL_DOES_NOT_FOOT" in named["position_pnl_identity"].reason_codes


def test_a_fill_that_belongs_to_no_position_is_reported() -> None:
    scenario = _scenario()
    # An open position has no upper bound and legitimately claims every later fill
    # on its account/instrument, so the orphan must sit before ``opened_at``.
    other = dict(scenario["fill"], fill_id=str(_uuid.uuid4()),
                 execute_timestamp=E - timedelta(hours=1))
    checks = vpe.verify_positions(
        positions=[_position(scenario, qty=Decimal("500"), status="open")],
        fills=[scenario["fill"], other],
        evidence_by_fill={scenario["ids"]["fill"]: scenario["evidence"]},
        postings=[],
    )
    codes = {code for c in checks for code in c.reason_codes}
    assert "FILL_NOT_ATTRIBUTED_TO_ANY_POSITION" in codes


def test_an_open_position_admits_it_has_no_lineage_upper_bound() -> None:
    """Attribution of later fills to an open position is one-sided, so it is N/A."""
    scenario = _scenario()
    checks = vpe.verify_positions(
        positions=[_position(scenario, qty=Decimal("500"), status="open")],
        fills=[scenario["fill"]],
        evidence_by_fill={scenario["ids"]["fill"]: scenario["evidence"]},
        postings=[],
    )
    named = {c.name: c for c in checks}
    assert named["position_lineage_upper_bound"].verdict == vpe.NOT_APPLICABLE
    assert "POSITION_STILL_OPEN_NO_UPPER_BOUND" in named[
        "position_lineage_upper_bound"
    ].reason_codes


def test_a_closed_position_does_not_claim_that_bound() -> None:
    scenario = _scenario()
    checks = vpe.verify_positions(
        positions=[_position(scenario, qty=Decimal("0"), status="closed")],
        fills=[scenario["fill"]],
        evidence_by_fill={scenario["ids"]["fill"]: scenario["evidence"]},
        postings=[],
    )
    assert "position_lineage_upper_bound" not in {c.name for c in checks}


def test_missing_evidence_refuses_the_fee_ledger_comparison() -> None:
    """Booked fees vs a half-populated evidence set proves nothing."""
    scenario = _scenario()
    checks = vpe.verify_positions(
        positions=[_position(scenario, qty=Decimal("500"), status="open")],
        fills=[scenario["fill"]],
        evidence_by_fill={},
        postings=[
            {"source_type": "fill", "source_ref": scenario["ids"]["fill"],
             "bucket": "entry_fee", "amount": -scenario["fee_amount"]},
        ],
    )
    named = {c.name: c for c in checks}
    assert named["ledger_fees"].verdict == vpe.UNVERIFIABLE
    assert "LEDGER_FEES_EVIDENCE_INCOMPLETE" in named["ledger_fees"].reason_codes


def test_overlapping_positions_refuse_a_guess_at_lineage() -> None:
    scenario = _scenario()
    first = _position(scenario, qty=Decimal("0"))
    second = dict(first, position_id=str(_uuid.uuid4()))
    checks = vpe.verify_positions(
        positions=[first, second],
        fills=[scenario["fill"]],
        evidence_by_fill={scenario["ids"]["fill"]: scenario["evidence"]},
        postings=[],
    )
    codes = {code for c in checks for code in c.reason_codes}
    assert "POSITION_FILL_LINEAGE_AMBIGUOUS" in codes


def test_fee_ledger_that_does_not_foot_is_a_fail() -> None:
    scenario = _scenario()
    checks = vpe.verify_positions(
        positions=[_position(scenario, qty=Decimal("500"), status="open")],
        fills=[scenario["fill"]],
        evidence_by_fill={scenario["ids"]["fill"]: scenario["evidence"]},
        postings=[
            {"source_type": "fill", "source_ref": scenario["ids"]["fill"],
             "bucket": "entry_fee", "amount": -Decimal("0.01")},
        ],
    )
    named = {c.name: c for c in checks}
    assert "LEDGER_FEES_DO_NOT_FOOT" in named["ledger_fees"].reason_codes


def test_without_postings_the_fee_ledger_is_n_a_not_passed() -> None:
    scenario = _scenario()
    checks = vpe.verify_positions(
        positions=[_position(scenario, qty=Decimal("500"), status="open")],
        fills=[scenario["fill"]],
        evidence_by_fill={scenario["ids"]["fill"]: scenario["evidence"]},
        postings=[],
    )
    named = {c.name: c for c in checks}
    assert named["ledger_fees"].verdict == vpe.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Day shape
# ---------------------------------------------------------------------------


def _day(scenario, *, state: str = "closed") -> dict:
    return {
        "day_id": str(_uuid.uuid4()),
        "state": state,
        "start_utc": E - timedelta(hours=12),
        "end_utc": E + timedelta(hours=12),
    }


def test_day_shape_accepts_a_closed_day_inside_its_window() -> None:
    scenario = _scenario()
    checks = vpe.verify_day_shape(
        day=_day(scenario),
        fills=[scenario["fill"]],
        positions=[_position(scenario, qty=Decimal("0"))],
    )
    assert all(c.verdict in (vpe.PASS, vpe.NOT_APPLICABLE) for c in checks), [
        c.as_dict() for c in checks
    ]


def test_fill_outside_the_day_window_is_a_fail() -> None:
    scenario = _scenario()
    checks = vpe.verify_day_shape(
        day=_day(scenario),
        fills=[dict(scenario["fill"], execute_timestamp=E + timedelta(days=3))],
        positions=[],
    )
    codes = {code for c in checks for code in c.reason_codes}
    assert "FILL_OUTSIDE_DAY_WINDOW" in codes


def test_open_position_after_day_close_is_a_fail() -> None:
    scenario = _scenario()
    checks = vpe.verify_day_shape(
        day=_day(scenario),
        fills=[scenario["fill"]],
        positions=[_position(scenario, qty=Decimal("500"), status="open")],
    )
    codes = {code for c in checks for code in c.reason_codes}
    assert "OPEN_POSITION_AFTER_DAY_CLOSE" in codes


def test_empty_day_is_reported_as_a_fail_not_as_success() -> None:
    checks = vpe.verify_day_shape(
        day={"day_id": str(_uuid.uuid4()), "state": "closed",
             "start_utc": E, "end_utc": E + timedelta(days=1)},
        fills=[],
        positions=[],
    )
    codes = {code for c in checks for code in c.reason_codes}
    assert "EMPTY_DAY" in codes


def test_unclosed_day_marks_the_close_check_n_a() -> None:
    scenario = _scenario()
    checks = vpe.verify_day_shape(
        day=_day(scenario, state="running"),
        fills=[scenario["fill"]],
        positions=[_position(scenario, qty=Decimal("500"), status="open")],
    )
    named = {c.name: c for c in checks}
    assert named["no_open_position_after_close"].verdict == vpe.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Orchestration and artifacts
# ---------------------------------------------------------------------------


def _ctx(scenario, **overrides) -> dict:
    ev = scenario["evidence"]
    usage = [
        {
            "fill_id": scenario["ids"]["fill"],
            "quote_event_id": scenario["ids"]["depth"],
            "trade_side": "buy",
            "price": Decimal(level["price"]),
            "contracts_used": Decimal(level["size_contracts"]),
            "source": "htx_public_ws",
            "channel": "depth",
            "contract_code": "BTC-USDT",
            "connection_epoch": "conn-1",
            "sequence_no": 101,
        }
        for level in json.loads(ev["levels_used"])
    ]
    ctx = {
        "day": _day(scenario, state="running"),
        "experiment": None,
        "positions": [_position(scenario, qty=Decimal("500"), status="open")],
        "fills": [scenario["fill"]],
        "evidence_by_fill": {scenario["ids"]["fill"]: ev},
        "events": scenario["events"],
        "rules": scenario["rules"],
        "usage": usage,
        "gaps": [],
        "postings": [
            {"source_type": "fill", "source_ref": scenario["ids"]["fill"],
             "bucket": "entry_fee", "amount": -scenario["fee_amount"]},
        ],
        "domain_events": [],
        "instrument": "BTC-USDT",
    }
    ctx.update(overrides)
    return ctx


def test_verify_context_reproduces_a_whole_day_as_pass() -> None:
    scenario = _scenario()
    result = vpe.verify_context(_ctx(scenario))
    assert result["overall"] == vpe.PASS, [
        c.as_dict() for v in result["verdicts"] for c in v.checks if c.verdict == vpe.FAIL
    ]
    assert result["fill_counts"] == {vpe.PASS: 1}
    assert result["policy"].version == scenario["evidence"]["model_version"]
    assert result["policy"].participation_cap == POLICY.participation_cap


def test_verify_context_policy_comes_from_the_recorded_snapshot() -> None:
    """Verifying the past against today's defaults would silently pass bad data."""
    scenario = _scenario()
    snapshot = json.loads(scenario["evidence"]["policy_snapshot"])
    snapshot["quote_age_max_ms"] = 10
    ev = dict(scenario["evidence"], policy_snapshot=json.dumps(snapshot))
    result = vpe.verify_context(_ctx(scenario, evidence_by_fill={scenario["ids"]["fill"]: ev}))
    assert result["policy"].quote_age_max_ms == 10
    assert result["overall"] == vpe.FAIL
    codes = {code for v in result["verdicts"] for code in v.reason_codes()}
    assert "QUOTE_STALE" in codes


def test_verify_context_fails_when_one_fill_is_repriced() -> None:
    scenario = _scenario()
    ev = dict(scenario["evidence"], fill_vwap=Decimal("81675.3"))
    result = vpe.verify_context(_ctx(scenario, evidence_by_fill={scenario["ids"]["fill"]: ev}))
    assert result["overall"] == vpe.FAIL
    assert result["fill_counts"][vpe.FAIL] == 1


def test_verify_context_reports_a_missing_evidence_row_as_unverifiable() -> None:
    scenario = _scenario()
    result = vpe.verify_context(_ctx(scenario, evidence_by_fill={}))
    assert result["overall"] == vpe.UNVERIFIABLE
    assert result["verdicts"][0].verdict == vpe.UNVERIFIABLE


def test_duplicate_depth_consumption_is_caught() -> None:
    scenario = _scenario()
    ctx = _ctx(scenario)
    ctx["usage"] = [*ctx["usage"], *ctx["usage"]]
    result = vpe.verify_context(ctx)
    codes = {code for c in result["structural"] for code in c.reason_codes}
    assert "DUPLICATE_DEPTH_CONSUMPTION" in codes


def test_write_artifacts_produces_the_five_mandated_files(tmp_path: Path) -> None:
    scenario = _scenario()
    ctx = _ctx(scenario)
    ctx["server_version"] = "PostgreSQL 16 (test)"
    result = vpe.verify_context(ctx)
    paths = vpe.write_artifacts(
        tmp_path,
        ctx=ctx,
        result=result,
        day_id=ctx["day"]["day_id"],
        experiment_id=None,
        database_url="postgresql://qa:secret@postgres-qa:5432/wored_qa",
        strict=True,
    )
    assert sorted(paths) == sorted(
        ["manifest", "fills", "positions", "reconciliation", "summary"]
    )
    for path in paths.values():
        assert Path(path).is_file()
    manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))
    assert manifest["read_only"] is True
    assert manifest["strict"] is True
    assert "secret" not in json.dumps(manifest)
    assert manifest["tolerances"]["money_usdt"] == "0.00000001"
    assert manifest["tolerances"]["contracts_and_levels"] == "0"
    assert manifest["execution_policy"]["depth_skew_max_ms"] == 500

    reconciliation = json.loads(
        Path(paths["reconciliation"]).read_text(encoding="utf-8")
    )
    assert reconciliation["overall"] == vpe.PASS
    assert reconciliation["fills"][0]["checks"], "every fill must carry its checks"
    assert isinstance(reconciliation["not_applicable"], list)

    csv_lines = Path(paths["fills"]).read_text(encoding="utf-8").splitlines()
    assert csv_lines[0].startswith("fill_id,order_id,instrument")
    assert len(csv_lines) == 2
    summary = Path(paths["summary"]).read_text(encoding="utf-8")
    assert "## Samples that could not be exercised (N/A, not PASS)" in summary


def test_artifacts_list_unexercised_samples_as_n_a(tmp_path: Path) -> None:
    scenario = _scenario()
    ctx = _ctx(scenario, postings=[])
    result = vpe.verify_context(ctx)
    paths = vpe.write_artifacts(
        tmp_path, ctx=ctx, result=result, day_id=ctx["day"]["day_id"],
        experiment_id=None, database_url="postgresql://qa@h/db", strict=False,
    )
    reconciliation = json.loads(Path(paths["reconciliation"]).read_text(encoding="utf-8"))
    assert any(item["verdict"] == vpe.NOT_APPLICABLE for item in reconciliation["not_applicable"])
    assert f"{vpe.NOT_APPLICABLE}" in Path(paths["summary"]).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI contract: exit codes are part of the protocol
# ---------------------------------------------------------------------------


class _StubConn:
    """Stands in for a read-only asyncpg connection in the CLI tests."""

    def __init__(self, *, database: str = "wored_qa", rows: int = 1) -> None:
        self.database = database
        self.rows = rows
        self.closed = False

    async def fetchval(self, sql: str, *args):
        if "current_database" in sql:
            return self.database
        if "version()" in sql:
            return "PostgreSQL 16 (stub)"
        return self.rows

    async def close(self) -> None:
        self.closed = True


def _coro(value):
    """Wrap a ready value so it can stand in for ``await open_read_only_session(...)``."""

    async def _inner(*_a, **_k):
        return value

    return _inner


def _coro_raises(exc: BaseException):
    async def _inner(*_a, **_k):
        raise exc

    return _inner


def _fake_fetch(ctx):
    async def _inner(_conn, **_kwargs):
        return ctx

    return _inner


def test_usage_error_when_no_database_url_is_available(monkeypatch, capsys) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("WORED_TEST_DATABASE_URL", raising=False)
    assert vpe.main(["--day-id", str(_uuid.uuid4()), "--output-dir", "."]) == 2
    assert "no database URL" in capsys.readouterr().err


def test_usage_error_for_a_missing_output_dir_is_reported(tmp_path, capsys) -> None:
    args = vpe.parse_args(
        ["--day-id", str(_uuid.uuid4()), "--output-dir", str(tmp_path / "nope"),
         "--database-url", "postgresql://qa@h/db"]
    )
    assert asyncio.run(vpe._run(args)) == vpe.EXIT_USAGE_ERROR
    assert "output dir does not exist" in capsys.readouterr().err


def test_usage_error_for_a_non_uuid_day_id(tmp_path) -> None:
    args = vpe.parse_args(
        ["--day-id", "today", "--output-dir", str(tmp_path),
         "--database-url", "postgresql://qa@h/db"]
    )
    assert asyncio.run(vpe._run(args)) == vpe.EXIT_USAGE_ERROR


def test_bad_uuid_is_reported_rather_than_queried(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://qa@h/db")
    assert vpe.main(["--day-id", "not-a-uuid", "--output-dir", str(tmp_path)]) == 2
    assert "must be a UUID" in capsys.readouterr().err


def test_production_database_is_refused_before_any_read(monkeypatch, tmp_path) -> None:
    conn = _StubConn(database="trading")
    seen: list[str] = []

    async def fake_fetch_day_context(_conn, **_kwargs):
        seen.append("fetched")
        raise AssertionError("must not read the production database")

    monkeypatch.setattr(vpe, "open_read_only_session", _coro(conn))
    monkeypatch.setattr(vpe, "fetch_day_context", fake_fetch_day_context)
    args = vpe.parse_args(
        ["--day-id", str(_uuid.uuid4()), "--output-dir", str(tmp_path),
         "--database-url", "postgresql://qa@h/trading"]
    )
    assert asyncio.run(vpe._run(args)) == vpe.EXIT_USAGE_ERROR
    assert seen == []
    assert conn.closed is True


def test_strict_exits_one_on_a_fail_verdict(monkeypatch, tmp_path, capsys) -> None:
    scenario = _scenario()
    ctx = _ctx(scenario)
    ctx["evidence_by_fill"] = {
        scenario["ids"]["fill"]: dict(scenario["evidence"], fill_vwap=Decimal("81675.3"))
    }
    conn = _StubConn()
    monkeypatch.setattr(vpe, "open_read_only_session", _coro(conn))
    monkeypatch.setattr(vpe, "fetch_day_context", _fake_fetch(ctx))
    args = vpe.parse_args(
        ["--day-id", ctx["day"]["day_id"], "--output-dir", str(tmp_path), "--strict",
         "--database-url", "postgresql://qa@h/db"]
    )
    assert asyncio.run(vpe._run(args)) == vpe.EXIT_STRICT_FAILURE
    out = capsys.readouterr().out
    assert "verdict=FAIL" in out
    assert (tmp_path / "reconciliation.json").is_file()
    assert conn.closed is True


def test_strict_exits_zero_when_everything_reproduces(monkeypatch, tmp_path) -> None:
    scenario = _scenario()
    ctx = _ctx(scenario)
    conn = _StubConn()
    monkeypatch.setattr(vpe, "open_read_only_session", _coro(conn))
    monkeypatch.setattr(vpe, "fetch_day_context", _fake_fetch(ctx))
    args = vpe.parse_args(
        ["--day-id", ctx["day"]["day_id"], "--output-dir", str(tmp_path), "--strict",
         "--database-url", "postgresql://qa@h/db"]
    )
    assert asyncio.run(vpe._run(args)) == vpe.EXIT_OK


def test_unstructured_verdict_is_exit_one_even_without_strict(monkeypatch, tmp_path) -> None:
    """Without ``--strict`` the run still succeeds; the report carries the FAIL."""
    scenario = _scenario()
    ctx = _ctx(scenario)
    ctx["evidence_by_fill"] = {
        scenario["ids"]["fill"]: dict(scenario["evidence"], fill_vwap=Decimal("81675.3"))
    }
    monkeypatch.setattr(vpe, "open_read_only_session", _coro(_StubConn()))
    monkeypatch.setattr(vpe, "fetch_day_context", _fake_fetch(ctx))
    args = vpe.parse_args(
        ["--day-id", ctx["day"]["day_id"], "--output-dir", str(tmp_path),
         "--database-url", "postgresql://qa@h/db"]
    )
    assert asyncio.run(vpe._run(args)) == vpe.EXIT_OK


def test_operational_failure_is_exit_two_never_a_trading_verdict(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(vpe, "open_read_only_session", _coro_raises(OSError("database is down")))
    argv = [
        "--day-id", str(_uuid.uuid4()), "--output-dir", str(tmp_path),
        "--database-url", "postgresql://qa@h/db",
    ]
    assert vpe.main(argv) == vpe.EXIT_USAGE_ERROR


def test_missing_day_is_exit_two_and_says_so(monkeypatch, tmp_path) -> None:
    async def fake_fetch(_conn, **_kwargs):
        raise LookupError("day does not exist")

    monkeypatch.setattr(vpe, "open_read_only_session", _coro(_StubConn()))
    monkeypatch.setattr(vpe, "fetch_day_context", fake_fetch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://qa@h/db")
    assert vpe.main(["--day-id", str(_uuid.uuid4()), "--output-dir", str(tmp_path)]) == 2


def test_read_only_session_refuses_an_unsafe_schema_name() -> None:
    """``search_path`` cannot be parameterized, so it is validated, not interpolated."""
    assert vpe._SCHEMA_RE.match("verify_store_1a2b3c4d")
    assert not vpe._SCHEMA_RE.match('public"; DROP TABLE paper_v2_fills; --')
    assert not vpe._SCHEMA_RE.match("1abc")


def test_payload_digest_survives_postgres_jsonb_reserialisation() -> None:
    """``raw_payload`` comes back from JSONB reformatted, so the digest is content-based.

    Hashing the returned string byte-for-byte would fail *every* honest row: the
    verifier would report a tampered capture where nothing is wrong.  Re-parsing and
    canonicalising keeps the check meaningful — it now proves the content, not the
    spacing PostgreSQL chose.
    """
    payload = {"tick": {"bid": 81675.2, "bidSz": 6551, "ask": 81675.3}}
    jsonb_text = json.dumps(payload)  # spaces after separators, insertion order kept
    assert jsonb_text != canonical_json(payload)
    assert vpe.sha256_of_payload(jsonb_text) == canonical_sha256(payload)
    assert vpe.sha256_of_payload(payload) == canonical_sha256(payload)
    tampered = json.dumps({"tick": {"bid": 81675.3, "bidSz": 6551, "ask": 81675.3}})
    assert vpe.sha256_of_payload(tampered) != canonical_sha256(payload)
    # a non-JSON legacy value is hashed as it stands
    assert vpe.sha256_of_payload("raw-bytes") == hashlib.sha256(b"raw-bytes").hexdigest()


# ---------------------------------------------------------------------------
# The real PostgreSQL substrate (TZ §4.4: the verifier reads rows, not fixtures)
#
# Everything above feeds hand-built dicts into the pure functions.  That proves the
# arithmetic but not the loading path: column names, JSONB round-tripping, NUMERIC
# rounding, uuid casts and the read-only session only exist against the database.
# These tests run on the disposable QA schema and skip elsewhere.
# ---------------------------------------------------------------------------

DSN = os.getenv(
    "WORED_TEST_DATABASE_URL", "postgresql://qa:disposable-qa-only@postgres-qa:5432/wored_qa"
)
BASE_SCHEMA = ROOT / "migrations" / "paper_v2_schema.sql"
MARKET_SCHEMA = ROOT / "migrations" / "20261023_verified_paper_market.sql"


@pytest.fixture
async def day_db():
    """A throwaway schema holding the base paper substrate plus the capture tables."""
    if DSN.rstrip("/").endswith("/trading"):
        pytest.fail("refusing to run against the production 'trading' database")
    asyncpg = pytest.importorskip("asyncpg")
    from paper_trading.quote_store import MarketDataStore

    try:
        conn = await asyncpg.connect(DSN)
    except Exception as exc:  # pragma: no cover - зависит от окружения
        pytest.skip(f"QA database not reachable: {exc}")
    schema = f"verify_day_{_uuid.uuid4().hex[:8]}"
    try:
        if await conn.fetchval("SELECT current_database()") == "trading":
            pytest.fail("refusing to run against the production database 'trading'")
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        await conn.execute(f'SET search_path TO "{schema}", public')
        await conn.execute(BASE_SCHEMA.read_text(encoding="utf-8"))
        await conn.execute(MARKET_SCHEMA.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - зависит от окружения
        await conn.close()
        pytest.skip(f"could not prepare QA schema: {exc}")
    pool = await asyncpg.create_pool(
        DSN, min_size=2, max_size=3, server_settings={"search_path": schema}
    )
    try:
        # The store's own default label (``linear_swap_ws_v2``) is not a real
        # parser version, so the fixture pins the version the socket capturer
        # actually declares — otherwise every DB fill would be unverifiable for
        # a labelling reason rather than a pricing one.
        yield schema, conn, MarketDataStore(pool, parser_version=WS_PARSER_VERSION)
    finally:
        await pool.close()
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()


async def _insert(conn, table: str, row: dict) -> None:
    columns = ", ".join(row)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(row)))
    await conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                       *row.values())


async def _capture_day(day_db, scenario, *, postings: bool = True) -> dict:
    """Seed one trading day and capture it through the production store.

    Returns the day id plus the *database* ids of the stored events and rule, which
    is what an evidence row has to reference to be provable.
    """
    _schema, conn, store = day_db
    ids = scenario["ids"]
    result = scenario["result"]
    owner = str(_uuid.uuid4())
    day_id = str(_uuid.uuid4())
    await _insert(conn, "paper_v2_owners",
                  {"owner_id": owner, "display_name": "verify-day"})
    await _insert(conn, "paper_v2_accounts",
                  {"account_id": ids["account"], "owner_id": owner, "kind": "auto"})
    await _insert(
        conn, "paper_v2_days",
        {
            "day_id": day_id,
            "owner_id": owner,
            "state": "running",
            "start_utc": E - timedelta(hours=1),
            "end_utc": E + timedelta(hours=1),
        },
    )
    await _insert(
        conn, "paper_v2_orders",
        {
            "order_id": ids["order"],
            "account_id": ids["account"],
            "day_id": day_id,
            "origin": "auto",
            "actor": "auto",
            "side": "buy",
            "qty": Decimal("500"),
            "state": "filled",
            "filled_qty": result.filled_contracts,
        },
    )
    await _insert(
        conn, "paper_v2_fills",
        {
            "fill_id": ids["fill"],
            "order_id": ids["order"],
            "account_id": ids["account"],
            "execution_quote_id": f"q-{ids['fill']}",
            "instrument": "BTC-USDT",
            "side": result.side,
            "price": result.fill_vwap,
            "qty": result.filled_contracts,
            "contract_size": result.contract_size_btc,
            "fee": scenario["fee_amount"],
            "fee_rate": FEE_RATE,
            "is_close": False,
            "execute_timestamp": result.executed_at,
        },
    )
    await _insert(
        conn, "paper_v2_positions",
        {
            "position_id": ids["position"],
            "account_id": ids["account"],
            "day_id": day_id,
            "instrument": "BTC-USDT",
            "side": "long",
            # contracts, not base BTC: 500 contracts of 0.001 BTC each
            "qty": Decimal("500"),
            "avg_entry_price": result.fill_vwap,
            "status": "open",
            "opened_at": result.executed_at,
        },
    )
    if postings:
        await _insert(
            conn, "paper_v2_postings",
            {
                "posting_id": str(_uuid.uuid4()),
                "account_id": ids["account"],
                "day_id": day_id,
                "event_id": str(_uuid.uuid4()),
                "source_type": "fill",
                "source_ref": ids["fill"],
                "bucket": "entry_fee",
                "amount": -scenario["fee_amount"],
                "occurred_at": result.executed_at,
            },
        )

    rule_id = await store.ensure_contract_rule(RULE, raw_payload=RULE_PAYLOAD, conn=conn)
    bbo_id = await store.record_quote(
        scenario["bbo"], conn=conn, raw_payload=scenario["frames"]["bbo"]
    )
    depth_id = await store.record_quote(
        scenario["depth"], conn=conn, raw_payload=scenario["frames"]["depth"]
    )
    await store.record_fill_evidence(
        fill_id=ids["fill"],
        result=result,
        account_id=ids["account"],
        order_id=ids["order"],
        position_id=ids["position"],
        day_id=day_id,
        quote_db_id=bbo_id,
        depth_db_id=depth_id,
        contract_rule_db_id=rule_id,
        fee_rate=FEE_RATE,
        fee_amount=scenario["fee_amount"],
        conn=conn,
    )
    return {"day_id": day_id, "rule": rule_id, "bbo": bbo_id, "depth": depth_id}


async def _verify(day_db, day_id: str):
    schema, _conn, _store = day_db
    ro = await vpe.open_read_only_session(DSN, search_path=schema)
    try:
        ctx = await vpe.fetch_day_context(ro, day_id=day_id, experiment_id=None)
    finally:
        await ro.close()
    return ctx, vpe.verify_context(ctx)


async def test_a_real_captured_day_verifies_clean(day_db) -> None:
    scenario = _scenario()
    seeded = await _capture_day(day_db, scenario)
    ctx, result = await _verify(day_db, seeded["day_id"])

    assert len(ctx["fills"]) == 1 and len(ctx["evidence_by_fill"]) == 1
    assert result["overall"] == vpe.PASS, _dump(result)
    assert result["fill_counts"].get(vpe.PASS) == 1
    assert not [c for c in result["structural"] if c.verdict in (vpe.FAIL, vpe.UNVERIFIABLE)]


async def test_the_fetch_loads_the_hashes_the_store_wrote(day_db) -> None:
    """The digests must survive the JSONB round-trip, or nothing else is provable."""
    scenario = _scenario()
    seeded = await _capture_day(day_db, scenario)
    ctx, _result = await _verify(day_db, seeded["day_id"])
    ev = ctx["evidence_by_fill"][scenario["ids"]["fill"]]
    bbo = ctx["events"][str(ev["quote_event_id"])]
    depth = ctx["events"][str(ev["depth_event_id"])]
    assert vpe.sha256_of_payload(bbo["raw_payload"]) == str(bbo["raw_payload_sha256"])
    assert vpe.sha256_of_payload(depth["raw_payload"]) == str(depth["raw_payload_sha256"])
    assert str(ev["raw_event_sha256"]) == str(bbo["raw_payload_sha256"]) + str(
        depth["raw_payload_sha256"]
    )


async def test_read_only_session_refuses_to_mutate_the_substrate(day_db) -> None:
    scenario = _scenario()
    await _capture_day(day_db, scenario)
    schema = day_db[0]
    ro = await vpe.open_read_only_session(DSN, search_path=schema)
    try:
        with pytest.raises(Exception, match="read-only"):
            await ro.execute(
                "UPDATE paper_v2_fills SET price = 1 WHERE fill_id = $1::uuid",
                scenario["ids"]["fill"],
            )
    finally:
        await ro.close()


async def test_a_repriced_evidence_row_fails_against_the_database(day_db) -> None:
    """A second fill that claims the touch price after the book was spent is a FAIL."""
    scenario = _scenario()
    seeded = await _capture_day(day_db, scenario)
    _schema, conn, _store = day_db
    ids = scenario["ids"]
    fill_b = str(_uuid.uuid4())
    order_b = str(_uuid.uuid4())
    await _insert(
        conn, "paper_v2_orders",
        {
            "order_id": order_b, "account_id": ids["account"], "day_id": seeded["day_id"],
            "origin": "auto", "actor": "auto", "side": "buy", "qty": Decimal("500"),
            "state": "filled", "filled_qty": Decimal("0.500"),
        },
    )
    when = scenario["result"].executed_at + timedelta(minutes=1)
    await _insert(
        conn, "paper_v2_fills",
        {
            "fill_id": fill_b, "order_id": order_b, "account_id": ids["account"],
            "execution_quote_id": f"q-{fill_b}", "instrument": "BTC-USDT", "side": "buy",
            "price": Decimal("81675.3"), "qty": Decimal("0.500"),
            "contract_size": Decimal("0.001"), "fee": Decimal("20.41882500"),
            "fee_rate": FEE_RATE, "is_close": False, "execute_timestamp": when,
        },
    )
    forged = dict(
        scenario["evidence"],
        fill_id=fill_b,
        order_id=order_b,
        day_id=seeded["day_id"],
        quote_event_id=seeded["bbo"],
        depth_event_id=seeded["depth"],
        contract_rule_id=seeded["rule"],
        executed_at=when,
        committed_at=when,
        fill_vwap=Decimal("81675.3"),
        touch_price=Decimal("81675.3"),
        slippage_bps=Decimal("0"),
        fee_amount=Decimal("20.41882500"),
    )
    await _insert(conn, "paper_v2_execution_evidence", forged)

    _ctx, result = await _verify(day_db, seeded["day_id"])
    verdict = next(v for v in result["verdicts"] if v.fill_id == fill_b)
    assert verdict.verdict == vpe.FAIL
    codes = verdict.reason_codes()
    assert "VWAP_NOT_REPRODUCIBLE" in codes, codes
    assert "SLIPPAGE_ZERO_WITH_A_NON_TOUCH_PRICE" in codes or "DEPTH_CONSUMPTION_NOT_REPRODUCIBLE" in codes
    assert result["overall"] == vpe.FAIL


async def test_a_fill_without_evidence_is_unverifiable_not_a_pass(day_db) -> None:
    """The honest grading: an unprovable fill degrades the day, it does not crash it."""
    scenario = _scenario()
    seeded = await _capture_day(day_db, scenario)
    _schema, conn, _store = day_db
    ids = scenario["ids"]
    fill_b = str(_uuid.uuid4())
    order_b = str(_uuid.uuid4())
    await _insert(
        conn, "paper_v2_orders",
        {
            "order_id": order_b, "account_id": ids["account"], "day_id": seeded["day_id"],
            "origin": "auto", "actor": "auto", "side": "buy", "qty": Decimal("500"),
            "state": "filled", "filled_qty": Decimal("0.500"),
        },
    )
    await _insert(
        conn, "paper_v2_fills",
        {
            "fill_id": fill_b, "order_id": order_b, "account_id": ids["account"],
            "execution_quote_id": f"q-{fill_b}", "instrument": "BTC-USDT", "side": "buy",
            "price": Decimal("81675.38"), "qty": Decimal("0.500"),
            "contract_size": Decimal("0.001"), "fee": Decimal("20.41884500"),
            "fee_rate": FEE_RATE, "is_close": False,
            "execute_timestamp": scenario["result"].executed_at + timedelta(minutes=1),
        },
    )

    _ctx, result = await _verify(day_db, seeded["day_id"])
    verdict = next(v for v in result["verdicts"] if v.fill_id == fill_b)
    assert verdict.verdict == vpe.UNVERIFIABLE
    assert "NO_EVIDENCE_ROW" in verdict.reason_codes()
    assert result["overall"] == vpe.UNVERIFIABLE
    assert vpe.FAIL not in result["fill_counts"]


async def test_record_verdicts_writes_only_the_verification_columns(day_db) -> None:
    """``--record-verdicts`` is the one writable path, and it is a narrow one."""
    scenario = _scenario()
    seeded = await _capture_day(day_db, scenario)
    _schema, conn, _store = day_db
    fill_id = scenario["ids"]["fill"]
    before = await conn.fetchrow(
        "SELECT fill_vwap, fee_amount, raw_event_sha256, verification_status, "
        "verified_at, verifier_run_id FROM paper_v2_execution_evidence WHERE fill_id = $1::uuid",
        fill_id,
    )
    assert before["verification_status"] == "PENDING_VERIFICATION"

    _ctx, result = await _verify(day_db, seeded["day_id"])
    run_id = str(_uuid.uuid4())
    assert await vpe.record_verdicts(DSN, result, run_id, search_path=day_db[0]) == 1

    after = await conn.fetchrow(
        "SELECT fill_vwap, fee_amount, raw_event_sha256, verification_status, "
        "verification_reason, verified_at, verifier_run_id "
        "FROM paper_v2_execution_evidence WHERE fill_id = $1::uuid",
        fill_id,
    )
    assert after["verification_status"] == vpe.PASS
    assert after["fill_vwap"] == before["fill_vwap"]
    assert after["fee_amount"] == before["fee_amount"]
    assert after["raw_event_sha256"] == before["raw_event_sha256"]
    assert after["verifier_run_id"] == run_id and after["verified_at"] is not None
    # A recorded verdict is never silently rewritten by a second run.
    assert await vpe.record_verdicts(DSN, result, str(_uuid.uuid4()),
                                     search_path=day_db[0]) == 0
    assert await conn.fetchval("SELECT count(*) FROM paper_v2_execution_evidence") == 1


async def test_evidence_facts_cannot_be_rewritten_in_place(day_db) -> None:
    """The substrate the verifier trusts is append-only, enforced by the database."""
    scenario = _scenario()
    seeded = await _capture_day(day_db, scenario)
    _schema, conn, _store = day_db
    with pytest.raises(Exception, match="immutable"):
        await conn.execute(
            "UPDATE paper_v2_execution_evidence SET fill_vwap = 1 "
            "WHERE fill_id = $1::uuid",
            scenario["ids"]["fill"],
        )
    with pytest.raises(Exception):
        await conn.execute(
            "DELETE FROM paper_v2_market_events WHERE quote_event_id = $1::uuid",
            seeded["bbo"],
        )


def _dump(result) -> str:
    """Readable failure output: which check said what."""
    lines = []
    for verdict in result["verdicts"]:
        for check in verdict.checks:
            if check.verdict in (vpe.FAIL, vpe.UNVERIFIABLE):
                lines.append(f"fill {check.name}={check.verdict} {check.reason_codes}")
    for check in result["structural"]:
        if check.verdict in (vpe.FAIL, vpe.UNVERIFIABLE):
            lines.append(f"structural {check.name}={check.verdict} {check.reason_codes}")
    return "\n".join(lines) or "no failures"


# ---------------------------------------------------------------------------
# Capture provenance (TZ §5.2): the grade is re-derived from the stored rows
# ---------------------------------------------------------------------------

STRICT_POLICY = replace(
    POLICY, allow_receive_time_ordering=False, require_ws_capture=True
)


def _checks(verdict: vpe.FillVerdict) -> list[tuple[str, str, tuple[str, ...]]]:
    return [(check.name, check.verdict, check.reason_codes) for check in verdict.checks]


def _check_named(verdict: vpe.FillVerdict, name: str) -> vpe.Check:
    return next(check for check in verdict.checks if check.name == name)


def test_a_socket_capture_earns_a_pass_and_reports_book_verified() -> None:
    verdict = _verdict(_scenario())
    assert verdict.verdict == vpe.PASS, _checks(verdict)
    assert verdict.capture_mode == "BOOK_VERIFIED"
    assert _check_named(verdict, "capture_grade").verdict == vpe.PASS


def test_a_rest_reconstruction_is_reported_but_never_earns_a_pass_grade() -> None:
    """A modelled day may still verify — it just cannot claim verified *prices*."""
    scenario = _scenario(bbo_source="htx_public_rest", depth_source="htx_public_rest")
    verdict = _verdict(scenario)
    assert verdict.capture_mode == "REST_MODELLED"
    grade = _check_named(verdict, "capture_grade")
    assert grade.verdict == vpe.NOT_APPLICABLE
    assert "REST_MODELLED" in grade.reason_codes
    # N/A ranks below PASS, so the fill itself still reproduces completely.
    assert verdict.verdict == vpe.PASS, _checks(verdict)


def test_a_rest_reconstruction_fails_a_strict_run() -> None:
    """The policy that demanded socket evidence is the one that judges the day."""
    scenario = _scenario(bbo_source="htx_public_rest", depth_source="htx_public_rest")
    verdict = _verdict(scenario, policy=STRICT_POLICY)
    assert verdict.verdict == vpe.FAIL
    assert "REST_MODELLED_IN_STRICT_RUN" in verdict.reason_codes()
    assert _check_named(verdict, "capture_grade").verdict == vpe.FAIL


def test_a_strict_run_accepts_socket_evidence() -> None:
    verdict = _verdict(_scenario(), policy=STRICT_POLICY)
    assert verdict.capture_mode == "BOOK_VERIFIED"
    assert "REST_MODELLED_IN_STRICT_RUN" not in verdict.reason_codes()
    assert _check_named(verdict, "capture_grade").verdict == vpe.PASS


def test_mixing_transports_leaves_the_source_unproven() -> None:
    """A socket BBO priced against REST depth is neither grade; it is a defect."""
    scenario = _scenario(bbo_source="htx_public_ws", depth_source="htx_public_rest")
    verdict = _verdict(scenario)
    assert verdict.capture_mode == "MIXED_SOURCES"
    assert "CAPTURE_SOURCE_UNPROVEN" in verdict.reason_codes()
    assert verdict.verdict == vpe.FAIL, "a mixed pair is never a verified fill"


def test_an_unlabelled_transport_is_not_silently_trusted() -> None:
    scenario = _scenario(bbo_source="", depth_source="")
    verdict = _verdict(scenario)
    assert verdict.capture_mode == "UNVERIFIABLE"
    assert "CAPTURE_SOURCE_UNPROVEN" in verdict.reason_codes()


def test_policy_snapshot_round_trips_the_capture_requirement() -> None:
    snapshot = STRICT_POLICY.settings_snapshot()
    rebuilt = vpe.policy_from_snapshot(snapshot, STRICT_POLICY.version)
    assert rebuilt.require_ws_capture is True
    assert rebuilt.allow_receive_time_ordering is False
    assert rebuilt.policy_hash(RULE) == STRICT_POLICY.policy_hash(RULE)
    # A historical snapshot without the key is a modelled day, not a strict one.
    legacy = dict(snapshot)
    legacy.pop("require_ws_capture")
    assert vpe.policy_from_snapshot(legacy, POLICY.version).require_ws_capture is False


# ---------------------------------------------------------------------------
# PP-01..PP-08: the acceptance matrix, one row at a time
# ---------------------------------------------------------------------------


def _replayed_events(scenario, **overrides) -> dict[str, dict]:
    """The stored capture rows, copied with per-channel edits applied."""
    events = {key: dict(row) for key, row in scenario["events"].items()}
    for channel, changes in overrides.items():
        events[scenario["ids"][channel]].update(changes)
    return events


def _legacy_decision(scenario, policy: ExecutionPolicy = POLICY) -> dict:
    """The pre-2026-10-31 decisive chain, replicated verbatim.

    That chain was: hash the stored bytes, rebuild both quotes from the
    **normalized** columns with ``event_from_row`` and re-price them through
    ``evaluate_book_fill``.  Because the query and the arithmetic were the same
    code the runtime had already used, a capture whose normalized columns did not
    match its own payload replayed perfectly and reported PASS.  The replica lives
    here — not in the verifier — so that false PASS stays reproducible on demand
    after the verifier stopped taking that route.
    """
    ev = scenario["evidence"]
    request = TradeRequest(
        decision_at=ev["decision_at"],
        submitted_at=ev["submitted_at"],
        contract_code="BTC-USDT",
        direction="long" if str(ev["side"]) == "buy" else "short",
        action=str(ev["open_or_close"]),
        requested_contracts=ev["requested_contracts"],
        order_id=str(ev["order_id"]),
    )
    engine = evaluate_book_fill(
        request=request,
        policy=policy,
        rule=RULE,
        quotes=[
            vpe.event_from_row(scenario["events"][scenario["ids"][channel]])
            for channel in ("bbo", "depth")
        ],
        already_used={},
        require_depth_vwap=policy.slippage_mode == "depth_vwap",
    )
    return {
        "status": engine.status,
        "contracts": engine.filled_contracts,
        "fill_vwap": engine.fill_vwap,
        "agrees_with_evidence": (
            engine.fill_vwap is not None
            and vpe.money_matches(engine.fill_vwap, ev["fill_vwap"])
            and vpe.exact_matches(engine.filled_contracts, ev["filled_contracts"])
        ),
    }


def test_the_verifier_parsers_are_not_the_collector_parsers() -> None:
    """A verifier that reuses the capturer's code inherits the capturer's bugs."""
    source = Path(vpe.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert not [name for name in imported if name.startswith("collector")], imported
    assert vpe._parse_ws_bbo is not parse_ws_bbo
    assert vpe._parse_ws_depth is not parse_ws_depth
    assert vpe._parse_rest_bbo is not parse_rest_bbo
    assert vpe._parse_rest_depth is not parse_rest_depth
    # The decisive walk is not the executor entry point either.
    assert vpe.independent_depth_walk is not vpe.evaluate_book_fill


def test_pp01_a_real_ws_capture_proves_a_price_and_a_rest_one_only_replays() -> None:
    """PP-01: real shapes on both transports; only the socket earns strict proof."""
    socket = _verdict(_scenario())
    assert socket.verdict == vpe.PASS, _checks(socket)
    assert socket.strict_pass is True
    assert socket.venue_time_proven is True

    rest = _verdict(_scenario(bbo_source=REST_SOURCE, depth_source=REST_SOURCE))
    assert rest.verdict == vpe.PASS, _checks(rest)
    assert rest.strict_pass is False
    assert rest.capture_mode == "REST_MODELLED"


def test_pp02_the_old_chain_passes_the_inflated_normalized_ask_size() -> None:
    """PP-02 red-before: raw ask 100, normalized ask 1000, old chain says PASS.

    The stored frame is untouched and still hashes to its own digest; only the
    normalized column — the one the engine priced from — was inflated, and the
    fill plus its evidence were rebuilt from that false number.  This is the
    defect the gate exists to close.
    """
    scenario = _scenario(depth_levels=("1000", "10000"))
    for channel in ("bbo", "depth"):
        row = scenario["events"][scenario["ids"][channel]]
        assert vpe.sha256_of_payload(row["raw_payload"]) == row["raw_payload_sha256"]
    assert scenario["frames"]["depth"]["tick"]["asks"][0][1] == "100"

    legacy = _legacy_decision(scenario)
    assert legacy["status"] == "FILLED", legacy
    assert legacy["agrees_with_evidence"] is True, legacy
    assert legacy["fill_vwap"] == Decimal("81675.3")

    verdict = _verdict(scenario)
    assert verdict.verdict == vpe.FAIL, _checks(verdict)
    assert "RAW_NORMALIZED_DEPTH_MISMATCH" in _codes(verdict)
    assert "VWAP_NOT_REPRODUCIBLE" in _codes(verdict)
    assert "DEPTH_CONSUMPTION_NOT_REPRODUCIBLE" in _codes(verdict)
    assert "BOOK_VISIBLE_NOT_REPRODUCIBLE" in _codes(verdict)
    assert verdict.strict_pass is False
    # The independent walk priced the *raw* book, two levels deep.
    assert "81675.38" in _check_named(verdict, "reproduced_vwap").detail


def test_pp03_a_normalized_bbo_price_the_payload_does_not_contain_is_rejected() -> None:
    """PP-03: the best ask column is a claim about the bytes, not a fact."""
    scenario = _scenario()
    events = _replayed_events(scenario, bbo={"ask_price": Decimal("81675.25")})
    verdict = _verdict(scenario, events=events)
    assert verdict.verdict == vpe.FAIL
    assert "RAW_NORMALIZED_BBO_MISMATCH" in _codes(verdict)
    assert _check_named(verdict, "raw_bbo_values_bbo").verdict == vpe.FAIL


def test_pp04_a_used_level_larger_than_the_published_liquidity_is_rejected() -> None:
    """PP-04: the price existed, but not that much of it."""
    scenario = _scenario()
    ev = dict(
        scenario["evidence"],
        levels_used=[{"side": "ask", "price": "81675.3", "size_contracts": "500"}],
    )
    verdict = _verdict(scenario, evidence=ev)
    assert verdict.verdict == vpe.FAIL
    assert "DEPTH_CONSUMPTION_NOT_REPRODUCIBLE" in _codes(verdict)
    detail = _check_named(verdict, "reproduced_levels").detail
    assert "recorded=ask@81675.3:500" in detail, detail


def test_pp05_a_fill_and_its_evidence_that_agree_on_a_wrong_vwap_still_fail() -> None:
    """PP-05: the executor and the evidence can be wrong together.

    Every stored number is made consistent with the touch price, so the fill row,
    its evidence, its fee and its notional all foot with each other — and the
    independent walk over the raw book still disagrees.
    """
    scenario = _scenario()
    wrong = Decimal("81675.3")
    fee = Decimal("20.418825")  # 0.5 BTC x 81675.3 x 0.0005
    ev = dict(
        scenario["evidence"],
        fill_vwap=wrong,
        slippage_bps=Decimal("0"),
        fee_amount=fee,
    )
    fill = dict(scenario["fill"], price=wrong, fee=fee)
    verdict = _verdict(scenario, evidence=ev, fill=fill)
    assert verdict.verdict == vpe.FAIL, _checks(verdict)
    assert "VWAP_NOT_REPRODUCIBLE" in _codes(verdict)
    assert _check_named(verdict, "fill_price_matches_fills_table").verdict == vpe.PASS
    assert _check_named(verdict, "fee_amount").verdict == vpe.PASS
    assert _check_named(verdict, "zero_slippage_only_at_touch").verdict == vpe.PASS
    assert "walk=81675.38" in _check_named(verdict, "reproduced_vwap").detail


def test_pp05_an_ioc_limit_the_evidence_version_carries_is_enforced() -> None:
    """PP-05: a limit is priced when the version stores it, never assumed."""
    scenario = _scenario()
    book_v1 = vpe.evidence_policy_checks(scenario["evidence"], side="buy")
    assert [c.verdict for c in book_v1] == [vpe.PASS, vpe.NOT_APPLICABLE]
    assert "IOC_LIMIT_NOT_IN_EVIDENCE_VERSION" in book_v1[-1].reason_codes

    unstated = vpe.evidence_policy_checks(
        dict(scenario["evidence"], model_version="book_v2_ioc_limit_v1"), side="buy"
    )
    assert [c.verdict for c in unstated] == [vpe.PASS, vpe.UNVERIFIABLE]
    assert "EVIDENCE_POLICY_INPUT_MISSING" in unstated[-1].reason_codes

    breached = vpe.evidence_policy_checks(
        dict(
            scenario["evidence"],
            model_version="book_v2_ioc_limit_v1",
            ioc_limit_price=Decimal("81675.30"),  # vwap 81675.38 is worse
        ),
        side="buy",
    )
    assert [c.verdict for c in breached] == [vpe.PASS, vpe.FAIL]
    assert "IOC_LIMIT_BREACHED" in breached[-1].reason_codes

    unknown = vpe.evidence_policy_checks(
        dict(scenario["evidence"], model_version="book_v9"), side="buy"
    )
    assert [c.verdict for c in unknown] == [vpe.UNVERIFIABLE]
    assert "EVIDENCE_POLICY_VERSION_UNKNOWN" in unknown[0].reason_codes


def test_pp05_an_unknown_evidence_version_leaves_the_fill_unverifiable() -> None:
    scenario = _scenario()
    ev = dict(scenario["evidence"], model_version="book_v9")
    verdict = _verdict(scenario, evidence=ev)
    assert verdict.verdict == vpe.UNVERIFIABLE
    assert "EVIDENCE_POLICY_VERSION_UNKNOWN" in _codes(verdict)
    assert verdict.strict_pass is False


def test_pp06_a_fabricated_parser_version_is_unverifiable_not_relabelled() -> None:
    """PP-06: no collector ever emitted ``linear_swap_ws_v2``."""
    scenario = _scenario()
    events = _replayed_events(
        scenario,
        bbo={"parser_version": LEGACY_FAKE_VERSION},
        depth={"parser_version": LEGACY_FAKE_VERSION},
    )
    verdict = _verdict(scenario, events=events)
    assert verdict.verdict == vpe.UNVERIFIABLE, _checks(verdict)
    assert "RAW_PARSER_VERSION_UNKNOWN" in _codes(verdict)
    assert verdict.strict_pass is False


def test_pp06_a_known_version_on_a_transport_it_never_ran_on_is_a_fail() -> None:
    """PP-06: ``htx_linear_swap_ws_v1`` was never a REST parser."""
    scenario = _scenario()
    events = _replayed_events(scenario, depth={"source": REST_SOURCE})
    verdict = _verdict(scenario, events=events)
    assert verdict.verdict == vpe.FAIL
    assert "RAW_PARSER_VERSION_AMBIGUOUS" in _codes(verdict)


def test_pp06_a_row_without_its_capture_bytes_is_unverifiable() -> None:
    """PP-06: missing raw material is missing proof, not a tampered capture."""
    scenario = _scenario()
    events = _replayed_events(scenario, depth={"raw_payload": None})
    verdict = _verdict(scenario, events=events)
    assert verdict.verdict == vpe.UNVERIFIABLE, _checks(verdict)
    assert "RAW_PAYLOAD_MISSING" in _codes(verdict)
    assert verdict.strict_pass is False


def test_pp06_a_depth_without_a_venue_time_never_claims_strict_proof() -> None:
    """PP-06: a receive-time-only depth replays numerically, no more than that."""
    frame = ws_depth_frame()
    del frame["tick"]["ts"]
    verdict = _verdict(_scenario(depth_frame=frame))
    assert verdict.verdict == vpe.PASS, _checks(verdict)
    assert verdict.venue_time_proven is False
    assert verdict.strict_pass is False
    venue = _check_named(verdict, "raw_venue_time_depth")
    assert venue.verdict == vpe.NOT_APPLICABLE
    assert "VENUE_TIME_ABSENT_IN_RAW_AND_ROW__RECEIVE_TIME_ONLY" in venue.reason_codes


def test_pp07_prior_fills_that_over_consume_one_raw_level_fail() -> None:
    """PP-07: two fills cannot take more of a level than the venue published."""
    scenario = _scenario()
    depth_row = scenario["events"][scenario["ids"]["depth"]]
    ledger_id = vpe.quote_reference_for(
        source=depth_row["source"],
        channel=depth_row["channel"],
        contract_code=depth_row["contract_code"],
        connection_epoch=depth_row.get("connection_epoch"),
        sequence_no=depth_row.get("sequence_no"),
    )
    key = vpe.consumption_key(ledger_id, "buy", "81675.3")
    verdict = _verdict(scenario, prior_consumption={key: Decimal("500")})
    assert verdict.verdict == vpe.FAIL
    assert "RAW_LEVEL_OVER_CONSUMED_BY_PRIOR_FILLS" in _codes(verdict)


def test_pp08_a_book_v1_row_is_priced_by_its_own_version() -> None:
    """PP-08: the historical version keeps its semantics, no retro-fitting."""
    verdict = _verdict(_scenario())
    assert _check_named(verdict, "evidence_policy_version").verdict == vpe.PASS
    # ``book_v1`` predates the IOC-limit column: N/A by version, not assumed.
    assert _check_named(verdict, "ioc_limit").verdict == vpe.NOT_APPLICABLE

    readable = vpe.RAW_PARSER_VERSIONS
    assert readable[("htx_linear_swap_ws_v1", WS_SOURCE, "bbo")] == "ws_bbo_tick_pair_v1"
    assert readable[("htx_linear_swap_ws_v1", WS_SOURCE, "depth")] == "ws_depth_snapshot_v1"
    assert readable[("htx_linear_swap_rest_v1", REST_SOURCE, "bbo")] == "rest_bbo_ticks_v1"
    assert readable[("htx_linear_swap_rest_v1", REST_SOURCE, "depth")] == "rest_depth_tick_v1"
    for (version, source, channel), shape in readable.items():
        assert vpe.SHAPE_SOURCE[shape] == source, (version, channel)

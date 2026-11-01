"""Capture HTX public BBO/depth as immutable evidence before anyone trades on it.

The verified paper experiment (ТЗ §4, RFC §5) forbids the old behaviour: take the
freshest numbers in memory, invent a quote id, stamp three local clocks.  This
module splits the market data into two halves.

* **Substrate** — one row per captured event in ``paper_v2_market_events``, with
  the raw venue payload and its SHA-256, the venue's own millisecond timestamp
  when the venue published one, the local receive time, the connection epoch and
  the venue sequence number.  Written once, never updated.
* **Pointer** — a small ``book_v1`` block published into the Redis snapshot: the
  database UUIDs, the receive times and the digests.  It carries no prices, so a
  reader cannot trade on the pointer alone; execution reloads the rows and
  re-verifies the digests (``paper_trading.book_path``).

Fail-closed is the whole design.  If a capture cannot be persisted, the pointer
is *not* published, and the runner refuses a new fill with
``NO_BOOK_CAPTURE_IN_SNAPSHOT`` instead of pricing an order off a memory value.
A missing migration, a dead REST call or a malformed tick all end the same way:
no evidence, no trade.

This transport is **REST polling**, so everything it produces is labelled
``REST_MODELLED``: a poll observes a book at two instants, not the whole stream
between them, and the remediation ТЗ §5.2 forbids such a reconstruction from
earning a strict PASS.  The socket transport lives in ``ws_book_capture``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence
from uuid import uuid4

from paper_trading.book_execution import BookLevel, QuoteEvent, capture_mode_for_source
from paper_trading.contract_rules import (
    ContractRule,
    TradeSample,
    attest_rule_with_trade_tape,
    rule_from_contract_info,
)
from paper_trading.quote_store import MarketDataStore, canonical_sha256

log = logging.getLogger(__name__)

#: Identity of the venue feed this capture came from.  Part of the unique event
#: key, so it must never be reused for a different transport or parser.
SOURCE = "htx_public_rest"
#: Grade published with every pointer from this transport.  A consumer that wants
#: socket-grade evidence must refuse it (``ExecutionPolicy.require_ws_capture``).
CAPTURE_MODE = "REST_MODELLED"
CHANNEL_BBO = "bbo"
CHANNEL_DEPTH = "depth"
BBO_PATH = "/linear-swap-ex/market/bbo"
DEPTH_PATH = "/linear-swap-ex/market/depth"
CONTRACT_INFO_PATH = "/linear-swap-api/v1/swap_contract_info"
#: The venue publishes no order-size step on ``swap_contract_info``, so the lot
#: granularity is proven from what it actually executed instead (see
#: ``contract_rules.attest_rule_with_trade_tape``).
HISTORY_TRADE_PATH = "/linear-swap-ex/market/history/trade"
TRADE_TAPE_SIZE = 100
#: Stored with every event: a later parser fix must be tellable apart from the
#: bytes this version interpreted.
PARSER_VERSION = "htx_linear_swap_rest_v1"
#: The pointer schema the execution gate expects (``paper_trading.market``).
BOOK_CAPTURE_SCHEMA = "book_v1"
DEFAULT_BASE_URL = "https://api.hbdm.com"
#: 20 aggregated levels is the documented public maximum for one REST call.
DEFAULT_DEPTH_LEVELS = 20
#: Consecutive failed polls before the book is declared gapped.  One blip is
#: noise; a run of them means the depth this contract is quoting is not there.
GAP_AFTER_CONSECUTIVE_FAILURES = 2


class BookCaptureError(RuntimeError):
    """A public payload cannot be captured as an immutable market event."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dec(value: Any, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BookCaptureError(f"{field}: invalid decimal") from exc
    if not result.is_finite() or result < 0:
        raise BookCaptureError(f"{field}: must be a non-negative finite decimal")
    return result


def ms_to_utc(value: Any, field: str) -> datetime:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError) as exc:
        raise BookCaptureError(f"{field}: invalid millisecond timestamp") from exc
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)


def level_pair(raw: Any, field: str) -> tuple[Decimal, Decimal]:
    """One ``[price, size_in_contracts]`` array, validated."""
    if not isinstance(raw, (list, tuple)) or len(raw) < 2:
        raise BookCaptureError(f"{field}: expected [price, size]")
    price = _dec(raw[0], f"{field}[0]")
    size = _dec(raw[1], f"{field}[1]")
    if price <= 0:
        raise BookCaptureError(f"{field}: price must be positive")
    return price, size


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.astimezone(timezone.utc).isoformat()


def contract_spec_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Strip the response envelope so the rule hash tracks the *spec*, not the clock.

    ``swap_contract_info`` re-stamps ``ts`` on every poll.  Hashing the whole body
    would mint a fresh contract-rule row several thousand times a day, which would
    make the deduped provenance meaningless; the ``data`` object is the statement
    of the rules and is byte-stable while the venue spec is unchanged.
    """
    return {"data": payload.get("data", payload)}


def samples_from_trade_payload(
    payload: Mapping[str, Any], *, contract_code: str
) -> list[TradeSample]:
    """Flatten ``history/trade`` into venue-quoted (contracts, base, price) triples.

    ``amount`` is the size in contracts and ``quantity`` the same size in base
    asset; both come from the venue, which is what makes the cross-check worth
    more than a documentation page.  Rows carry no ``contract_code`` — the request
    is contract-scoped — so a row is kept when the field is absent and dropped
    only when it names a different instrument.

    This endpoint prints numbers as JSON floats, so every value goes through
    ``Decimal(str(...))``: the shortest round-tripping decimal the venue meant,
    never binary arithmetic on a price.
    """
    chunks = payload.get("data")
    if not isinstance(chunks, list):
        raise BookCaptureError("history/trade: expected a list of trade windows")
    samples: list[TradeSample] = []
    for chunk in chunks:
        rows = chunk.get("data") if isinstance(chunk, Mapping) else None
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                continue
            row_code = str(row.get("contract_code") or "").upper()
            if row_code and row_code != contract_code.upper():
                continue
            samples.append(
                TradeSample(
                    amount_contracts=_dec(row.get("amount"), f"trade[{index}].amount"),
                    quantity_base=_dec(row.get("quantity"), f"trade[{index}].quantity"),
                    price_usdt=_dec(row.get("price"), f"trade[{index}].price"),
                )
            )
    return samples


def trade_tape_digest(samples: Sequence[TradeSample]) -> str:
    """Hash the exact triples the attestation read, in venue order."""
    return canonical_sha256(
        {
            "rows": [
                [
                    format(sample.amount_contracts, "f"),
                    format(sample.quantity_base, "f"),
                    format(sample.price_usdt, "f"),
                ]
                for sample in samples
            ]
        }
    )


def trade_tape_audit(samples: Sequence[TradeSample]) -> dict[str, Any]:
    """The tape as stored beside the rule: enough to re-run the proof offline."""
    return {
        "sample_count": len(samples),
        "digest": trade_tape_digest(samples),
        "rows": [
            {
                "amount_contracts": format(sample.amount_contracts, "f"),
                "quantity_base": format(sample.quantity_base, "f"),
                "price_usdt": format(sample.price_usdt, "f"),
            }
            for sample in samples
        ],
    }


def rule_provenance_payload(
    spec: Mapping[str, Any],
    samples: Sequence[TradeSample] | None,
) -> dict[str, Any]:
    """The bytes stored with a contract rule: exactly what its hash claims to cover.

    ``paper_v2_contract_rules.raw_payload`` is not a debug field.  An independent
    verifier re-hashes these bytes and requires the result to reproduce the row's
    ``source_payload_hash``; if the stored payload were some other shape, every fill
    pointing at the row would fail provenance.  So the document and the hash are
    produced together here (:func:`build_rule_provenance`).
    """
    payload: dict[str, Any] = {
        "contract_spec": dict(spec),
        "contract_spec_sha256": canonical_sha256(spec),
    }
    if samples is not None:
        payload["trade_tape"] = trade_tape_audit(samples)
    return payload


def build_rule_provenance(
    contract_payload: Mapping[str, Any],
    *,
    contract_code: str,
    observed_at: datetime,
    source_uri: str,
    tape_payload: Mapping[str, Any] | None,
    tape_source_uri: str,
) -> tuple[ContractRule, dict[str, Any]]:
    """Return ``(rule, payload to store)`` for one contract-spec poll.

    The rule is built from the venue's own specification and, when a tape is
    available, promoted by ``attest_rule_with_trade_tape`` — whose provenance hash is
    the pair commitment of spec and tape digests.  The returned payload carries those
    same two sources, so the stored row can be re-derived from its own bytes.

    A missing tape is a *missing proof*, not a contradiction: the rule stays
    ``DOCUMENTARY_ONLY``, the payload says honestly that no tape was read, and the
    execution gate keeps refusing new fills.
    """
    spec = contract_spec_payload(contract_payload)
    rule = rule_from_contract_info(
        spec,
        contract_code=contract_code,
        observed_at=observed_at,
        source_uri=source_uri,
        source_payload_hash=canonical_sha256(spec),
    )
    if tape_payload is None:
        return rule, rule_provenance_payload(spec, None)
    samples = samples_from_trade_payload(tape_payload, contract_code=contract_code)
    # A tape that contradicts the stored rule is fatal: the venue is executing a
    # shape this rule cannot describe.
    rule = attest_rule_with_trade_tape(
        rule,
        samples,
        source_uri=tape_source_uri,
        source_payload_hash=trade_tape_digest(samples),
    )
    return rule, rule_provenance_payload(spec, samples)


# ---------------------------------------------------------------------------
# Pure parsing — one payload in, one immutable event out
# ---------------------------------------------------------------------------


def parse_bbo_payload(
    payload: Mapping[str, Any],
    *,
    contract_code: str,
    received_at: datetime,
    connection_epoch: str,
) -> QuoteEvent:
    """``/linear-swap-ex/market/bbo`` → one best-quote event.

    ``tick.ts`` is the venue's own millisecond time and is stored as
    ``source_event_at``; ``mrid`` is the venue sequence.  Sizes are contract
    counts, which is what the venue publishes for this channel (RFC §2).
    """
    if payload.get("status") != "ok":
        raise BookCaptureError("bbo: HTX status is not ok")
    ticks = payload.get("ticks")
    if not isinstance(ticks, list) or not ticks:
        raise BookCaptureError("bbo: ticks array is missing")
    tick: Mapping[str, Any] | None = None
    for candidate in ticks:
        if isinstance(candidate, Mapping) and str(
            candidate.get("contract_code", "")
        ).upper() == contract_code.upper():
            tick = candidate
            break
    if tick is None:
        raise BookCaptureError(f"bbo: no tick for {contract_code}")

    bid_price, bid_size = level_pair(tick.get("bid"), "bbo.bid")
    ask_price, ask_size = level_pair(tick.get("ask"), "bbo.ask")
    if bid_price > ask_price:
        raise BookCaptureError("bbo: bid exceeds ask (crossed)")
    if tick.get("ts") is None:
        raise BookCaptureError("bbo.ts: venue timestamp is missing")

    sequence = tick.get("mrid")
    return QuoteEvent(
        quote_event_id=f"rest-{CHANNEL_BBO}-{sequence}",
        source=SOURCE,
        channel=CHANNEL_BBO,
        contract_code=contract_code.upper(),
        received_at=received_at,
        source_event_at=ms_to_utc(tick.get("ts"), "bbo.ts"),
        connection_epoch=connection_epoch,
        sequence_no=(None if sequence is None else int(sequence)),
        version_no=(None if tick.get("version") is None else int(str(tick["version"]))),
        mrid=(None if sequence is None else str(sequence)),
        bid_price=bid_price,
        bid_size_contracts=bid_size,
        ask_price=ask_price,
        ask_size_contracts=ask_size,
        levels=(
            BookLevel(side="bid", price=bid_price, size_contracts=bid_size),
            BookLevel(side="ask", price=ask_price, size_contracts=ask_size),
        ),
        raw_payload_sha256=canonical_sha256(dict(payload)),
        parser_version=PARSER_VERSION,
        quality_status="live",
    )


def parse_depth_payload(
    payload: Mapping[str, Any],
    *,
    contract_code: str,
    received_at: datetime,
    connection_epoch: str,
) -> QuoteEvent:
    """``/linear-swap-ex/market/depth?type=step0`` → one aggregated depth event.

    Every level is stored exactly as published, on both sides, so a later walk is
    reproducible from the row.  ``tick.ts`` is the venue millisecond time; if a
    response ever omits it the event is stored with ``source_event_at=NULL`` and
    an explicit reason rather than a local clock dressed up as exchange time.
    """
    if payload.get("status") != "ok":
        raise BookCaptureError("depth: HTX status is not ok")
    tick = payload.get("tick")
    if not isinstance(tick, Mapping):
        raise BookCaptureError("depth: tick object is missing")

    levels: list[BookLevel] = []
    for side, key in (("bid", "bids"), ("ask", "asks")):
        rows = tick.get(key)
        if not isinstance(rows, list):
            raise BookCaptureError(f"depth.{key}: expected an array of levels")
        for index, raw in enumerate(rows):
            price, size = level_pair(raw, f"depth.{key}[{index}]")
            levels.append(BookLevel(side=side, price=price, size_contracts=size))
    if not any(level.side == "bid" for level in levels) or not any(
        level.side == "ask" for level in levels
    ):
        raise BookCaptureError("depth: both sides must be present")

    best_bid = max((level.price for level in levels if level.side == "bid"))
    best_ask = min((level.price for level in levels if level.side == "ask"))
    if best_bid > best_ask:
        raise BookCaptureError("depth: bid exceeds ask (crossed)")

    sequence = tick.get("mrid")
    raw_ts = tick.get("ts")
    source_event_at = ms_to_utc(raw_ts, "depth.ts") if raw_ts is not None else None
    source_time_reason = (
        None if source_event_at is not None else "DEPTH_TS_ABSENT__RECEIVE_TIME_ONLY"
    )
    bid_size = next(
        (level.size_contracts for level in levels if level.side == "bid"), None
    )
    ask_size = next(
        (level.size_contracts for level in levels if level.side == "ask"), None
    )
    return QuoteEvent(
        quote_event_id=f"rest-{CHANNEL_DEPTH}-{sequence}",
        source=SOURCE,
        channel=CHANNEL_DEPTH,
        contract_code=contract_code.upper(),
        received_at=received_at,
        source_event_at=source_event_at,
        source_time_reason=source_time_reason,
        connection_epoch=connection_epoch,
        sequence_no=(None if sequence is None else int(sequence)),
        version_no=(None if tick.get("version") is None else int(str(tick["version"]))),
        mrid=(None if sequence is None else str(sequence)),
        bid_price=best_bid,
        bid_size_contracts=bid_size,
        ask_price=best_ask,
        ask_size_contracts=ask_size,
        levels=tuple(levels),
        raw_payload_sha256=canonical_sha256(dict(payload)),
        parser_version=PARSER_VERSION,
        quality_status="live",
    )


def rule_block_from_rule(rule: ContractRule, rule_id: str) -> dict[str, Any]:
    """The snapshot's ``contract_rule`` block: the stored id plus the units.

    ``rule_id`` is a PostgreSQL UUID — the pointer the execution gate resolves.
    The explicit ``*_contracts`` fields travel beside it so a reader can tell
    what ``quantity_step`` means instead of guessing from ``contract_size``.
    """
    return {
        "rule_id": str(rule_id),
        "venue": rule.venue,
        "market": rule.market,
        "contract_code": rule.contract_code,
        "contract_size_btc_per_contract": format(rule.contract_size_btc_per_contract, "f"),
        "price_tick_usdt": format(rule.price_tick_usdt, "f"),
        "order_qty_unit": rule.order_qty_unit,
        "order_qty_step_contracts": format(rule.order_qty_step_contracts, "f"),
        "order_qty_min_contracts": format(rule.order_qty_min_contracts, "f"),
        "book_qty_unit": rule.book_qty_unit,
        "contract_rule_status": rule.validation_status,
        "rule_version": rule.rule_version,
        "source_uri": rule.source_uri,
        "source_payload_hash": rule.source_payload_hash,
        "observed_at": _iso(rule.observed_at),
    }


def capture_pointer_block(
    bbo: QuoteEvent,
    depth: QuoteEvent,
    *,
    captured_at: datetime | None = None,
    source: str = SOURCE,
    capture_mode: str = CAPTURE_MODE,
) -> dict[str, Any]:
    """The ``book`` block: references only, never prices.

    ``received_at`` and ``raw_payload_sha256`` are what the gate checks before it
    trusts a row: a pointer whose digest differs from the stored bytes is a
    ``CAPTURE_DIGEST_MISMATCH`` block, not a fill.

    ``source``/``capture_mode`` are parameters so both transports publish the
    identical block shape; the socket capturer passes its own values rather than
    growing a second copy of this layout that could drift.
    """
    stamp = captured_at or _utc_now()
    return {
        "schema": BOOK_CAPTURE_SCHEMA,
        "contract_code": bbo.contract_code.upper(),
        "captured_at": _iso(stamp) or "",
        "source": source,
        "capture_mode": capture_mode,
        "connection_epoch": bbo.connection_epoch,
        "bbo": {
            "event_id": bbo.quote_event_id,
            "channel": CHANNEL_BBO,
            "received_at": _iso(bbo.received_at) or "",
            "source_event_at": _iso(bbo.source_event_at),
            "sequence_no": bbo.sequence_no,
            "raw_payload_sha256": bbo.raw_payload_sha256,
            "quality": bbo.quality_status,
            "parser_version": bbo.parser_version,
        },
        "depth": {
            "event_id": depth.quote_event_id,
            "channel": CHANNEL_DEPTH,
            "received_at": _iso(depth.received_at) or "",
            "source_event_at": _iso(depth.source_event_at),
            "source_time_reason": depth.source_time_reason,
            "sequence_no": depth.sequence_no,
            "level_count": len(depth.levels),
            "raw_payload_sha256": depth.raw_payload_sha256,
            "quality": depth.quality_status,
            "parser_version": depth.parser_version,
        },
    }


# ---------------------------------------------------------------------------
# The capturer: REST → PostgreSQL → pointer blocks for the snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BookCapture:
    """One successfully persisted pair, ready to be published."""

    book: dict[str, Any]
    contract_rule: dict[str, Any]
    bbo: QuoteEvent
    depth: QuoteEvent
    rule_id: str

    @property
    def bid_size_contracts(self) -> Decimal:
        return self.bbo.bid_size_contracts or Decimal(0)

    @property
    def ask_size_contracts(self) -> Decimal:
        return self.bbo.ask_size_contracts or Decimal(0)


def snapshot_capture_blocks(capture: BookCapture) -> dict[str, Any]:
    """The keys one captured poll adds to the published snapshot.

    ``bid_size``/``ask_size`` are contract counts copied straight from the same
    BBO bytes that were stored, so the dashboard top-of-book and the evidence row
    cannot drift apart.  ``quantity_step`` comes from the persisted rule rather
    than from ``contract_size`` — that substitution was the original units defect.
    """
    rule = capture.contract_rule
    blocks: dict[str, Any] = {
        "book": capture.book,
        "contract_rule": rule,
        "bid_size": format(capture.bid_size_contracts, "f"),
        "ask_size": format(capture.ask_size_contracts, "f"),
    }
    step = rule.get("order_qty_step_contracts")
    if step:
        blocks["quantity_step"] = str(step)
        blocks["quantity_step_source"] = "contract_rule"
    return blocks


async def capture_contract_rule(
    *,
    store: MarketDataStore,
    client: Any,
    contract_code: str,
    contract_payload: Mapping[str, Any],
    base_url: str,
    cache: dict[str, tuple[str, dict[str, Any]]],
) -> tuple[str, dict[str, Any]]:
    """Persist (once) and return ``(rule_id, snapshot block)`` for this spec.

    The rule is keyed by the contract-spec hash, so a venue specification that
    has not changed reuses the stored row instead of growing a copy per poll.
    The lot step is attested from the public trade tape: ``swap_contract_info``
    does not publish one, so a rule that never saw the tape stays
    ``DOCUMENTARY_ONLY`` and the execution gate keeps refusing new fills.

    The rule and the payload stored with it come from one place
    (:func:`build_rule_provenance`), because the stored bytes have to reproduce
    the row's ``source_payload_hash`` for the independent verifier to accept any
    fill that points at it.

    Split out of :class:`HtxBookCapturer` so the socket transport proves its lot
    rules through the same code path: two implementations of this would be two
    ways to disagree about the multiplier.
    """
    spec_digest = canonical_sha256(contract_spec_payload(contract_payload))
    cached = cache.get(spec_digest)
    if cached is not None:
        return cached

    contract_uri = f"{base_url}{CONTRACT_INFO_PATH}?contract_code={contract_code}"
    tape_uri = f"{base_url}{HISTORY_TRADE_PATH}?contract_code={contract_code}"
    tape_payload: Mapping[str, Any] | None = None
    try:
        response = await client.get(
            f"{base_url}{HISTORY_TRADE_PATH}",
            params={"contract_code": contract_code, "size": TRADE_TAPE_SIZE},
        )
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict):
            tape_payload = body
    except BookCaptureError as exc:
        # No tape is a missing proof, not a contradiction: publish the honest
        # documentary rule so the gate blocks with CONTRACT_RULE_UNVERIFIED.
        log.warning("trade tape unavailable for %s: %s", contract_code, exc)
    except Exception as exc:  # noqa: BLE001 - absence of evidence, never a guess
        log.warning("trade tape fetch failed for %s: %s", contract_code, exc)

    rule, audit = build_rule_provenance(
        contract_payload,
        contract_code=contract_code,
        observed_at=_utc_now(),
        source_uri=contract_uri,
        tape_payload=tape_payload,
        tape_source_uri=tape_uri,
    )
    rule_id = await store.ensure_contract_rule(rule, raw_payload=audit)
    block = rule_block_from_rule(rule, rule_id)
    cache[spec_digest] = (rule_id, block)
    log.debug(
        "contract rule captured for %s: %s (%s, step %s contracts)",
        contract_code,
        rule_id,
        rule.validation_status,
        block["order_qty_step_contracts"],
    )
    return rule_id, block


class HtxBookCapturer:
    """Poll public BBO/depth, persist them, hand back the snapshot pointers.

    One ``connection_epoch`` per process, shared by both channels: the execution
    engine refuses a BBO/depth pair from two different connections, and REST
    polling is one connection from the engine's point of view.
    """

    def __init__(
        self,
        contract_code: str,
        *,
        store: MarketDataStore,
        base_url: str = DEFAULT_BASE_URL,
        depth_levels: int = DEFAULT_DEPTH_LEVELS,
        epoch: str | None = None,
    ) -> None:
        self.contract_code = contract_code.upper()
        self.store = store
        self.base_url = base_url.rstrip("/")
        self.depth_levels = int(depth_levels)
        self.epoch = epoch or f"rest-{uuid4().hex[:16]}"
        self._rule_cache: dict[str, tuple[str, dict[str, Any]]] = {}
        self._failures = 0
        self._open_gap_ids: dict[str, str] = {}
        self.last_error = ""

    # -- HTTP ---------------------------------------------------------------

    async def _get(self, client: Any, path: str, **params: Any) -> dict[str, Any]:
        response = await client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise BookCaptureError(f"{path}: expected JSON object")
        return payload

    async def _timed_get(
        self, client: Any, path: str, **params: Any
    ) -> tuple[dict[str, Any], datetime]:
        """Fetch one public payload and stamp *its* arrival instant.

        Each channel gets its own receive time because the two HTTP round-trips
        genuinely finish at different moments.  Stamping both with the time the
        ``gather`` returned would hide the arrival skew the policy measures and
        would be a clock claim the collector cannot support.
        """
        payload = await self._get(client, path, **params)
        return payload, _utc_now()

    # -- contract rules -----------------------------------------------------

    async def rule_for(
        self, client: Any, contract_payload: Mapping[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Persist (once) and return ``(rule_id, snapshot block)`` for this spec."""
        return await capture_contract_rule(
            store=self.store,
            client=client,
            contract_code=self.contract_code,
            contract_payload=contract_payload,
            base_url=self.base_url,
            cache=self._rule_cache,
        )

    # -- capture ------------------------------------------------------------

    async def capture(self, client: Any, contract_payload: Mapping[str, Any]) -> BookCapture:
        """Fetch, persist and pointer one BBO/depth pair.

        Raises :class:`BookCaptureError` when anything is unusable — the caller
        must then publish a snapshot *without* the ``book`` block, which blocks a
        new fill instead of trading on an unprovable quote.
        """
        try:
            rule_id, rule_block = await self.rule_for(client, contract_payload)
            # One poll of one pair: both channels are fetched together, and each
            # one keeps its own arrival instant.
            started = _utc_now()
            (bbo_raw, bbo_received), (depth_raw, depth_received) = await asyncio.gather(
                self._timed_get(client, BBO_PATH, contract_code=self.contract_code),
                self._timed_get(
                    client,
                    DEPTH_PATH,
                    contract_code=self.contract_code,
                    depth=self.depth_levels,
                    type="step0",
                ),
            )
            bbo = parse_bbo_payload(
                bbo_raw,
                contract_code=self.contract_code,
                received_at=bbo_received,
                connection_epoch=self.epoch,
            )
            depth = parse_depth_payload(
                depth_raw,
                contract_code=self.contract_code,
                received_at=depth_received,
                connection_epoch=self.epoch,
            )
            bbo_db_id = await self.store.record_quote(bbo, raw_payload=dict(bbo_raw))
            depth_db_id = await self.store.record_quote(
                depth, raw_payload=dict(depth_raw)
            )
            # The pointer describes the *stored rows*, never this poll's objects.
            # ``record_quote`` is idempotent on (source, channel, contract, epoch,
            # sequence_no), and the venue keeps that id while the top of book is
            # unchanged, so a quiet moment resolves to the row of the observation
            # that first proved it — whose bytes predate this poll's envelope
            # timestamp.  A pointer built from the poll would then claim a digest the
            # row does not carry and the gate would refuse the pair as
            # ``CAPTURE_DIGEST_MISMATCH``: an integrity alarm raised for what really
            # happened is "the book did not change".  Reading the rows back makes the
            # pointer unable to lie, and fail-closed twice over — a row that cannot
            # be read back publishes no evidence at all.
            stored = await self.store.load_quote_events([bbo_db_id, depth_db_id])
            bbo_row = stored.get(str(bbo_db_id))
            depth_row = stored.get(str(depth_db_id))
            if bbo_row is None or depth_row is None:
                missing = bbo_db_id if bbo_row is None else depth_db_id
                raise BookCaptureError(
                    f"stored capture {missing} did not read back: publishing no "
                    "book block rather than an unverifiable pointer"
                )
            capture = BookCapture(
                book=capture_pointer_block(
                    bbo_row,
                    depth_row,
                    captured_at=started,
                    source=bbo_row.source,
                    capture_mode=capture_mode_for_source(bbo_row.source),
                ),
                contract_rule=rule_block,
                bbo=bbo_row,
                depth=depth_row,
                rule_id=rule_id,
            )
            await self._heal_gaps()
            self._failures = 0
            self.last_error = ""
            return capture
        except Exception as exc:
            self._failures += 1
            self.last_error = str(exc) or type(exc).__name__
            await self._note_failure()
            raise

    # -- feed quality -------------------------------------------------------

    async def _note_failure(self) -> None:
        """Record a capture gap once the failures are a run, not a blip."""
        if self._failures < GAP_AFTER_CONSECUTIVE_FAILURES:
            return
        for channel in (CHANNEL_BBO, CHANNEL_DEPTH):
            if channel in self._open_gap_ids:
                continue
            try:
                self._open_gap_ids[channel] = await self.store.record_feed_gap(
                    source=SOURCE,
                    channel=channel,
                    contract_code=self.contract_code,
                    detected_at=_utc_now(),
                    reason="rest_capture_failed",
                    connection_epoch=self.epoch,
                    detail=self.last_error[:500],
                    entry_blocked=True,
                )
                log.warning(
                    "book capture gap on %s/%s after %d failures: %s",
                    self.contract_code,
                    channel,
                    self._failures,
                    self.last_error,
                )
            except Exception as exc:  # noqa: BLE001 — the gap row is best-effort
                log.warning("could not record %s capture gap: %s", channel, exc)

    async def _heal_gaps(self) -> None:
        """A successful capture resolves the incident it follows."""
        for channel, gap_id in list(self._open_gap_ids.items()):
            try:
                await self.store.resolve_gap(gap_id, resolved_at=_utc_now())
                log.info(
                    "book capture recovered on %s/%s (gap %s resolved)",
                    self.contract_code,
                    channel,
                    gap_id,
                )
            except Exception as exc:  # noqa: BLE001 — retried on the next success
                log.warning("could not resolve %s capture gap: %s", channel, exc)
                continue
            self._open_gap_ids.pop(channel, None)


def build_capturer(
    contract_code: str,
    *,
    base_url: str | None = None,
    pool: Any = None,
) -> HtxBookCapturer | None:
    """Construct the capturer, or ``None`` when the substrate is unavailable.

    The collector must keep publishing its market snapshot even when the capture
    store cannot be built: the dashboard still works, while the runner refuses
    new fills — which is the correct degradation, not a silent one.
    """
    if pool is None:
        return None
    return HtxBookCapturer(
        contract_code,
        # The store now takes the parser version from the event itself (QD-13);
        # PARSER_VERSION is passed as a fallback for events that declare none —
        # the label always names the REST parser that actually produced them.
        store=MarketDataStore(pool, parser_version=PARSER_VERSION),
        base_url=(base_url or os.getenv("HTX_LINEAR_SWAP_BASE_URL", DEFAULT_BASE_URL)).strip(),
    )

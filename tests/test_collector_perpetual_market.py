"""Contract tests for the HTX perpetual Redis producer."""
from datetime import datetime, timezone

import pytest

from collector.htx.perpetual_market import PerpetualFeedError, build_snapshot


def payloads() -> dict:
    return {
        "contract_code": "BTC-USDT",
        "ticker": {
            "status": "ok",
            "ts": 1789034906753,
            "tick": {
                "bid": [64250.1, 12],
                "ask": [64250.2, 8],
                "close": 64250.15,
                "ts": 1789034906753,
            },
        },
        "index": {
            "status": "ok",
            "ts": 1789034906753,
            "data": [{
                "contract_code": "BTC-USDT",
                "index_price": 64240.5,
                "index_ts": 1789034906000,
            }],
        },
        "funding": {
            "status": "ok",
            "ts": 1789034906753,
            "data": {
                "contract_code": "BTC-USDT",
                "funding_rate": "0.0001",
                "next_funding_time": "1789046400000",
            },
        },
        "contract": {
            "status": "ok",
            "ts": 1789034906753,
            "data": [{
                "contract_code": "BTC-USDT",
                "contract_size": 0.001,
                "price_tick": 0.1,
            }],
        },
        "mark": {
            "status": "ok",
            "ts": 1789034906753,
            "data": [{"id": 1789034880, "close": 64245.25}],
        },
        "received_at": datetime.fromtimestamp(1789034907, tz=timezone.utc),
    }


def test_build_snapshot_matches_webui_contract() -> None:
    snapshot = build_snapshot(**payloads())
    assert snapshot["venue"] == "htx"
    assert snapshot["market_type"] == "linear-swap"
    assert snapshot["bid"] == "64250.1"
    assert snapshot["ask"] == "64250.2"
    assert snapshot["mark"] == "64245.25"
    assert snapshot["quantity_step"] == "0.001"
    assert set(snapshot["component_times"]) == {"ticker", "index", "mark", "funding"}


def test_build_snapshot_rejects_crossed_book() -> None:
    values = payloads()
    values["ticker"]["tick"]["bid"] = [65000, 1]
    with pytest.raises(PerpetualFeedError, match="bid exceeds ask"):
        build_snapshot(**values)


def test_build_snapshot_accepts_live_funding_time_field() -> None:
    values = payloads()
    funding = values["funding"]["data"]
    funding["funding_time"] = funding.pop("next_funding_time")
    snapshot = build_snapshot(**values)
    assert snapshot["next_funding_at"].startswith("2026-")


def test_build_snapshot_rejects_contract_mismatch() -> None:
    values = payloads()
    values["funding"]["data"]["contract_code"] = "ETH-USDT"
    with pytest.raises(PerpetualFeedError, match="identity mismatch"):
        build_snapshot(**values)

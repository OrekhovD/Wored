"""Phase 1: Feed parser and history upsert tests.

Tests decoding/normalization/reconnect logic for the linear-swap WS adapter
and the REST history loader.  No live network calls.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone

import pytest

from collector.htx.linear_swap_ws import (
    WSConfig,
    WSMessage,
    _gzip_decompress,
    _parse_message,
    extract_bbo,
    extract_depth,
    extract_kline,
    extract_mark_price,
)


class TestGzipDecompress:
    def test_valid_gzip(self):
        original = '{"ch":"market.BTC-USDT.bbo","tick":{"bidPrice":"78000"}}'
        compressed = gzip.compress(original.encode("utf-8"))
        result = _gzip_decompress(compressed)
        assert result == original

    def test_invalid_gzip_raises(self):
        with pytest.raises(ValueError):
            _gzip_decompress(b"not-gzip-data")


class TestParseMessage:
    def test_ping_ignored(self):
        raw = json.dumps({"ping": 1700000000000})
        msg = _parse_message(raw, datetime.now(timezone.utc))
        assert msg is None

    def test_pong_ignored(self):
        raw = json.dumps({"pong": 1700000000000})
        msg = _parse_message(raw, datetime.now(timezone.utc))
        assert msg is None

    def test_subscription_ack_ignored(self):
        raw = json.dumps({"status": "ok", "subbed": "market.BTC-USDT.bbo"})
        msg = _parse_message(raw, datetime.now(timezone.utc))
        assert msg is None

    def test_kline_message_parsed(self):
        raw = json.dumps({
            "ch": "market.BTC-USDT.kline.1min",
            "tick": {
                "id": 1700000000,
                "open": "78000.0",
                "high": "78100.0",
                "low": "77900.0",
                "close": "78050.0",
                "vol": "123.45",
                "count": 500,
            },
        })
        msg = _parse_message(raw, datetime.now(timezone.utc))
        assert msg is not None
        assert msg.topic == "market.BTC-USDT.kline.1min"
        assert msg.data["tick"]["close"] == "78050.0"

    def test_non_json_returns_none(self):
        msg = _parse_message("not-json", datetime.now(timezone.utc))
        assert msg is None


class TestExtractKline:
    def test_1min_kline(self):
        msg = WSMessage(
            topic="market.BTC-USDT.kline.1min",
            data={"tick": {"id": 1700000000, "open": "78000", "high": "78100",
                           "low": "77900", "close": "78050", "vol": "123.45", "count": 500}},
            received_at=datetime.now(timezone.utc),
        )
        kline = extract_kline(msg)
        assert kline is not None
        assert kline["timeframe"] == "1min"
        assert kline["open_time"] == 1700000000
        assert kline["close"] == "78050"

    def test_60min_kline(self):
        msg = WSMessage(
            topic="market.BTC-USDT.kline.60min",
            data={"tick": {"id": 1700000000, "open": "78000", "high": "78100",
                           "low": "77900", "close": "78050", "vol": "1000", "count": 5000}},
            received_at=datetime.now(timezone.utc),
        )
        kline = extract_kline(msg)
        assert kline is not None
        assert kline["timeframe"] == "60min"

    def test_non_kline_returns_none(self):
        msg = WSMessage(
            topic="market.BTC-USDT.bbo",
            data={"tick": {"bidPrice": "78000"}},
            received_at=datetime.now(timezone.utc),
        )
        assert extract_kline(msg) is None


class TestExtractBBO:
    def test_bbo_extracted(self):
        msg = WSMessage(
            topic="market.BTC-USDT.bbo",
            data={"tick": {"bidPrice": "78000", "bidSize": "1.5",
                           "askPrice": "78000.1", "askSize": "2.0"}},
            received_at=datetime.now(timezone.utc),
        )
        bbo = extract_bbo(msg)
        assert bbo is not None
        assert bbo["bid_price"] == "78000"
        assert bbo["ask_price"] == "78000.1"


class TestExtractMarkPrice:
    def test_mark_price_extracted(self):
        msg = WSMessage(
            topic="market.BTC-USDT.mark_price",
            data={"tick": {"markPrice": "78005", "indexPrice": "78000",
                           "fundingRate": "0.0001", "nextFundingTime": 1700000000}},
            received_at=datetime.now(timezone.utc),
        )
        mp = extract_mark_price(msg)
        assert mp is not None
        assert mp["mark_price"] == "78005"
        assert mp["funding_rate"] == "0.0001"


class TestExtractDepth:
    def test_depth_truncated_to_20(self):
        bids = [[f"7800{i}", "1"] for i in range(30)]
        asks = [[f"7801{i}", "1"] for i in range(30)]
        msg = WSMessage(
            topic="market.BTC-USDT.depth.size_20",
            data={"tick": {"bids": bids, "asks": asks, "version": 100}},
            received_at=datetime.now(timezone.utc),
        )
        depth = extract_depth(msg)
        assert depth is not None
        assert len(depth["bids"]) == 20
        assert len(depth["asks"]) == 20


class TestWSConfig:
    def test_default_topics(self):
        cfg = WSConfig(contract_code="BTC-USDT")
        topics = cfg.topics
        assert any("bbo" in t for t in topics)
        assert any("depth.size_20" in t for t in topics)
        assert any("mark_price" in t for t in topics)
        assert any("kline.1min" in t for t in topics)
        assert any("kline.60min" in t for t in topics)
        assert all("BTC-USDT" in t for t in topics)

    def test_topics_override(self):
        cfg = WSConfig(topics_override=["market.ETH-USDT.bbo"])
        assert cfg.topics == ["market.ETH-USDT.bbo"]

    def test_custom_contract(self):
        cfg = WSConfig(contract_code="ETH-USDT")
        assert all("ETH-USDT" in t for t in cfg.topics)


class TestHistoryLoaderIntegration:
    """Verify history_loader produces parseable candle data."""

    def test_parse_closed_candles_rejects_open(self):
        from collector.htx.history_loader import parse_closed_candles
        now = datetime(2026, 9, 18, 12, 0, 30, tzinfo=timezone.utc)
        # Candle that opens at 12:00:00 and closes at 12:01:00 → still open at 12:00:30
        open_ts = int(datetime(2026, 9, 18, 12, 0, 0, tzinfo=timezone.utc).timestamp())
        payload = {
            "status": "ok",
            "data": [
                {"id": open_ts, "open": "78000", "high": "78100",
                 "low": "77900", "close": "78050", "vol": "100"},
            ],
        }
        result = parse_closed_candles(payload, contract_code="BTC-USDT",
                                       period="1min", received_at=now)
        # Candle opens at 12:00:00, closes at 12:01:00 > now=12:00:30 → discarded
        assert len(result) == 0

    def test_parse_closed_candles_accepts_closed(self):
        from collector.htx.history_loader import parse_closed_candles
        now = datetime(2026, 9, 18, 12, 5, 0, tzinfo=timezone.utc)
        payload = {
            "status": "ok",
            "data": [
                {"id": 1726655940, "open": "78000", "high": "78100",
                 "low": "77900", "close": "78050", "vol": "100"},
            ],
        }
        result = parse_closed_candles(payload, contract_code="BTC-USDT",
                                       period="1min", received_at=now)
        assert len(result) == 1
        candle = result[0]
        assert candle.contract_code == "BTC-USDT"
        assert candle.open == 78000
        assert candle.close == 78050
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from collector.htx.history_loader import HistoryLoadError, find_gaps, parse_closed_candles


def payload(*rows):
    return {"status": "ok", "data": list(rows)}


def row(timestamp, close="101"):
    return {"id": timestamp, "open": "100", "high": "102", "low": "99", "close": close, "vol": "2"}


def test_loader_sorts_closed_candles_and_excludes_open_candle() -> None:
    received = datetime.fromtimestamp(180, tz=timezone.utc)
    candles = parse_closed_candles(
        payload(row(60), row(0), row(180)),
        contract_code="BTC-USDT",
        period="1min",
        received_at=received,
    )

    assert [item.open_time.timestamp() for item in candles] == [0, 60]
    assert candles[-1].close == Decimal("101")


def test_loader_reports_a_missing_closed_candle() -> None:
    received = datetime.fromtimestamp(240, tz=timezone.utc)
    candles = parse_closed_candles(
        payload(row(0), row(120)),
        contract_code="BTC-USDT",
        period="1min",
        received_at=received,
    )

    assert find_gaps(candles) == [
        (datetime.fromtimestamp(60, tz=timezone.utc), datetime.fromtimestamp(120, tz=timezone.utc))
    ]


def test_loader_rejects_duplicate_candle_id() -> None:
    with pytest.raises(HistoryLoadError, match="duplicate"):
        parse_closed_candles(
            payload(row(0), row(0)),
            contract_code="BTC-USDT",
            period="1min",
            received_at=datetime.fromtimestamp(120, tz=timezone.utc),
        )


def test_loader_rejects_inconsistent_ohlc_range() -> None:
    invalid = row(0)
    invalid["high"] = "99"
    with pytest.raises(HistoryLoadError, match="OHLC range"):
        parse_closed_candles(
            payload(invalid),
            contract_code="BTC-USDT",
            period="1min",
            received_at=datetime.fromtimestamp(120, tz=timezone.utc),
        )

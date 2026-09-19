import json
from decimal import Decimal

import pytest

from paper_trading.runner import RedisMarketDataSource


class Redis:
    async def get(self, key):
        assert key == "market:perpetual:htx:candles:BTC-USDT:1min"
        return json.dumps(
            [
                {
                    "open_time": "2026-01-01T00:00:00+00:00",
                    "close_time": "2026-01-01T00:01:00+00:00",
                    "open": "100",
                    "high": "102",
                    "low": "99",
                    "close": "101",
                    "volume": "3",
                },
                {
                    "open_time": "2026-01-01T00:01:00+00:00",
                    "close_time": "2026-01-01T00:02:00+00:00",
                    "open": "101",
                    "high": "103",
                    "low": "100",
                    "close": "102",
                    "volume": "4",
                },
            ]
        )


@pytest.mark.asyncio
async def test_runner_reads_closed_ohlcv_bars_from_redis() -> None:
    source = RedisMarketDataSource(Redis())

    bars = await source.fetch_bars("1m", 2)

    assert [bar.close for bar in bars] == [Decimal("101"), Decimal("102")]
    assert bars[-1].timestamp == "2026-01-01T00:02:00+00:00"


class Redis15m:
    async def get(self, key):
        assert key == "market:perpetual:htx:candles:BTC-USDT:1min"
        rows = []
        for minute in range(1, 16):
            rows.append(
                {
                    "close_time": f"2026-01-01T00:{minute:02d}:00+00:00",
                    "open": str(99 + minute),
                    "high": str(101 + minute),
                    "low": str(98 + minute),
                    "close": str(100 + minute),
                    "volume": "2",
                }
            )
        return json.dumps(rows)


@pytest.mark.asyncio
async def test_runner_derives_only_complete_15m_bars() -> None:
    source = RedisMarketDataSource(Redis15m())

    bars = await source.fetch_bars("15m", 1)

    assert len(bars) == 1
    assert bars[0].open == Decimal("100")
    assert bars[0].high == Decimal("116")
    assert bars[0].low == Decimal("99")
    assert bars[0].close == Decimal("115")
    assert bars[0].volume == Decimal("30")
    assert bars[0].timestamp == "2026-01-01T00:15:00+00:00"

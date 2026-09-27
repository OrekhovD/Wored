"""Unit tests for F05: day report assembly (service + presenter + reconciliation).

Tests the pure computation layer without a real DB. The repository calls are
mocked to supply known data; assertions verify that format_report, reconcile and
the service glue produce deterministic, honest results.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from paper_trading.contracts import (
    Account,
    AccountKind,
    DayState,
    JournalBucket,
    JournalPosting,
    JournalSourceType,
    Position,
    PositionSide,
    PositionStatus,
    TradingDay,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

OWNER_ID = uuid4()
DAY_ID = uuid4()
MANUAL_ACCT_ID = uuid4()
AUTO_ACCT_ID = uuid4()


def _make_day() -> TradingDay:
    return TradingDay(
        day_id=DAY_ID,
        owner_id=OWNER_ID,
        state=DayState.closed,
        start_utc=datetime(2026, 9, 20, tzinfo=timezone.utc),
        end_utc=datetime(2026, 9, 20, 12, tzinfo=timezone.utc),
        strategy_version="baseline_v1",
    )


def _make_account(acct_id, kind) -> Account:
    return Account(
        account_id=acct_id,
        owner_id=OWNER_ID,
        kind=kind,
        currency="USDT",
        opening_deposit=Decimal("1000"),
    )


def _make_position(pos_id, acct_id, side, pnl_net, fees_entry, fees_exit) -> Position:
    return Position(
        position_id=pos_id,
        account_id=acct_id,
        day_id=DAY_ID,
        instrument="BTC-USDT",
        side=side,
        qty=Decimal("1"),
        avg_entry_price=Decimal("60000"),
        status=PositionStatus.closed,
        opened_at=datetime(2026, 9, 20, 6, tzinfo=timezone.utc),
        closed_at=datetime(2026, 9, 20, 8, tzinfo=timezone.utc),
        close_price=Decimal("61000"),
        realized_gross_pnl=pnl_net + fees_entry + fees_exit,
        realized_net_pnl=pnl_net,
        entry_fee=fees_entry,
        exit_fee=fees_exit,
    )


# Positions: 2 wins, 1 loss
_POSITIONS = [
    _make_position(uuid4(), MANUAL_ACCT_ID, PositionSide.long, Decimal("950"), Decimal("36"), Decimal("36")),
    _make_position(uuid4(), MANUAL_ACCT_ID, PositionSide.short, Decimal("100"), Decimal("10"), Decimal("10")),
    _make_position(uuid4(), MANUAL_ACCT_ID, PositionSide.long, Decimal("-200"), Decimal("20"), Decimal("20")),
]


@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    repo.get_day = AsyncMock(return_value=_make_day())
    repo.get_closed_days = AsyncMock(return_value=[_make_day()])
    repo.get_all_positions_by_day = AsyncMock(return_value=_POSITIONS)
    repo.get_account_by_kind = AsyncMock(side_effect=lambda oid, kind: (
        _make_account(MANUAL_ACCT_ID, AccountKind.manual) if kind == AccountKind.manual
        else _make_account(AUTO_ACCT_ID, AccountKind.auto)
    ))
    repo.get_postings_for_account = AsyncMock(return_value=[])
    repo.get_account_balance = AsyncMock(return_value=Decimal("1000"))
    return repo


@pytest.fixture
def service(mock_repo):
    from paper_trading.service import PaperTradingService
    return PaperTradingService(mock_repo)


# ─── Tests ───────────────────────────────────────────────────────────────────

class TestListDayReports:
    @pytest.mark.asyncio
    async def test_returns_list(self, service):
        reports = await service.list_day_reports(str(OWNER_ID))
        assert isinstance(reports, list)
        assert len(reports) == 1

    @pytest.mark.asyncio
    async def test_day_has_correct_stats(self, service):
        reports = await service.list_day_reports(str(OWNER_ID))
        r = reports[0]
        assert r["trades"] == 3
        assert r["wins"] == 2
        assert r["losses"] == 1
        # net_pnl = 950 + 100 + (-200) = 850
        assert Decimal(r["net_pnl"]) == Decimal("850")

    @pytest.mark.asyncio
    async def test_fees_summed(self, service):
        reports = await service.list_day_reports(str(OWNER_ID))
        # fees = (36+36) + (10+10) + (20+20) = 132
        assert Decimal(reports[0]["fees"]) == Decimal("132")


class TestGetDayReport:
    @pytest.mark.asyncio
    async def test_returns_full_report(self, service):
        report = await service.get_day_report(str(OWNER_ID), str(DAY_ID))
        assert report is not None
        assert report["day_id"] == str(DAY_ID)
        assert report["state"] == "closed"
        assert len(report["accounts"]) == 2  # manual + auto

    @pytest.mark.asyncio
    async def test_manual_account_has_positions(self, service):
        report = await service.get_day_report(str(OWNER_ID), str(DAY_ID))
        manual = next(a for a in report["accounts"] if a["account_label"] == "manual")
        assert manual["stats"]["trades_count"] == 3
        assert len(manual["closed_positions"]) == 3

    @pytest.mark.asyncio
    async def test_auto_account_has_no_positions(self, service):
        report = await service.get_day_report(str(OWNER_ID), str(DAY_ID))
        auto = next(a for a in report["accounts"] if a["account_label"] == "auto")
        assert auto["stats"]["trades_count"] == 0

    @pytest.mark.asyncio
    async def test_reconciliation_present(self, service):
        """Reconciliation field exists; mismatch expected since mock has no postings."""
        report = await service.get_day_report(str(OWNER_ID), str(DAY_ID))
        manual = next(a for a in report["accounts"] if a["account_label"] == "manual")
        assert "reconciliation" in manual
        # With empty postings but non-zero PnL, reconcile detects mismatch
        assert manual["reconciliation"]["balanced"] is False
        assert len(manual["reconciliation"]["mismatches"]) > 0

    @pytest.mark.asyncio
    async def test_unknown_day_returns_none(self, service):
        service.repo.get_day = AsyncMock(return_value=None)
        result = await service.get_day_report(str(OWNER_ID), str(uuid4()))
        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_owner_returns_none(self, service):
        result = await service.get_day_report(str(uuid4()), str(DAY_ID))
        assert result is None


class TestNoFabrication:
    """F05 must never fabricate data."""

    @pytest.mark.asyncio
    async def test_empty_days_returns_empty_list(self, service):
        service.repo.get_closed_days = AsyncMock(return_value=[])
        reports = await service.list_day_reports(str(OWNER_ID))
        assert reports == []

    @pytest.mark.asyncio
    async def test_no_positions_gives_zero_pnl(self, service):
        service.repo.get_all_positions_by_day = AsyncMock(return_value=[])
        report = await service.get_day_report(str(OWNER_ID), str(DAY_ID))
        manual = next(a for a in report["accounts"] if a["account_label"] == "manual")
        assert manual["stats"]["trades_count"] == 0
        assert Decimal(manual["summary"]["net_pnl"]) == Decimal("0")

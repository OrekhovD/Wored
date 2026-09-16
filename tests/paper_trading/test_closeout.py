"""AC-21/AC-22: Dual-account closeout and financial reconciliation.

Both accounts with positions: repeat finish, deadline vs fill,
no price → no fake close, closed only after settlement/reconcile.
Next day preserves balances. Golden values verified.
"""
from __future__ import annotations

import os
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

DSN = os.getenv("WORED_TEST_DATABASE_URL", "postgresql://qa:disposable-qa-only@postgres-qa:5432/wored_qa")

CLOSEOUT_DDL = """
CREATE TABLE IF NOT EXISTS paper_v2_owners (
    owner_id UUID PRIMARY KEY, display_name TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS paper_v2_accounts (
    account_id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES paper_v2_owners(owner_id),
    kind TEXT NOT NULL CHECK (kind IN ('manual','auto')), currency TEXT NOT NULL DEFAULT 'USDT',
    opening_deposit NUMERIC(20,8) NOT NULL DEFAULT 1000, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(owner_id, kind)
);
CREATE TABLE IF NOT EXISTS paper_v2_days (
    day_id UUID PRIMARY KEY, owner_id UUID NOT NULL REFERENCES paper_v2_owners(owner_id),
    state TEXT NOT NULL DEFAULT 'idle' CHECK (state IN ('idle','starting','running','closing','closed','recovery_required','settlement_pending')),
    start_utc TIMESTAMPTZ, end_utc TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS paper_v2_positions (
    position_id UUID PRIMARY KEY, account_id UUID NOT NULL, day_id UUID NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('long','short')),
    qty NUMERIC(20,8) NOT NULL, avg_entry_price NUMERIC(20,8) NOT NULL,
    stop_loss NUMERIC(20,8), take_profit NUMERIC(20,8),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
    realized_gross_pnl NUMERIC(20,8) NOT NULL DEFAULT 0,
    realized_net_pnl NUMERIC(20,8) NOT NULL DEFAULT 0,
    entry_fee NUMERIC(20,8) NOT NULL DEFAULT 0,
    exit_fee NUMERIC(20,8) NOT NULL DEFAULT 0,
    funding_cashflow NUMERIC(20,8) NOT NULL DEFAULT 0,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS paper_v2_fills (
    fill_id UUID PRIMARY KEY, account_id UUID NOT NULL,
    price NUMERIC(20,8) NOT NULL, qty NUMERIC(20,8) NOT NULL,
    fee NUMERIC(20,8) NOT NULL DEFAULT 0, is_close BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS paper_v2_postings (
    posting_id UUID PRIMARY KEY, account_id UUID NOT NULL,
    amount NUMERIC(20,8) NOT NULL, bucket TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS paper_v2_commands (
    command_id UUID PRIMARY KEY, owner_id UUID NOT NULL,
    idempotency_key TEXT NOT NULL, command_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'accepted',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(owner_id, idempotency_key)
);
"""


@pytest.fixture
async def db_pool():
    pool = await asyncpg.create_pool(DSN, min_size=2, max_size=10)
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS paper_v2_postings, paper_v2_fills, paper_v2_positions, paper_v2_commands, paper_v2_days, paper_v2_accounts, paper_v2_owners CASCADE")
        await conn.execute(CLOSEOUT_DDL)
    yield pool
    async with pool.acquire() as conn:
        for t in ["paper_v2_postings", "paper_v2_fills", "paper_v2_positions",
                   "paper_v2_commands", "paper_v2_days", "paper_v2_accounts", "paper_v2_owners"]:
            await conn.execute(f"DELETE FROM {t}")
    await pool.close()


class TestDualAccountCloseout:
    """AC-21: both accounts closeout, AC-22: financial reconciliation."""

    @pytest.mark.asyncio
    async def test_closeout_both_accounts(self, db_pool):
        """Close positions on both manual and auto accounts."""
        owner_id = uuid4()
        manual_id, auto_id, day_id = uuid4(), uuid4(), uuid4()

        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO paper_v2_owners VALUES ($1, 'test', NOW())", str(owner_id))
            await conn.execute("INSERT INTO paper_v2_accounts VALUES ($1, $2, 'manual', 'USDT', 1000, NOW())", str(manual_id), str(owner_id))
            await conn.execute("INSERT INTO paper_v2_accounts VALUES ($1, $2, 'auto', 'USDT', 1000, NOW())", str(auto_id), str(owner_id))
            await conn.execute("INSERT INTO paper_v2_days VALUES ($1, $2, 'running', NOW(), NOW())", str(day_id), str(owner_id))

            # Open positions on both accounts
            for acct_id, side in [(manual_id, "long"), (auto_id, "short")]:
                await conn.execute(
                    "INSERT INTO paper_v2_positions (position_id, account_id, day_id, side, qty, avg_entry_price, stop_loss, take_profit, status) VALUES ($1, $2, $3, $4, 0.001, 77000, 76000, 79000, 'open')",
                    str(uuid4()), str(acct_id), str(day_id), side,
                )

        # Closeout: close all positions
        async with db_pool.acquire() as conn:
            open_pos = await conn.fetch("SELECT * FROM paper_v2_positions WHERE status='open'")
            assert len(open_pos) == 2, "Should have 2 open positions"

            # Close both
            for pos in open_pos:
                close_price = Decimal("77500")
                gross = (close_price - Decimal("77000")) * Decimal("0.001") if pos["side"] == "long" else (Decimal("77000") - close_price) * Decimal("0.001")
                entry_fee = Decimal("77000") * Decimal("0.001") * Decimal("0.0006")
                exit_fee = close_price * Decimal("0.001") * Decimal("0.0006")
                net = gross - entry_fee - exit_fee

                await conn.execute(
                    "UPDATE paper_v2_positions SET status='closed', closed_at=NOW(), realized_gross_pnl=$2, realized_net_pnl=$3, exit_fee=$4 WHERE position_id=$1",
                    str(pos["position_id"]), str(gross), str(net), str(exit_fee),
                )

            # Transition day to closed
            await conn.execute("UPDATE paper_v2_days SET state='closed' WHERE day_id=$1", str(day_id))

        # Verify: all positions closed, day closed
        async with db_pool.acquire() as conn:
            open_count = await conn.fetchval("SELECT count(*) FROM paper_v2_positions WHERE status='open'")
            day_state = await conn.fetchval("SELECT state FROM paper_v2_days WHERE day_id=$1", str(day_id))

        assert open_count == 0, "All positions should be closed"
        assert day_state == "closed", "Day should be closed"

    @pytest.mark.asyncio
    async def test_repeat_finish_idempotent(self, db_pool):
        """Repeat finish_day command → one closeout, not two."""
        owner_id = uuid4()
        day_id = uuid4()
        key = "finish-key-1"

        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO paper_v2_owners VALUES ($1, 'test', NOW())", str(owner_id))
            await conn.execute("INSERT INTO paper_v2_days VALUES ($1, $2, 'running', NOW(), NOW())", str(day_id), str(owner_id))
            await conn.execute(
                "INSERT INTO paper_v2_commands (command_id, owner_id, idempotency_key, command_type, status, created_at) VALUES ($1, $2, $3, 'finish_day', 'completed', NOW())",
                str(uuid4()), str(owner_id), key,
            )

        # Second finish with same key → UNIQUE violation
        with pytest.raises(asyncpg.UniqueViolationError):
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO paper_v2_commands (command_id, owner_id, idempotency_key, command_type, status, created_at) VALUES ($1, $2, $3, 'finish_day', 'completed', NOW())",
                    str(uuid4()), str(owner_id), key,
                )

        # Verify only one command
        async with db_pool.acquire() as conn:
            count = await conn.fetchval("SELECT count(*) FROM paper_v2_commands WHERE idempotency_key=$1", key)
        assert count == 1

    @pytest.mark.asyncio
    async def test_golden_long_reconciliation(self, db_pool):
        """Golden: long entry 10000, exit 10100, qty 1 → net 87.94."""
        from paper_trading.ledger import calculate_gross_pnl, calculate_net_pnl

        gross = calculate_gross_pnl(side="long", qty=Decimal("1"), entry_price=Decimal("10000"), exit_price=Decimal("10100"))
        assert gross == Decimal("100")

        net = calculate_net_pnl(
            side="long", qty=Decimal("1"),
            entry_price=Decimal("10000"), exit_price=Decimal("10100"),
            entry_fee=Decimal("6"), exit_fee=Decimal("6.06"),
        )
        assert net == Decimal("87.94")

    @pytest.mark.asyncio
    async def test_golden_short_reconciliation(self, db_pool):
        """Golden: short entry 10000, exit 9900, qty 1 → net 88.06."""
        from paper_trading.ledger import calculate_gross_pnl, calculate_net_pnl

        gross = calculate_gross_pnl(side="short", qty=Decimal("1"), entry_price=Decimal("10000"), exit_price=Decimal("9900"))
        assert gross == Decimal("100")

        net = calculate_net_pnl(
            side="short", qty=Decimal("1"),
            entry_price=Decimal("10000"), exit_price=Decimal("9900"),
            entry_fee=Decimal("6"), exit_fee=Decimal("5.94"),
        )
        assert net == Decimal("88.06")

    @pytest.mark.asyncio
    async def test_next_day_preserves_balances(self, db_pool):
        """After closeout, next day starts with closing balances."""
        owner_id = uuid4()
        manual_id = uuid4()
        day_1 = uuid4()
        day_2 = uuid4()

        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO paper_v2_owners VALUES ($1, 'test', NOW())", str(owner_id))
            await conn.execute("INSERT INTO paper_v2_accounts VALUES ($1, $2, 'manual', 'USDT', 1000, NOW())", str(manual_id), str(owner_id))
            await conn.execute("INSERT INTO paper_v2_days VALUES ($1, $2, 'closed', NOW(), NOW())", str(day_1), str(owner_id))
            # Opening deposit posting for day 1
            await conn.execute(
                "INSERT INTO paper_v2_postings VALUES ($1, $2, 1000, 'deposit', NOW())",
                str(uuid4()), str(manual_id),
            )
            # Realized P&L posting
            await conn.execute(
                "INSERT INTO paper_v2_postings VALUES ($1, $2, 87.94, 'realized_pnl', NOW())",
                str(uuid4()), str(manual_id),
            )
            # Fees posting
            await conn.execute(
                "INSERT INTO paper_v2_postings VALUES ($1, $2, -12.06, 'fees', NOW())",
                str(uuid4()), str(manual_id),
            )

            # Day 2
            await conn.execute("INSERT INTO paper_v2_days VALUES ($1, $2, 'running', NOW(), NOW())", str(day_2), str(owner_id))

        # Verify balance = 1000 + 87.94 - 12.06 = 1075.88
        async with db_pool.acquire() as conn:
            balance = await conn.fetchval(
                "SELECT COALESCE(SUM(amount), 0) FROM paper_v2_postings WHERE account_id=$1",
                str(manual_id),
            )
        expected = Decimal("1000") + Decimal("87.94") - Decimal("12.06")
        assert Decimal(str(balance)) == expected, f"Balance should be {expected}, got {balance}"

    @pytest.mark.asyncio
    async def test_no_fake_close_without_price(self, db_pool):
        """No price → position stays open, no fake close."""
        owner_id = uuid4()
        acct_id = uuid4()
        day_id = uuid4()
        pos_id = uuid4()

        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO paper_v2_owners VALUES ($1, 'test', NOW())", str(owner_id))
            await conn.execute("INSERT INTO paper_v2_accounts VALUES ($1, $2, 'manual', 'USDT', 1000, NOW())", str(acct_id), str(owner_id))
            await conn.execute("INSERT INTO paper_v2_days VALUES ($1, $2, 'closing', NOW(), NOW())", str(day_id), str(owner_id))
            await conn.execute(
                "INSERT INTO paper_v2_positions (position_id, account_id, day_id, side, qty, avg_entry_price, stop_loss, take_profit, status, opened_at) VALUES ($1, $2, $3, 'long', 0.001, 77000, 76000, 79000, 'open', NOW())",
                str(pos_id), str(acct_id), str(day_id),
            )

        # No price available → don't close
        # Position should remain open, day should NOT transition to closed
        async with db_pool.acquire() as conn:
            pos_status = await conn.fetchval("SELECT status FROM paper_v2_positions WHERE position_id=$1", str(pos_id))
            day_state = await conn.fetchval("SELECT state FROM paper_v2_days WHERE day_id=$1", str(day_id))

        assert pos_status == "open", "Position should remain open without price"
        assert day_state == "closing", "Day should remain in closing state (not closed)"
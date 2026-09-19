from datetime import datetime, timezone
from uuid import uuid4

import pytest

from paper_trading.contracts import Account, AccountKind, Command, CommandType, DayState
from paper_trading.runner import PaperTradingRunner
from paper_trading.strategy import BaselineV1Strategy


class Repository:
    def __init__(self, account: Account) -> None:
        self.account = account

    async def get_account(self, account_id):
        return self.account if account_id == self.account.account_id else None

    async def update_day_state(self, day_id, state, expected_state=None):
        return state == DayState.running


def command(account_id, owner_id):
    now = datetime.now(timezone.utc)
    return Command(
        command_id=uuid4(),
        owner_id=owner_id,
        account_id=account_id,
        day_id=uuid4(),
        command_type=CommandType.start_day,
        idempotency_key="start-test",
        request_hash="test",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_only_auto_start_command_enables_automatic_entries() -> None:
    owner_id = uuid4()
    manual = Account(uuid4(), owner_id, AccountKind.manual)
    runner = PaperTradingRunner(strategy=BaselineV1Strategy(), repository=Repository(manual))

    await runner._handle_start_day(command(manual.account_id, owner_id))

    assert runner.entries_blocked
    assert runner._get_auto_account_id() is None


@pytest.mark.asyncio
async def test_auto_start_command_binds_owner_and_account() -> None:
    owner_id = uuid4()
    auto = Account(uuid4(), owner_id, AccountKind.auto)
    runner = PaperTradingRunner(strategy=BaselineV1Strategy(), repository=Repository(auto))

    await runner._handle_start_day(command(auto.account_id, owner_id))

    assert not runner.entries_blocked
    assert runner._get_auto_account_id() == auto.account_id
    assert runner._auto_owner_id == owner_id

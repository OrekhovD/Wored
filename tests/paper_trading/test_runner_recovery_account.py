from uuid import uuid4

import pytest

from paper_trading.contracts import Account, AccountKind
from paper_trading.runner import PaperTradingRunner, RecoveryStore
from paper_trading.strategy import BaselineV1Strategy


class Store(RecoveryStore):
    def __init__(self, accounts):
        self.accounts = accounts

    async def load_active_auto_accounts(self):
        return self.accounts


@pytest.mark.asyncio
async def test_recovery_restores_one_active_auto_account() -> None:
    owner_id = uuid4()
    account = Account(uuid4(), owner_id, AccountKind.auto)
    runner = PaperTradingRunner(
        strategy=BaselineV1Strategy(),
        recovery_store=Store([account]),
    )

    report = await runner.recover()

    assert report["recovered"] is True
    assert report["active_auto_accounts"] == 1
    assert not runner.entries_blocked
    assert runner._get_auto_account_id() == account.account_id
    assert runner._auto_owner_id == owner_id


@pytest.mark.asyncio
async def test_recovery_without_active_auto_account_stays_blocked() -> None:
    runner = PaperTradingRunner(
        strategy=BaselineV1Strategy(),
        recovery_store=Store([]),
    )

    report = await runner.recover()

    assert report["entries_blocked_reason"] == "no_active_auto_account"
    assert runner.entries_blocked
    assert runner._get_auto_account_id() is None

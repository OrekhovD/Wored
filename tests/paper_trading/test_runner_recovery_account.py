from uuid import UUID, uuid4

import pytest

from paper_trading.adapter import owner_id_from_telegram
from paper_trading.contracts import Account, AccountKind
from paper_trading.runner import (
    OWNER_TELEGRAM_ID_ENV,
    PaperTradingRunner,
    RecoveryStore,
    _owner_scope,
)
from paper_trading.strategy import BaselineV1Strategy


class Store(RecoveryStore):
    """Mirrors the production query: scoped to one owner when asked."""

    def __init__(self, accounts):
        self.accounts = accounts
        self.seen_scopes: list[str | None] = []

    async def load_active_auto_accounts(self, owner_id=None):
        self.seen_scopes.append(str(owner_id) if owner_id is not None else None)
        if owner_id is None:
            return list(self.accounts)
        return [a for a in self.accounts if str(a.owner_id) == str(owner_id)]


def _runner(store: Store) -> PaperTradingRunner:
    return PaperTradingRunner(strategy=BaselineV1Strategy(), recovery_store=store)


@pytest.mark.asyncio
async def test_recovery_restores_one_active_auto_account() -> None:
    owner_id = uuid4()
    account = Account(uuid4(), owner_id, AccountKind.auto)
    store = Store([account])
    runner = _runner(store)

    report = await runner.recover()

    assert report["recovered"] is True
    assert report["active_auto_accounts"] == 1
    assert report["auto_owner_scope"] == "unscoped"
    assert store.seen_scopes == [None]
    assert not runner.entries_blocked
    assert runner._get_auto_account_id() == account.account_id
    assert runner._auto_owner_id == owner_id


@pytest.mark.asyncio
async def test_recovery_without_active_auto_account_stays_blocked() -> None:
    runner = _runner(Store([]))

    report = await runner.recover()

    assert report["entries_blocked_reason"] == "no_active_auto_account"
    assert runner.entries_blocked
    assert runner._get_auto_account_id() is None


@pytest.mark.asyncio
async def test_second_owner_does_not_block_a_scoped_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The state measured live: one runner, two owners both holding a day.

    Unscoped, the other owner's account makes the bind ambiguous and every new
    entry stays blocked until someone runs ``start_day`` by hand. Declaring the
    operator scopes the search to one owner, so recovery binds the runner's own
    account and unblocks entries.
    """
    telegram_id = 1234567890
    my_owner = UUID(owner_id_from_telegram(telegram_id))
    mine = Account(uuid4(), my_owner, AccountKind.auto)
    theirs = Account(uuid4(), uuid4(), AccountKind.auto)
    monkeypatch.setenv(OWNER_TELEGRAM_ID_ENV, str(telegram_id))
    store = Store([theirs, mine])
    runner = _runner(store)

    report = await runner.recover()

    assert store.seen_scopes == [str(my_owner)]
    assert report["auto_owner_scope"] == str(my_owner)
    assert report["active_auto_accounts"] == 1
    assert report["recovered"] is True
    assert not runner.entries_blocked
    assert runner._get_auto_account_id() == mine.account_id
    assert runner._auto_owner_id == my_owner


@pytest.mark.asyncio
async def test_unscoped_recovery_keeps_two_owners_ambiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OWNER_TELEGRAM_ID_ENV, raising=False)
    store = Store([Account(uuid4(), uuid4(), AccountKind.auto),
                   Account(uuid4(), uuid4(), AccountKind.auto)])
    runner = _runner(store)

    report = await runner.recover()

    assert store.seen_scopes == [None]
    assert report["auto_owner_scope"] == "unscoped"
    assert report["entries_blocked_reason"] == "ambiguous_active_auto_accounts"
    assert runner.entries_blocked
    assert runner._get_auto_account_id() is None


def test_owner_scope_is_none_when_undeclared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OWNER_TELEGRAM_ID_ENV, raising=False)
    assert _owner_scope() is None
    monkeypatch.setenv(OWNER_TELEGRAM_ID_ENV, "   ")
    assert _owner_scope() is None


def test_owner_scope_uses_the_canonical_owner_derivation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runner must resolve to the id the consoles already write."""
    monkeypatch.setenv(OWNER_TELEGRAM_ID_ENV, "1234567890")
    assert _owner_scope() == owner_id_from_telegram(1234567890)


def test_owner_scope_rejects_a_non_id_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OWNER_TELEGRAM_ID_ENV, "not-a-telegram-id")
    with pytest.raises(RuntimeError, match=OWNER_TELEGRAM_ID_ENV):
        _owner_scope()

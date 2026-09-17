from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from paper_trading.contracts import Fill, OrderSide, Position, PositionSide
from paper_trading.ledger import build_fill_postings
from paper_trading.repository import PaperRepository


class AsyncContext:
    def __init__(self, value, *, on_enter=None) -> None:
        self.value = value
        self.on_enter = on_enter

    async def __aenter__(self):
        if self.on_enter is not None:
            self.on_enter()
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class Connection:
    def __init__(self, account_id, balance=Decimal("1000")) -> None:
        self.account_id = account_id
        self.balance = balance
        self.statements: list[str] = []
        self.transaction_entries = 0

    def transaction(self):
        return AsyncContext(self, on_enter=self._enter_transaction)

    def _enter_transaction(self) -> None:
        self.transaction_entries += 1

    async def execute(self, sql, *args):
        self.statements.append(" ".join(sql.split()))
        return "OK"

    async def fetchrow(self, sql, *args):
        self.statements.append(" ".join(sql.split()))
        if "SUM(amount)" in sql:
            return {"balance": self.balance}
        if "paper_v2_orders" in sql:
            return {"account_id": self.account_id, "state": "pending"}
        return None


class Pool:
    def __init__(self, connection) -> None:
        self.connection = connection

    def acquire(self):
        return AsyncContext(self.connection)


@pytest.mark.asyncio
async def test_open_fill_position_and_ledger_share_one_transaction() -> None:
    account_id = uuid4()
    day_id = uuid4()
    order_id = uuid4()
    now = datetime.now(timezone.utc)
    fill = Fill(
        fill_id=uuid4(),
        order_id=order_id,
        account_id=account_id,
        execution_quote_id="quote-1",
        side=OrderSide.buy,
        price=Decimal("10000"),
        qty=Decimal("1"),
        fee=Decimal("6"),
        source_timestamp=now,
        receive_timestamp=now,
        execute_timestamp=now,
    )
    position = Position(
        position_id=order_id,
        account_id=account_id,
        day_id=day_id,
        side=PositionSide.long,
        qty=Decimal("1"),
        avg_entry_price=Decimal("10000"),
        isolated_margin=Decimal("100"),
        stop_loss=Decimal("9900"),
        take_profit=Decimal("10200"),
        entry_fee=Decimal("6"),
    )
    postings = build_fill_postings(
        account_id=account_id,
        fill_price=fill.price,
        fill_qty=fill.qty,
        fee=fill.fee,
        is_close=False,
        side=PositionSide.long,
        day_id=day_id,
        source_ref=str(fill.fill_id),
        reserved_margin=position.isolated_margin,
    )
    connection = Connection(account_id)
    repository = PaperRepository(Pool(connection))

    await repository.commit_open_fill(
        order_id=order_id,
        fill=fill,
        position=position,
        postings=postings,
    )

    joined = "\n".join(connection.statements)
    assert connection.transaction_entries == 1
    assert "UPDATE paper_v2_orders" in joined
    assert "INSERT INTO paper_v2_fills" in joined
    assert "INSERT INTO paper_v2_positions" in joined
    assert joined.count("INSERT INTO paper_v2_postings") == len(postings)


@pytest.mark.asyncio
async def test_open_fill_is_rejected_under_lock_when_balance_changed() -> None:
    account_id = uuid4()
    day_id = uuid4()
    order_id = uuid4()
    now = datetime.now(timezone.utc)
    fill = Fill(
        fill_id=uuid4(), order_id=order_id, account_id=account_id,
        execution_quote_id="quote-low-balance", side=OrderSide.buy,
        price=Decimal("10000"), qty=Decimal("1"), fee=Decimal("6"),
        source_timestamp=now, receive_timestamp=now, execute_timestamp=now,
    )
    position = Position(
        position_id=order_id, account_id=account_id, day_id=day_id,
        side=PositionSide.long, qty=Decimal("1"),
        avg_entry_price=Decimal("10000"), isolated_margin=Decimal("100"),
        entry_fee=Decimal("6"),
    )
    postings = build_fill_postings(
        account_id=account_id, fill_price=fill.price, fill_qty=fill.qty,
        fee=fill.fee, is_close=False, side=PositionSide.long,
        day_id=day_id, source_ref=str(fill.fill_id),
        reserved_margin=position.isolated_margin,
    )
    connection = Connection(account_id, balance=Decimal("50"))
    repository = PaperRepository(Pool(connection))

    with pytest.raises(ValueError, match="insufficient account balance at commit"):
        await repository.commit_open_fill(
            order_id=order_id, fill=fill, position=position, postings=postings
        )

    joined = "\n".join(connection.statements)
    assert "UPDATE paper_v2_orders" not in joined
    assert "INSERT INTO paper_v2_fills" not in joined

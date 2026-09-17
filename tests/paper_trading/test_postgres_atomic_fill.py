from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from paper_trading.contracts import (
    AccountKind,
    DayState,
    Fill,
    Order,
    OrderSide,
    OrderType,
    Position,
    PositionSide,
)
from paper_trading.ledger import build_fill_postings
from paper_trading.repository import PaperRepository


@pytest.fixture
async def repository():
    import os

    dsn = os.environ["WORED_TEST_DATABASE_URL"]
    schema = f"atomic_{uuid4().hex}"
    admin = await asyncpg.connect(dsn)
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    await admin.close()
    pool = await asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=4,
        server_settings={"search_path": f'"{schema}"'},
    )
    ddl = (Path(__file__).parents[2] / "migrations" / "paper_v2_schema.sql").read_text(
        encoding="utf-8"
    )
    async with pool.acquire() as conn:
        await conn.execute(ddl)
    try:
        yield PaperRepository(pool)
    finally:
        await pool.close()
        admin = await asyncpg.connect(dsn)
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


@pytest.mark.asyncio
async def test_atomic_open_and_close_persist_one_consistent_financial_chain(repository) -> None:
    owner_id, account_id, day_id, order_id = uuid4(), uuid4(), uuid4(), uuid4()
    await repository.create_owner(owner_id, "atomic-test")
    await repository.create_account(
        account_id,
        owner_id,
        AccountKind.auto,
        opening_deposit=Decimal("1000"),
    )
    await repository.create_day(day_id, owner_id)
    await repository.update_day_state(day_id, DayState.running, expected_state=DayState.idle)
    order = Order(
        order_id=order_id,
        account_id=account_id,
        day_id=day_id,
        origin="auto",
        actor="auto",
        side=OrderSide.buy,
        order_type=OrderType.market,
        qty=Decimal("1"),
        stop_loss=Decimal("9900"),
        take_profit=Decimal("10200"),
    )
    await repository.create_order(order)
    now = datetime.now(timezone.utc)
    open_fill = Fill(
        fill_id=uuid4(), order_id=order_id, account_id=account_id,
        execution_quote_id="atomic-open", side=OrderSide.buy,
        price=Decimal("10000"), qty=Decimal("1"), fee=Decimal("6"),
        source_timestamp=now, receive_timestamp=now, execute_timestamp=now,
    )
    position = Position(
        position_id=order_id, account_id=account_id, day_id=day_id,
        side=PositionSide.long, qty=Decimal("1"),
        avg_entry_price=Decimal("10000"), isolated_margin=Decimal("100"),
        stop_loss=Decimal("9900"), take_profit=Decimal("10200"),
        entry_fee=Decimal("6"),
    )
    open_postings = build_fill_postings(
        account_id, Decimal("10000"), Decimal("1"), Decimal("6"), False,
        PositionSide.long, day_id, str(open_fill.fill_id),
        reserved_margin=Decimal("100"),
    )

    await repository.commit_open_fill(
        order_id=order_id, fill=open_fill, position=position, postings=open_postings
    )

    assert (await repository.get_order(order_id)).state.value == "filled"
    assert (await repository.get_position(order_id)).status.value == "open"
    assert await repository.get_account_balance(account_id) == Decimal("894.00000000")

    close_fill = Fill(
        fill_id=uuid4(), order_id=order_id, account_id=account_id,
        execution_quote_id="atomic-close", side=OrderSide.sell,
        price=Decimal("10100"), qty=Decimal("1"), fee=Decimal("6.06"),
        is_close=True, source_timestamp=now, receive_timestamp=now,
        execute_timestamp=now,
    )
    close_postings = build_fill_postings(
        account_id, Decimal("10100"), Decimal("1"), Decimal("6.06"), True,
        PositionSide.long, day_id, str(close_fill.fill_id),
        realized_gross=Decimal("100"), realized_net=Decimal("87.94"),
        released_margin=Decimal("100"),
    )
    await repository.commit_position_close(
        position_id=order_id,
        fill=close_fill,
        close_price=Decimal("10100"),
        realized_gross_pnl=Decimal("100"),
        realized_net_pnl=Decimal("87.94"),
        exit_fee=Decimal("6.06"),
        postings=close_postings,
    )

    assert (await repository.get_position(order_id)).status.value == "closed"
    assert await repository.get_account_balance(account_id) == Decimal("1087.94000000")
    async with repository.pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM paper_v2_fills") == 2
        assert await conn.fetchval("SELECT count(*) FROM paper_v2_postings") == 6

    with pytest.raises(ValueError, match="position is not open"):
        await repository.commit_position_close(
            position_id=order_id,
            fill=close_fill,
            close_price=Decimal("10100"),
            realized_gross_pnl=Decimal("100"),
            realized_net_pnl=Decimal("87.94"),
            exit_fee=Decimal("6.06"),
            postings=close_postings,
        )

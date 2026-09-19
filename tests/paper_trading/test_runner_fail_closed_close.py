from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from paper_trading.contracts import Command, CommandType, DayState, Position, PositionSide
from paper_trading.runner import PaperTradingRunner
from paper_trading.strategy import BaselineV1Strategy


@pytest.mark.asyncio
async def test_manual_close_without_snapshot_keeps_position_open() -> None:
    owner_id = uuid4()
    account_id = uuid4()
    position_id = uuid4()
    position = Position(
        position_id=position_id,
        account_id=account_id,
        day_id=uuid4(),
        side=PositionSide.long,
        qty=Decimal("1"),
        avg_entry_price=Decimal("10000"),
        isolated_margin=Decimal("1000"),
    )
    runner = PaperTradingRunner(strategy=BaselineV1Strategy())
    runner._positions[position_id] = position
    now = datetime.now(timezone.utc)
    command = Command(
        command_id=uuid4(),
        owner_id=owner_id,
        account_id=account_id,
        day_id=position.day_id,
        command_type=CommandType.close_position,
        idempotency_key="close-no-market",
        request_hash="close-no-market",
        result={"position_id": str(position_id)},
        created_at=now,
        updated_at=now,
    )

    processed = await runner._execute_command(command, now.timestamp())

    assert processed is False
    assert position_id in runner._positions


class Repository:
    def __init__(self) -> None:
        self.states = []

    async def update_day_state(self, day_id, state, expected_state=None):
        self.states.append((day_id, state))
        return True


@pytest.mark.asyncio
async def test_finish_day_without_snapshot_keeps_position_and_marks_settlement_pending() -> None:
    owner_id = uuid4()
    account_id = uuid4()
    day_id = uuid4()
    position_id = uuid4()
    repository = Repository()
    runner = PaperTradingRunner(
        strategy=BaselineV1Strategy(),
        repository=repository,
    )
    runner._positions[position_id] = Position(
        position_id=position_id,
        account_id=account_id,
        day_id=day_id,
        side=PositionSide.long,
        qty=Decimal("1"),
        avg_entry_price=Decimal("10000"),
        isolated_margin=Decimal("100"),
    )
    now = datetime.now(timezone.utc)
    command = Command(
        command_id=uuid4(), owner_id=owner_id, account_id=account_id,
        day_id=day_id, command_type=CommandType.finish_day,
        idempotency_key="finish-no-market", request_hash="finish-no-market",
        created_at=now, updated_at=now,
    )

    processed = await runner._handle_finish_day(command)

    assert processed is False
    assert position_id in runner._positions
    assert repository.states[-1] == (day_id, DayState.settlement_pending)

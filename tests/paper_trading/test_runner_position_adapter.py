from decimal import Decimal
from uuid import uuid4

from paper_trading.contracts import Position, PositionSide, PositionStatus
from paper_trading.runner import PaperTradingRunner


def test_persisted_position_is_translated_for_execution() -> None:
    position = Position(
        position_id=uuid4(),
        account_id=uuid4(),
        day_id=uuid4(),
        side=PositionSide.short,
        qty=Decimal("2"),
        avg_entry_price=Decimal("10000"),
        isolated_margin=Decimal("200"),
        stop_loss=Decimal("10100"),
        take_profit=Decimal("9800"),
        entry_fee=Decimal("1.2"),
        status=PositionStatus.open,
    )

    adapted = PaperTradingRunner._to_execution_position(position)

    assert adapted.position_id == str(position.position_id)
    assert adapted.direction == "short"
    assert adapted.entry_price == Decimal("10000")
    assert adapted.stop_price == Decimal("10100")
    assert adapted.quantity == Decimal("2")
    assert adapted.reserved_margin == Decimal("200")

from decimal import Decimal
from uuid import uuid4

from paper_trading.contracts import PositionSide
from paper_trading.ledger import build_fill_postings, get_account_balance, post_entry


def test_open_and_close_cashflows_restore_margin_and_apply_net_result() -> None:
    account_id = uuid4()
    opening = build_fill_postings(
        account_id=account_id,
        fill_price=Decimal("10000"),
        fill_qty=Decimal("1"),
        fee=Decimal("6"),
        is_close=False,
        side=PositionSide.long,
        source_ref="open-fill",
        reserved_margin=Decimal("1000"),
    )
    closing = build_fill_postings(
        account_id=account_id,
        fill_price=Decimal("10100"),
        fill_qty=Decimal("1"),
        fee=Decimal("6.06"),
        is_close=True,
        side=PositionSide.long,
        source_ref="close-fill",
        realized_gross=Decimal("100"),
        realized_net=Decimal("87.94"),
        released_margin=Decimal("1000"),
    )

    assert post_entry(account_id, opening) == opening
    assert post_entry(account_id, closing) == closing
    assert get_account_balance(opening + closing, account_id) == Decimal("87.94000000")


def test_one_fill_uses_one_event_id() -> None:
    postings = build_fill_postings(
        account_id=uuid4(),
        fill_price=Decimal("100"),
        fill_qty=Decimal("1"),
        fee=Decimal("0.06"),
        is_close=False,
        side=PositionSide.long,
        source_ref="fill-1",
        reserved_margin=Decimal("10"),
    )

    assert len({posting.event_id for posting in postings}) == 1

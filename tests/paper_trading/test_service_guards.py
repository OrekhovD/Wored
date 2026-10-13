"""Гейты доменного сервиса ручной сделки (:mod:`paper_trading.service`).

P4 переносит проверку «кому можно бить руками» из UI-слоя в домен: runner
исполняет команду исходя из предпосылки, что превью уже проверило риск,
поэтому без этих гейтов любой не-WebUI вызывающий обходил бы и тип счёта,
и владение, и состояние дня. Репозиторий подменяется duck-typed заглушкой:
проверяется решение сервиса, а не SQL.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from paper_trading.contracts import (
    Account,
    AccountKind,
    Command,
    CommandType,
    DayState,
    Position,
    PositionSide,
    TradingDay,
)
from paper_trading.repository import IdempotencyConflict
from paper_trading.service import PaperTradingService

OWNER = uuid4()
OTHER_OWNER = uuid4()
MANUAL = uuid4()
AUTO = uuid4()
GHOST = uuid4()


class FakeRepo:
    """Минимальный репозиторий: только то, что зовёт сервис."""

    def __init__(
        self,
        *,
        day: TradingDay | None,
        accounts: dict[str, Account],
        positions: dict[str, list[Position]] | None = None,
        balance: Decimal = Decimal("1000"),
        realized_net: Decimal = Decimal("0"),
        by_id: dict[str, Position] | None = None,
        commands: dict[str, Command] | None = None,
        conflict: bool = False,
    ) -> None:
        self.day = day
        self.accounts = accounts
        self.positions = positions or {}
        self.balance = balance
        self.realized_net = realized_net
        self.by_id = by_id or {}
        self.commands = commands or {}
        self.conflict = conflict
        self.submitted: list[dict] = []

    async def get_active_day(self, owner_id):
        return self.day if str(owner_id) == str(self.day_owner) else None

    @property
    def day_owner(self) -> str:
        return self.day.owner_id if self.day else OWNER

    async def get_account(self, account_id):
        return self.accounts.get(str(account_id))

    async def get_account_balance(self, account_id):
        return self.balance

    async def get_open_positions_by_account(self, account_id):
        return list(self.positions.get(str(account_id), []))

    async def get_day_realized_net(self, account_id, day_id):
        return self.realized_net

    async def get_position_by_id(self, position_id):
        return self.by_id.get(str(position_id))

    async def get_command(self, command_id):
        return self.commands.get(str(command_id))

    async def find_command_by_idempotency_key(self, owner_id, idempotency_key):
        for call in self.submitted:
            if call["idempotency_key"] == idempotency_key:
                return _command(call)
        return _command({"command_id": str(uuid4()), "owner_id": owner_id,
                         "idempotency_key": idempotency_key,
                         "command_type": CommandType.submit_order})

    async def submit_command(self, **kwargs):
        if self.conflict:
            self.submitted.append(kwargs)
            raise IdempotencyConflict("key already used with a different payload")
        self.submitted.append(kwargs)
        return _command(kwargs)


def _command(call: dict) -> Command:
    now = datetime.now(timezone.utc)
    return Command(
        command_id=call.get("command_id") or uuid4(),
        owner_id=call["owner_id"],
        account_id=call.get("account_id"),
        day_id=call.get("day_id"),
        command_type=call["command_type"],
        idempotency_key=call["idempotency_key"],
        request_hash=call["idempotency_key"],
        created_at=now,
        updated_at=now,
    )


def day(*, state: DayState = DayState.running, owner_id: str = None) -> TradingDay:
    return TradingDay(
        day_id=uuid4(),
        owner_id=owner_id or OWNER,
        state=state,
        settings_snapshot={"max_risk_per_order_usdt": "10"},
    )


def account(account_id, owner_id, kind) -> Account:
    return Account(account_id=account_id, owner_id=owner_id, kind=kind)


ACCOUNTS = {
    str(MANUAL): account(MANUAL, OWNER, AccountKind.manual),
    str(AUTO): account(AUTO, OWNER, AccountKind.auto),
    str(GHOST): account(GHOST, OTHER_OWNER, AccountKind.manual),
}


def service(**overrides) -> tuple[PaperTradingService, FakeRepo, TradingDay]:
    current = day()
    repo = FakeRepo(day=overrides.pop("day", current), accounts=ACCOUNTS, **overrides)
    return PaperTradingService(repo), repo, current


async def submit(service_: PaperTradingService, **kwargs):
    args = {
        "owner_id": str(OWNER),
        "account_id": str(MANUAL),
        "side": "buy",
        "order_type": "market",
        "qty": Decimal("10"),
        "stop_loss": Decimal("99000"),
        "idempotency_key": "tg-1",
    }
    args.update(kwargs)
    return await service_.submit_manual_order(**args)


# ---------------------------------------------------------------------------
# submit_manual_order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_order_is_recorded_for_owned_manual_account() -> None:
    service_, repo, current = service()

    result = await submit(service_)

    assert result["ok"] is True
    assert len(repo.submitted) == 1
    call = repo.submitted[0]
    assert call["command_type"] is CommandType.submit_order
    # Счёт и день берутся из проверки, а не из клиентского payload.
    assert str(call["account_id"]) == str(MANUAL)
    assert str(call["day_id"]) == str(current.day_id)
    assert call["payload"]["origin"] == "manual"
    assert call["payload"]["actor"] == "user"


@pytest.mark.asyncio
async def test_manual_order_rejects_auto_account() -> None:
    service_, repo, _ = service()

    result = await submit(service_, account_id=str(AUTO))

    assert result == {"ok": False, "error": "no_manual_account"}
    assert repo.submitted == []


@pytest.mark.asyncio
async def test_manual_order_rejects_account_of_another_owner() -> None:
    service_, repo, _ = service()

    result = await submit(service_, account_id=str(GHOST))

    assert result == {"ok": False, "error": "account_not_owned"}
    assert repo.submitted == []


@pytest.mark.asyncio
async def test_manual_order_rejects_unknown_and_malformed_account() -> None:
    service_, repo, _ = service()

    missing = await submit(service_, account_id=str(uuid4()))
    malformed = await submit(service_, account_id="proto-manual")

    assert missing == {"ok": False, "error": "account_not_found"}
    assert malformed == {"ok": False, "error": "account_not_found"}
    assert repo.submitted == []


@pytest.mark.asyncio
async def test_manual_order_rejects_when_no_day_at_all() -> None:
    service_, repo, _ = service(day=None)
    repo.day = None

    result = await submit(service_)

    assert result == {"ok": False, "error": "no_active_day"}
    assert repo.submitted == []


@pytest.mark.parametrize(
    "state",
    [DayState.idle, DayState.closing, DayState.settlement_pending, DayState.closed],
)
@pytest.mark.asyncio
async def test_manual_order_rejects_day_that_cannot_trade(state: DayState) -> None:
    service_, repo, _ = service(day=day(state=state))

    result = await submit(service_)

    assert result["ok"] is False
    assert result["error"] == "day_not_active"
    assert result["day_state"] == state.value
    assert repo.submitted == []


@pytest.mark.asyncio
async def test_manual_order_rejects_ticket_of_a_previous_day() -> None:
    service_, repo, _ = service()

    result = await submit(service_, expected_day_id=str(uuid4()))

    assert result["ok"] is False
    assert result["error"] == "day_mismatch"
    assert repo.submitted == []


@pytest.mark.asyncio
async def test_manual_order_accepts_matching_expected_day() -> None:
    service_, repo, current = service()

    result = await submit(service_, expected_day_id=str(current.day_id))

    assert result["ok"] is True
    assert len(repo.submitted) == 1


@pytest.mark.asyncio
async def test_idempotency_conflict_reports_existing_command_id() -> None:
    service_, repo, _ = service(conflict=True)

    result = await submit(service_)

    assert result["ok"] is False
    assert result["error"] == "idempotency_conflict"
    # Конфликт обязан назвать команду, которой занят ключ, — иначе клиент
    # не сможет показать статус вместо «не удалось».
    assert result["command_id"]
    assert len(repo.submitted) == 1


# ---------------------------------------------------------------------------
# close_position
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_position_rejects_foreign_owner_without_leaking_it() -> None:
    position_id = uuid4()
    position = Position(
        position_id=position_id,
        account_id=GHOST,
        day_id=uuid4(),
        side=PositionSide.long,
        qty=Decimal("1"),
        avg_entry_price=Decimal("100000"),
    )
    service_, repo, _ = service(by_id={str(position_id): position})

    result = await service_.close_position(owner_id=str(OWNER), position_id=str(position_id))

    assert result == {"ok": False, "error": "position_not_found"}
    assert repo.submitted == []


@pytest.mark.asyncio
async def test_close_position_accepts_own_position() -> None:
    position_id = uuid4()
    day_id = uuid4()
    position = Position(
        position_id=position_id,
        account_id=MANUAL,
        day_id=day_id,
        side=PositionSide.long,
        qty=Decimal("1"),
        avg_entry_price=Decimal("100000"),
    )
    service_, repo, _ = service(by_id={str(position_id): position})

    result = await service_.close_position(
        owner_id=str(OWNER), position_id=str(position_id), idempotency_key="close-1"
    )

    assert result["ok"] is True
    call = repo.submitted[0]
    assert str(call["account_id"]) == str(MANUAL)
    assert str(call["day_id"]) == str(day_id)


@pytest.mark.asyncio
async def test_close_position_malformed_id_is_not_found() -> None:
    service_, repo, _ = service()

    result = await service_.close_position(owner_id=str(OWNER), position_id="proto-1")

    assert result == {"ok": False, "error": "position_not_found"}
    assert repo.submitted == []


# ---------------------------------------------------------------------------
# get_command_status
# ---------------------------------------------------------------------------


def _cmd(owner_id) -> Command:
    return _command({
        "command_id": uuid4(), "owner_id": owner_id, "account_id": MANUAL,
        "day_id": uuid4(), "idempotency_key": "tg-1",
        "command_type": CommandType.submit_order,
    })


@pytest.mark.asyncio
async def test_command_status_of_another_owner_is_not_found() -> None:
    foreign = _cmd(OTHER_OWNER)
    mine = _cmd(OWNER)
    repo = FakeRepo(
        day=day(), accounts=ACCOUNTS,
        commands={str(foreign.command_id): foreign, str(mine.command_id): mine},
    )
    service_ = PaperTradingService(repo)

    other = await service_.get_command_status(str(foreign.command_id), owner_id=str(OWNER))
    own = await service_.get_command_status(str(mine.command_id), owner_id=str(OWNER))

    assert other == {"ok": False, "error": "command_not_found"}
    assert own["ok"] is True
    assert own["owner_id"] == str(OWNER)


@pytest.mark.asyncio
async def test_command_status_without_owner_stays_unscoped() -> None:
    """Совместимость: WebUI-путь до P4 звал статус без владельца."""
    foreign = _cmd(OTHER_OWNER)
    repo = FakeRepo(day=day(), accounts=ACCOUNTS, commands={str(foreign.command_id): foreign})
    service_ = PaperTradingService(repo)

    result = await service_.get_command_status(str(foreign.command_id))

    assert result["ok"] is True


@pytest.mark.asyncio
async def test_command_status_malformed_id_is_not_found() -> None:
    repo = FakeRepo(day=day(), accounts=ACCOUNTS)
    service_ = PaperTradingService(repo)

    result = await service_.get_command_status("cmd-1", owner_id=str(OWNER))

    assert result == {"ok": False, "error": "command_not_found"}


# ---------------------------------------------------------------------------
# load_ticket_inputs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ticket_inputs_come_from_the_ledger_not_the_client() -> None:
    held = Position(
        position_id=uuid4(),
        account_id=MANUAL,
        day_id=uuid4(),
        side=PositionSide.long,
        qty=Decimal("10"),
        avg_entry_price=Decimal("100000"),
        stop_loss=Decimal("99000"),
    )
    service_, _, current = service(
        positions={str(MANUAL): [held]},
        balance=Decimal("940.5"),
        realized_net=Decimal("-12.5"),
    )

    inputs = await service_.load_ticket_inputs(str(OWNER), str(MANUAL))

    assert inputs["ok"] is True
    assert inputs["day"] is not None and str(inputs["day_id"]) == str(current.day_id)
    assert str(inputs["account"].account_id) == str(MANUAL)
    assert inputs["available_margin"] == Decimal("940.5")
    assert inputs["open_positions"] == [held]
    # Знаковый реализованный результат: убыток отрицательный.
    assert inputs["realized_net"] == Decimal("-12.5")


@pytest.mark.asyncio
async def test_ticket_inputs_refuse_auto_account_and_foreign_owner() -> None:
    service_, _, _ = service()

    auto = await service_.load_ticket_inputs(str(OWNER), str(AUTO))
    foreign = await service_.load_ticket_inputs(str(OWNER), str(GHOST))

    assert auto["error"] == "no_manual_account"
    assert foreign["error"] == "account_not_owned"

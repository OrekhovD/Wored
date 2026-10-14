"""Q-24 / P4b — «ужесточить/сбросить лимиты» как доменная команда лимитов дня.

Три слоя проверки, потому что срез чинит три разных вранья:

* :mod:`paper_trading.ticket` — один источник формы лимитов: карточка показывает
  те же канонические строки, которые потом сверяет compare-and-swap, и не может
  подменить fail-closed правило риска числом из снапшота дня.
* :class:`paper_trading.runner.PaperTradingRunner` — ``set_automation`` раньше не
  имел ветки исполнения, поэтому команда «tighten» умирала молча. Теперь каждый
  отказ терминален и назван кодом, а проигранный CAS не перезаталкивается.
* реальный PostgreSQL (QA-схема, одноразовая) — идемпотентность по попытке для
  pause/resume/close_all и запись лимитов в ``paper_v2_days.settings_snapshot``.

PG-тесты скидываются, если QA-база недоступна, и никогда не трогают ``trading``.
"""
from __future__ import annotations

import json
import os
import uuid as _uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from paper_trading.contracts import (
    Command,
    CommandStatus,
    CommandType,
    DayState,
    TradingDay,
)
from paper_trading.risk import RiskSettings, canonical_limit
from paper_trading.runner import PaperTradingRunner
from paper_trading.strategy import BaselineV1Strategy
from paper_trading.ticket import (
    LIMIT_FIELDS,
    TicketDenied,
    apply_day_limits,
    current_limits,
    parse_limits,
)

NOW = datetime.now(timezone.utc)

#: различает «не указали день» и «явно день = None» (отказ no_active_day)
NO_DAY = object()

SNAPSHOT = {
    "max_risk_per_order_usdt": "10",
    "max_daily_loss_usdt": "50",
    "max_leverage": "10",
    "max_open_risk_usdt": "20",
    "min_net_rr": "1.0",
    #policy keys — лимиты дня не вправе их переписать
    "timezone": "Asia/Bangkok",
    "end_time_local": "21:00",
    "opening_capital": "1000",
}


def _day(snapshot: dict | None = None, state: DayState = DayState.running) -> TradingDay:
    return TradingDay(
        day_id=uuid4(),
        owner_id=uuid4(),
        state=state,
        settings_snapshot=dict(SNAPSHOT if snapshot is None else snapshot),
    )


def _command(
    *,
    day_id=NO_DAY,
    payload: dict | None = None,
    status: CommandStatus = CommandStatus.processing,
) -> Command:
    """Команда set_automation в том виде, в каком её отдаёт ``claim_command``:
    входной payload лежит в ``result`` (см. ``submit_command``). ``day_id`` по
    умолчанию настоящий; ``day_id=None`` передаётся явно — это другой случай."""
    return Command(
        command_id=uuid4(),
        owner_id=uuid4(),
        account_id=uuid4(),
        day_id=uuid4() if day_id is NO_DAY else day_id,
        command_type=CommandType.set_automation,
        idempotency_key=f"limits-{uuid4()}",
        request_hash="limits-hash",
        status=status,
        result=payload if payload is not None else {"limits": {"max_risk_per_order_usdt": "5"}},
        created_at=NOW,
        updated_at=NOW,
    )


class LimitsRepository:
    """Репозиторий ровно под ``_handle_set_automation``.

    ``complete_command`` повторяет guard настоящего
    :meth:`paper_trading.repository.PaperRepository.complete_command` —
    перезаписывается только строка ``processing``. Без этого guard'а тест не
    доказывал бы, что отказ не затёрт общим settle-проходом.
    """

    def __init__(self, *, applied: bool = True, raises: Exception | None = None) -> None:
        self.applied = applied
        self.raises = raises
        self.merge_calls: list[tuple] = []
        self.writes: list[tuple] = []
        self.statuses: dict = {}

    def seed(self, cmd: Command) -> Command:
        self.statuses[cmd.command_id] = cmd.status
        return cmd

    async def merge_day_limits(self, day_id, limits, expected=None) -> bool:
        self.merge_calls.append((day_id, dict(limits), dict(expected or {})))
        if self.raises is not None:
            raise self.raises
        return self.applied

    async def update_command_result(self, command_id, status, result=None, error=None) -> bool:
        self.writes.append((command_id, status, result, error))
        self.statuses[command_id] = status
        return True

    async def complete_command(self, command_id, result=None) -> bool:
        if self.statuses.get(command_id) == CommandStatus.processing:
            self.statuses[command_id] = CommandStatus.completed
            return True
        return self.statuses.get(command_id) in (
            CommandStatus.completed,
            CommandStatus.failed,
            CommandStatus.conflict,
        )


def _runner(repository) -> PaperTradingRunner:
    return PaperTradingRunner(strategy=BaselineV1Strategy(), repository=repository)


async def run_and_settle(repository, cmd: Command) -> bool:
    """Прогнать команду через runner вместе с общим settle-проходом."""
    runner = _runner(repository)
    processed = await runner._execute_command(cmd, NOW.timestamp())
    await runner._settle_command(cmd)
    return processed


# ---------------------------------------------------------------------------
# Форма лимитов: один источник для показа и для сверки
# ---------------------------------------------------------------------------


def test_canonical_limit_stays_readable_for_the_operator() -> None:
    """Карточка показывает число, а не научную нотацию, и сверяет ровно его же."""
    assert canonical_limit("10") == "10"
    assert canonical_limit("10.0") == "10"
    assert canonical_limit("1.0") == "1"
    assert canonical_limit("0.05") == "0.05"
    assert canonical_limit("100000") == "100000"
    assert canonical_limit(None) is None
    assert canonical_limit("None") == "None", "текст из базы не чинится молча"
    assert canonical_limit("abc") == "abc"


def test_current_limits_covers_every_limit_field_in_canonical_form() -> None:
    day = _day()
    limits, notes = current_limits(day)

    assert set(limits) == set(LIMIT_FIELDS), "показываем ровно те ключи, которые умеет писать домен"
    assert limits["max_risk_per_order_usdt"] == "10"
    assert isinstance(limits["max_risk_per_order_usdt"], str), (
        "строки, а не Decimal: карточка и CAS обязаны сверять один и тот же текст"
    )
    assert notes == [], "полный снапшот — ничего не подставлялось"


def test_current_limits_names_absent_key_instead_of_hiding_default() -> None:
    snapshot = {k: v for k, v in SNAPSHOT.items() if k != "min_net_rr"}
    limits, notes = current_limits(_day(snapshot))

    assert limits["min_net_rr"] is None, "отсутствие — это None, а не 0 и не дефолт"
    assert notes == ["min_net_rr"]


def test_parse_limits_refuses_unknown_key_and_field_name() -> None:
    # Поле RiskSettings не является ключом снапшота: именно эту подмену
    # ловил чат-экран, когда «/day limits max_daily_loss_usdt=25» упирался в
    # unknown_limit (ключ алиасится на поле, а не наоборот).
    with pytest.raises(TicketDenied) as exc:
        parse_limits({"max_daily_loss": "25"})
    assert exc.value.code == "unknown_limit"

    with pytest.raises(TicketDenied) as empty:
        parse_limits({"max_risk_per_order_usdt": ""})
    assert empty.value.code == "invalid_limits"

    with pytest.raises(TicketDenied) as bad:
        parse_limits({"max_risk_per_order_usdt": "abc"})
    assert bad.value.code == "invalid_number"


def test_parse_limits_returns_decimal_under_snapshot_keys() -> None:
    parsed = parse_limits({"max_risk_per_order_usdt": "5", "max_leverage": "3"})

    assert parsed == {"max_risk_per_order_usdt": Decimal("5"), "max_leverage": Decimal("3")}


def test_apply_day_limits_cannot_disable_fail_closed_rules() -> None:
    base = RiskSettings(
        require_risk_tier=True,
        cooldown_seconds=9.0,
        order_ttl_seconds=11.0,
        taker_fee_rate=Decimal("0.0005"),
    )
    snapshot = dict(SNAPSHOT)
    snapshot.update(
        {
            "max_risk_per_order_usdt": "1",
            "max_daily_loss_usdt": "5",
            "max_leverage": "2",
            "max_open_risk_usdt": "3",
            "min_net_rr": "3.0",
            # прямой вызов в обход parse_limits: даже случайное число в снапшоте
            # не имеет права выключать риск-правило процесса
            "require_risk_tier": "false",
            "cooldown_seconds": "0",
        }
    )

    applied = apply_day_limits(base, _day(snapshot))

    assert applied.max_risk_per_order == Decimal("1")
    assert applied.max_daily_loss == Decimal("5")
    assert applied.max_leverage == 2
    assert applied.max_open_risk == Decimal("3")
    assert applied.min_net_rr == Decimal("3.0")
    assert applied.require_risk_tier is True
    assert applied.cooldown_seconds == 9.0
    assert applied.order_ttl_seconds == 11.0
    assert applied.taker_fee_rate == Decimal("0.0005")


# ---------------------------------------------------------------------------
# Runner: отказ терминален и назван
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_automation_happy_path_writes_limits_and_result() -> None:
    repo = LimitsRepository(applied=True)
    cmd = repo.seed(_command(payload={"limits": {"max_risk_per_order_usdt": "5"}, "expected": {}}))
    runner = _runner(repo)

    processed = await runner._execute_command(cmd, NOW.timestamp())
    await runner._settle_command(cmd)

    assert processed is True, "обработано, а не отложено на следующий такт"
    assert repo.statuses[cmd.command_id] == CommandStatus.completed
    assert repo.merge_calls == [(cmd.day_id, {"max_risk_per_order_usdt": "5"}, {})]
    assert repo.writes[0] == (
        cmd.command_id,
        CommandStatus.completed,
        {"limits": {"max_risk_per_order_usdt": "5"}, "day_id": str(cmd.day_id)},
        None,
    )


@pytest.mark.asyncio
async def test_lost_compare_and_swap_is_failed_and_not_rewritten_by_settle() -> None:
    repo = LimitsRepository(applied=False)
    cmd = repo.seed(
        _command(payload={"limits": {"max_risk_per_order_usdt": "5"}, "expected": {"max_risk_per_order_usdt": "10"}})
    )
    runner = _runner(repo)

    processed = await runner._execute_command(cmd, NOW.timestamp())
    await runner._settle_command(cmd)

    assert processed is True, "проигранный CAS не переиздаётся: повтор перезаписал бы чужое изменение"
    assert repo.statuses[cmd.command_id] == CommandStatus.failed
    assert repo.writes[0] == (cmd.command_id, CommandStatus.failed, None, "limits_changed")


@pytest.mark.asyncio
async def test_set_automation_without_day_fails_before_touching_snapshot() -> None:
    repo = LimitsRepository()
    cmd = repo.seed(_command(day_id=None))

    assert await run_and_settle(repo, cmd) is True
    assert repo.merge_calls == []
    assert repo.writes == [(cmd.command_id, CommandStatus.failed, None, "no_active_day")]


@pytest.mark.asyncio
async def test_set_automation_without_limits_is_invalid_and_writes_nothing() -> None:
    repo = LimitsRepository()
    cmd = repo.seed(_command(payload={"limits": {}}))

    assert await run_and_settle(repo, cmd) is True
    assert repo.merge_calls == []
    assert repo.writes == [(cmd.command_id, CommandStatus.failed, None, "invalid_limits")]


@pytest.mark.asyncio
async def test_set_automation_write_failure_is_not_reported_as_success() -> None:
    repo = LimitsRepository(raises=RuntimeError("connection reset by peer"))
    cmd = repo.seed(_command())
    runner = _runner(repo)

    processed = await runner._execute_command(cmd, NOW.timestamp())

    assert processed is True
    assert repo.statuses[cmd.command_id] == CommandStatus.failed
    assert repo.writes[0][3] == "limits_write_failed"
    assert "connection reset" in str(runner._last_error)


# ---------------------------------------------------------------------------
# Реальный PostgreSQL: идемпотентность по попытке + snapshot дня
# ---------------------------------------------------------------------------

DSN = os.getenv(
    "WORED_TEST_DATABASE_URL", "postgresql://qa:disposable-qa-only@postgres-qa:5432/wored_qa"
)
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "migrations" / "paper_v2_schema.sql"


@pytest.fixture
async def repo():
    """Одноразовая схема на QA-базе; ``PaperRepository`` поверх настоящего DDL."""
    if DSN.rstrip("/").endswith("/trading"):
        pytest.fail("refusing to run against production 'trading' database")
    schema = f"day_limits_{_uuid.uuid4().hex[:8]}"
    try:
        import asyncpg
    except ImportError:  # pragma: no cover - среда без asyncpg
        pytest.skip("asyncpg is not installed")
    try:
        admin = await asyncpg.connect(DSN)
    except Exception as exc:  # pragma: no cover - зависит от окружения
        pytest.skip(f"QA database not reachable: {exc}")
    try:
        db = await admin.fetchval("SELECT current_database()")
        if db == "trading":
            pytest.fail("refusing to run against production database 'trading'")
        await admin.execute(f'CREATE SCHEMA "{schema}"')
        await admin.execute(f'SET search_path TO "{schema}", public')
        await admin.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - зависит от окружения
        pytest.skip(f"could not prepare QA schema: {exc}")
    finally:
        await admin.close()

    from paper_trading.repository import PaperRepository

    pool = await asyncpg.create_pool(
        DSN, min_size=2, max_size=4, server_settings={"search_path": schema}
    )
    try:
        yield PaperRepository(pool)
    finally:
        await pool.close()
        conn = await asyncpg.connect(DSN)
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()


async def _seed_owner(repo) -> _uuid.UUID:
    from uuid import uuid4 as _u

    owner_id = _u()
    async with repo.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO paper_v2_owners (owner_id, display_name, created_at, schema_version) "
            "VALUES ($1, $2, NOW(), 2)",
            str(owner_id), f"owner-{str(owner_id)[:8]}",
        )
        for kind in ("manual", "auto"):
            await conn.execute(
                "INSERT INTO paper_v2_accounts (account_id, owner_id, kind, currency, "
                "opening_deposit, created_at, schema_version) VALUES ($1, $2, $3, 'USDT', "
                "1000, NOW(), 2)",
                str(_u()), str(owner_id), kind,
            )
    return owner_id


async def _seed_day(repo, owner_id, snapshot: dict | None = None) -> _uuid.UUID:
    from uuid import uuid4 as _u

    day_id = _u()
    async with repo.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO paper_v2_days (day_id, owner_id, state, settings_snapshot, "
            "created_at, schema_version) VALUES ($1, $2, 'running', $3::jsonb, NOW(), 2)",
            str(day_id), str(owner_id), json.dumps(dict(SNAPSHOT if snapshot is None else snapshot)),
        )
    return day_id


async def _command_count(repo, owner_id) -> int:
    async with repo.pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM paper_v2_commands WHERE owner_id = $1", str(owner_id)
        )


class TestAutoControlIdempotencyScopedToAttempt:
    """pause/resume/close_all ключ живёт по попытке, а не по дню (Q-24)."""

    async def test_repeated_pause_after_resume_creates_new_command(self, repo) -> None:
        from paper_trading.service import PaperTradingService

        owner_id = await _seed_owner(repo)
        await _seed_day(repo, owner_id)
        svc = PaperTradingService(repo)

        first = await svc.pause_auto(str(owner_id))
        resume = await svc.resume_auto(str(owner_id))
        second = await svc.pause_auto(str(owner_id))

        assert first["ok"] and resume["ok"] and second["ok"]
        assert first["command_id"] != second["command_id"], (
            "законное «пауза → продолжить → пауза» внутри дня не может быть "
            "повторной выдачей уже исполненной команды"
        )
        assert await _command_count(repo, owner_id) == 3

    async def test_explicit_key_twice_replays_same_command(self, repo) -> None:
        """Двойное нажатие на одну карточку подтверждает одну команду."""
        from paper_trading.service import PaperTradingService

        owner_id = await _seed_owner(repo)
        await _seed_day(repo, owner_id)
        svc = PaperTradingService(repo)
        key = f"tg-{owner_id}-ticket-1"

        first = await svc.pause_auto(str(owner_id), idempotency_key=key)
        second = await svc.pause_auto(str(owner_id), idempotency_key=key)

        assert second["ok"] is True
        assert second["command_id"] == first["command_id"]
        assert await _command_count(repo, owner_id) == 1

    async def test_conflicting_payload_under_one_key_reports_existing_command(self, repo) -> None:
        from paper_trading.service import PaperTradingService

        owner_id = await _seed_owner(repo)
        await _seed_day(repo, owner_id)
        svc = PaperTradingService(repo)
        key = f"tg-{owner_id}-ticket-2"

        first = await svc.close_all_positions(str(owner_id), idempotency_key=key, block_entries=True)
        clash = await svc.close_all_positions(str(owner_id), idempotency_key=key, block_entries=False)

        assert clash["ok"] is False
        assert clash["error"] == "idempotency_conflict"
        assert clash["command_id"] == first["command_id"], (
            "конфликт должен назвать ту команду, что заняла ключ, а не молча "
            "выдать новую (MC-15)"
        )
        assert await _command_count(repo, owner_id) == 1

    async def test_close_all_scopes_to_the_auto_account(self, repo) -> None:
        """``close_all`` без счёта в домене значит «все позиции исполнителя»."""
        from paper_trading.contracts import AccountKind
        from paper_trading.service import PaperTradingService

        owner_id = await _seed_owner(repo)
        await _seed_day(repo, owner_id)
        svc = PaperTradingService(repo)
        auto = await svc.repo.get_account_by_kind(owner_id, AccountKind("auto"))

        res = await svc.close_all_positions(str(owner_id))
        async with repo.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT account_id, day_id, command_type FROM paper_v2_commands WHERE command_id = $1",
                res["command_id"],
            )

        assert str(row["account_id"]) == str(auto.account_id), (
            "ручной счёт не может попасть под закрытие автомата"
        )
        assert row["command_type"] == CommandType.close_all.value


class TestMergeDayLimitsCompareAndSwap:
    async def test_match_writes_only_submitted_keys(self, repo) -> None:
        owner_id = await _seed_owner(repo)
        day_id = await _seed_day(repo, owner_id)

        applied = await repo.merge_day_limits(
            day_id,
            {"max_risk_per_order_usdt": "5"},
            {"max_risk_per_order_usdt": "10.0"},  # та же величина в другой форме
        )
        day = await repo.get_active_day(owner_id)

        assert applied is True, "сверка идёт по canonical_limit, а не по тексту карточки"
        snap = dict(day.settings_snapshot)
        assert snap["max_risk_per_order_usdt"] == "5"
        assert snap["timezone"] == "Asia/Bangkok"
        assert snap["end_time_local"] == "21:00"
        assert snap["opening_capital"] == "1000"
        assert snap["max_daily_loss_usdt"] == "50", "не названные ключи не двигаются"

    async def test_mismatch_changes_nothing(self, repo) -> None:
        owner_id = await _seed_owner(repo)
        day_id = await _seed_day(repo, owner_id)

        applied = await repo.merge_day_limits(
            day_id,
            {"max_risk_per_order_usdt": "5"},
            {"max_risk_per_order_usdt": "8"},
        )
        day = await repo.get_active_day(owner_id)

        assert applied is False
        assert dict(day.settings_snapshot)["max_risk_per_order_usdt"] == "10"

    async def test_absent_key_compares_to_none_not_to_text(self, repo) -> None:
        owner_id = await _seed_owner(repo)
        snapshot = {k: v for k, v in SNAPSHOT.items() if k != "min_net_rr"}
        day_id = await _seed_day(repo, owner_id, snapshot)

        wrong = await repo.merge_day_limits(day_id, {"min_net_rr": "1.5"}, {"min_net_rr": "None"})
        right = await repo.merge_day_limits(day_id, {"min_net_rr": "1.5"}, {"min_net_rr": None})
        day = await repo.get_active_day(owner_id)

        assert wrong is False, "отсутствие ключа и строка «None» — разные состояния"
        assert right is True
        assert dict(day.settings_snapshot)["min_net_rr"] == "1.5"

    async def test_empty_limits_is_not_a_write(self, repo) -> None:
        owner_id = await _seed_owner(repo)
        day_id = await _seed_day(repo, owner_id)

        assert await repo.merge_day_limits(day_id, {}) is False


class TestDayLimitsCommandEndToEnd:
    async def test_unknown_key_is_refused_before_any_command_row(self, repo) -> None:
        from paper_trading.service import PaperTradingService

        owner_id = await _seed_owner(repo)
        await _seed_day(repo, owner_id)
        svc = PaperTradingService(repo)

        res = await svc.set_day_limits(str(owner_id), {"max_daily_loss": "25"})

        assert res["ok"] is False
        assert res["error"] == "unknown_limit"
        assert await _command_count(repo, owner_id) == 0, (
            "отказ обязан произойти до очереди: команда, которую runner не "
            "сможет честно исполнить, не существует"
        )

    async def test_confirmed_limits_reach_the_day_the_operator_reads(self, repo) -> None:
        from paper_trading.service import PaperTradingService

        owner_id = await _seed_owner(repo)
        await _seed_day(repo, owner_id)
        svc = PaperTradingService(repo)
        shown = await svc.load_day_limits(str(owner_id))
        assert shown["ok"] is True

        submit = await svc.set_day_limits(
            str(owner_id),
            {"max_risk_per_order_usdt": "5", "max_daily_loss_usdt": "25"},
            expected=shown["limits"],
        )
        assert submit["ok"] is True

        claimed = await repo.claim_command(_uuid.UUID(submit["command_id"]))
        assert claimed is not None
        runner = PaperTradingRunner(strategy=BaselineV1Strategy(), repository=repo)
        processed = await runner._execute_command(claimed, NOW.timestamp())
        await runner._settle_command(claimed)
        assert processed is True

        after = await svc.load_day_limits(str(owner_id))
        status = await svc.get_command_status(submit["command_id"], owner_id=str(owner_id))

        assert after["limits"]["max_risk_per_order_usdt"] == "5"
        assert after["limits"]["max_daily_loss_usdt"] == "25"
        assert after["limits"]["max_leverage"] == "10", "названное меняется, остальное остаётся"
        assert status["status"] == CommandStatus.completed.value
        assert status["result"]["limits"]["max_risk_per_order_usdt"] == "5"

    async def test_stale_confirmation_ends_as_limits_changed(self, repo) -> None:
        """Пока карточка лежала неподтверждённой, лимит успели изменить."""
        from paper_trading.service import PaperTradingService

        owner_id = await _seed_owner(repo)
        await _seed_day(repo, owner_id)
        svc = PaperTradingService(repo)
        shown = await svc.load_day_limits(str(owner_id))
        day_id = _uuid.UUID(shown["day_id"])

        submit = await svc.set_day_limits(
            str(owner_id), {"max_risk_per_order_usdt": "5"}, expected=shown["limits"]
        )
        # постороннее изменение успевает записаться раньше
        await repo.merge_day_limits(day_id, {"max_risk_per_order_usdt": "7"}, None)

        claimed = await repo.claim_command(_uuid.UUID(submit["command_id"]))
        runner = PaperTradingRunner(strategy=BaselineV1Strategy(), repository=repo)
        processed = await runner._execute_command(claimed, NOW.timestamp())
        await runner._settle_command(claimed)

        status = await svc.get_command_status(submit["command_id"], owner_id=str(owner_id))
        after = await svc.load_day_limits(str(owner_id))

        assert processed is True
        assert status["status"] == CommandStatus.failed.value, (
            "подтверждённое одним оператором не может перезаписать чужое изменение"
        )
        assert status["error"] == "limits_changed"
        assert after["limits"]["max_risk_per_order_usdt"] == "7"

    async def test_limits_are_refused_when_day_is_not_tradable(self, repo) -> None:
        from paper_trading.service import PaperTradingService

        owner_id = await _seed_owner(repo)
        day_id = await _seed_day(repo, owner_id)
        svc = PaperTradingService(repo)
        await repo.update_day_state(day_id, DayState.closing)

        res = await svc.set_day_limits(str(owner_id), {"max_risk_per_order_usdt": "5"})

        assert res["ok"] is False
        assert res["error"] == "day_not_active"
        assert res["day_state"] == DayState.closing.value
        assert await _command_count(repo, owner_id) == 0

    async def test_command_status_is_owner_scoped(self, repo) -> None:
        from paper_trading.service import PaperTradingService

        owner_id = await _seed_owner(repo)
        await _seed_day(repo, owner_id)
        stranger = await _seed_owner(repo)
        svc = PaperTradingService(repo)

        submit = await svc.set_day_limits(str(owner_id), {"max_risk_per_order_usdt": "5"})

        leaked = await svc.get_command_status(submit["command_id"], owner_id=str(stranger))
        assert leaked["ok"] is False
        assert leaked["error"] == "command_not_found", (
            "чужая команда отдаётся так же, как несуществующая: её payload "
            "содержит параметры ордера"
        )

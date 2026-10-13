"""Paper Trading Service — commands for day, manual and auto accounts.

This is the domain service that Telegram and WebUI adapters call.
It does NOT import FastAPI Request, Telegram types, or UI templates.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from paper_trading.contracts import (
    Account,
    AccountKind,
    CommandType,
    Position,
)
from paper_trading.repository import IdempotencyConflict, PaperRepository
from paper_trading.ticket import TRADABLE_DAY_STATES, day_state_value

log = logging.getLogger(__name__)


def _as_uuid(value: UUID | str) -> UUID:
    """Normalise a str/UUID identifier to UUID for repository calls."""
    return UUID(value) if isinstance(value, str) else value

# Default risk settings for new owner without saved settings
DEFAULT_SETTINGS = {
    "opening_capital": "1000",
    "max_risk_per_order_usdt": "10",
    "max_daily_loss_usdt": "50",
    "max_leverage": "10",
    "max_open_risk_usdt": "20",
    "max_positions_per_instrument": "1",
    "end_time_local": "21:00",
    "timezone": "Asia/Bangkok",
}


@dataclass
class StartDayRequest:
    owner_id: str
    timezone: str = "Asia/Bangkok"
    end_time_local: str = "21:00"
    settings: dict[str, Any] | None = None
    strategy_version: str = "baseline_v1"
    mode: str = "baseline_auto"  # or "ai_plan"


class PaperTradingService:
    """Domain service for paper trading day operations."""

    def __init__(self, repo: PaperRepository):
        self.repo = repo

    async def start_day(self, req: StartDayRequest) -> dict[str, Any]:
        """Start a new trading day for both manual and auto accounts."""
        # Ensure owner exists
        try:
            await self.repo.create_owner(_as_uuid(req.owner_id), display_name=f"owner-{req.owner_id[:8]}")
        except Exception:
            pass  # owner already exists is OK

        # Ensure both accounts exist with default capital
        # Persist the *selected* day policy into the snapshot so an automated
        # rollover can inherit it (timezone / end_time_local / mode /
        # strategy_version) instead of falling back to a hidden default.
        policy = {
            "timezone": req.timezone,
            "end_time_local": req.end_time_local,
            "mode": req.mode,
            "strategy_version": req.strategy_version,
        }
        settings = {**DEFAULT_SETTINGS, **(req.settings or {}), **policy}
        capital = Decimal(settings["opening_capital"])

        manual = await self._get_or_create_account(req.owner_id, "manual", "USDT", capital)
        auto = await self._get_or_create_account(req.owner_id, "auto", "USDT", capital)

        # Check no unclosed day exists.  A running, closing OR settlement_pending
        # day is an active incomplete day and must be finished before a new one
        # is started — the partial unique index uq_days_one_incomplete forbids a
        # second one, so treating settlement_pending as "passable" here would
        # only make create_day fail later.
        existing = await self.repo.get_active_day(_as_uuid(req.owner_id))
        if existing and existing.state != "closed":
            return {
                "ok": False,
                "error": "day_already_active",
                "day_id": str(existing.day_id),
                "state": str(existing.state),
            }

        # Create new day
        now = datetime.now(timezone.utc)
        # Compute end_utc from end_time_local + timezone
        from datetime import timedelta
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(req.timezone)
            # Parse end_time_local (e.g. "21:00")
            end_hour, end_min = map(int, req.end_time_local.split(":"))
            # Today's end time in local tz, convert to UTC
            local_now = now.astimezone(tz)
            local_end = local_now.replace(hour=end_hour, minute=end_min, second=0, microsecond=0)
            # If end time already passed today, it's tomorrow — but don't silently extend
            if local_end <= local_now:
                # Use 8h from start as fallback (should not happen — UI should prevent this)
                end_utc = now + timedelta(hours=8)
            else:
                end_utc = local_end.astimezone(timezone.utc)
        except Exception:
            # Fallback: 8h from start
            end_utc = now + timedelta(hours=8)

        day = await self.repo.create_day(
            day_id=uuid4(),
            owner_id=UUID(req.owner_id) if isinstance(req.owner_id, str) else req.owner_id,
            timezone=req.timezone,
            start_utc=now,
            end_utc=end_utc,
            settings_snapshot=settings,
            strategy_version=req.strategy_version,
        )

        # Submit start commands for both accounts
        from paper_trading.contracts import CommandType
        cmd_manual = await self.repo.submit_command(
            command_id=uuid4(),
            owner_id=UUID(req.owner_id) if isinstance(req.owner_id, str) else req.owner_id,
            idempotency_key=f"start-{day.day_id}-manual",
            command_type=CommandType("start_day"),
            payload={"account_kind": "manual", "mode": req.mode},
            account_id=UUID(str(manual.account_id)) if manual else None,
            day_id=UUID(str(day.day_id)) if day else None,
        )
        cmd_auto = await self.repo.submit_command(
            command_id=uuid4(),
            owner_id=UUID(req.owner_id) if isinstance(req.owner_id, str) else req.owner_id,
            idempotency_key=f"start-{day.day_id}-auto",
            command_type=CommandType("start_day"),
            payload={"account_kind": "auto", "mode": req.mode},
            account_id=UUID(str(auto.account_id)) if auto else None,
            day_id=UUID(str(day.day_id)) if day else None,
        )

        return {
            "ok": True,
            "day_id": str(day.day_id),
            "manual_account_id": str(manual.account_id),
            "auto_account_id": str(auto.account_id),
            "start_at": now.isoformat(),
            "end_at": end_utc.isoformat(),
            "commands": [str(cmd_manual), str(cmd_auto)],
        }

    async def _get_or_create_account(self, owner_id: str, kind: str, currency: str, opening_deposit: Decimal):
        """Get or create an account for an owner."""

        owner_uuid = UUID(owner_id) if isinstance(owner_id, str) else owner_id
        kind_enum = AccountKind(kind) if isinstance(kind, str) else kind

        try:
            account_id = uuid4()
            return await self.repo.create_account(
                account_id=account_id,
                owner_id=owner_uuid,
                kind=kind_enum,
                currency=currency,
                opening_deposit=opening_deposit,
            )
        except Exception:
            # Account already exists — find it
            return await self.get_account_by_kind(owner_uuid, kind_enum)

    async def get_current_state(self, owner_id: str) -> dict[str, Any]:
        """Get current trading day state with both accounts."""
        day = await self.repo.get_active_day(_as_uuid(owner_id))
        if not day:
            return {
                "ok": True,
                "day": None,
                "next_action": "start",
                "accounts": [],
            }

        manual = await self.repo.get_account_by_kind(_as_uuid(owner_id), AccountKind("manual"))
        auto = await self.repo.get_account_by_kind(_as_uuid(owner_id), AccountKind("auto"))

        accounts = []
        for acct, kind in [(manual, "manual"), (auto, "auto")]:
            if not acct:
                continue
            positions = await self.repo.get_open_positions_by_account(_as_uuid(acct.account_id))
            balance = await self.repo.get_account_balance(_as_uuid(acct.account_id))
            accounts.append({
                "id": str(acct.account_id),
                "kind": kind,
                "currency": acct.currency,
                "cash": str(balance),
                "open_positions": len(positions),
                "positions": [
                    {
                        "id": str(p.position_id),
                        "side": p.side,
                        "qty": str(p.qty),
                        "avg_entry": str(p.avg_entry),
                        "stop": str(p.stop_loss) if p.stop_loss else None,
                        "target": str(p.take_profit) if p.take_profit else None,
                    }
                    for p in positions
                ],
            })

        return {
            "ok": True,
            "day": {
                "id": str(day.day_id),
                "state": day.state,
                "start_at": day.start_utc.isoformat() if day.start_utc else None,
                "end_at": day.end_utc.isoformat() if day.end_utc else None,
            },
            "accounts": accounts,
            "next_action": "trade" if day.state == "running" else "start",
        }

    async def _manual_account(self, owner_uuid, account_id):
        """Проверить счёт под ручную сделку. Возвращает (account, error).

        Порядок фиксирован: сначала владение, потом тип счёта. Чужой счёт
        отвечается как ``account_not_owned`` (не «не найден»), потому что
        caller уже назвал его идентификатор в своём запросе.
        """
        try:
            acct_uuid = _as_uuid(account_id)
        except (TypeError, ValueError):
            return None, "account_not_found"
        account = await self.repo.get_account(acct_uuid)
        if account is None:
            return None, "account_not_found"
        if str(account.owner_id) != str(owner_uuid):
            return None, "account_not_owned"
        if account.kind != AccountKind.manual:
            return None, "no_manual_account"
        return account, None

    async def load_ticket_inputs(self, owner_id: str, account_id: str) -> dict[str, Any]:
        """Собрать входы доменного тикета из authoritative состояния.

        Единый источник и для WebUI-презентера, и для Telegram: лимиты — из
        ``settings_snapshot`` дня, доступная маржа — из журнала проводок,
        открытые позиции и реализованный результат — из БД. Ни одно из этих
        чисел не приходит от клиента; рынок (snapshot) добавляет вызывающая
        сторона, потому что её читатель — единственная вещь, отличающая UI от
        чата.
        """
        owner_uuid = _as_uuid(owner_id)
        day = await self.repo.get_active_day(owner_uuid)
        if not day:
            return {"ok": False, "error": "no_active_day"}
        account, error = await self._manual_account(owner_uuid, account_id)
        if error:
            return {"ok": False, "error": error}
        balance = await self.repo.get_account_balance(account.account_id)
        positions = await self.repo.get_open_positions_by_account(account.account_id)
        realized_net = await self.repo.get_day_realized_net(account.account_id, day.day_id)
        return {
            "ok": True,
            "day": day,
            "account": account,
            "available_margin": balance,
            "open_positions": positions,
            "realized_net": realized_net,
            "day_id": str(day.day_id),
            "account_id": str(account.account_id),
        }

    async def submit_manual_order(
        self,
        owner_id: str,
        account_id: str,
        side: str,
        order_type: str,
        qty: Decimal,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        idempotency_key: str | None = None,
        expected_day_id: str | None = None,
    ) -> dict[str, Any]:
        """Submit a manual order.

        P4: this is the execution gate, not just a writer. The runner starts
        from the premise that "the preview already validated risk", so the
        domain has to verify the facts that no client may decide for itself:
        the account exists, belongs to this owner, is the *manual* one, and
        belongs to a tradable day. Checks run before ``submit_command``; the
        command itself is still recorded exactly once (MC-14: a manual
        account command never touches the auto account).
        """
        owner_uuid = _as_uuid(owner_id)
        day = await self.repo.get_active_day(owner_uuid)
        if not day:
            return {"ok": False, "error": "no_active_day"}

        account, error = await self._manual_account(owner_uuid, account_id)
        if error:
            return {"ok": False, "error": error}

        day_state = day_state_value(day)
        if day_state not in TRADABLE_DAY_STATES:
            return {"ok": False, "error": "day_not_active", "day_state": day_state}
        if expected_day_id is not None and str(day.day_id) != str(expected_day_id):
            # Тикет был собран для другого дня: между preview и confirm день
            # закрылся и открылся новый. Считать за прежний день нельзя.
            return {"ok": False, "error": "day_mismatch", "day_id": str(day.day_id)}

        key = idempotency_key or f"order-{uuid4()}"
        try:
            cmd = await self.repo.submit_command(
                command_id=uuid4(),
                owner_id=owner_uuid,
                idempotency_key=key,
                # Enum name is `submit_order` (see paper_trading/contracts.py). The
                # historical value `place_order` never existed on the enum and
                # raised "'place_order' is not a valid CommandType" on submit.
                command_type=CommandType.submit_order,
                payload={
                    "side": side,
                    "order_type": order_type,
                    "qty": str(qty),
                    "stop_loss": str(stop_loss) if stop_loss else None,
                    "take_profit": str(take_profit) if take_profit else None,
                    "origin": "manual",
                    "actor": "user",
                },
                account_id=account.account_id,
                day_id=UUID(str(day.day_id)),
            )
        except IdempotencyConflict:
            # Конфликт идемпотентности — не шёпот: отдаём id команды, которой
            # занят ключ, чтобы клиент мог показать статус вместо «не удалось».
            existing = await self.repo.find_command_by_idempotency_key(owner_uuid, key)
            return {
                "ok": False,
                "error": "idempotency_conflict",
                "command_id": str(existing.command_id) if existing else None,
            }
        return {"ok": True, "command_id": str(cmd.command_id)}

    async def close_position(
        self,
        owner_id: str,
        position_id: str,
        partial_qty: Decimal | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Close a position (full or partial).

        Ownership is enforced here, not in the caller: the command's account is
        derived from the position, so a runner-side account match would always
        pass. A position that is not the caller's reports the same
        ``position_not_found`` as a position that does not exist, so the error
        surface cannot be used to probe other owners' positions.
        """
        owner_uuid = _as_uuid(owner_id)
        try:
            pos = await self.repo.get_position_by_id(_as_uuid(position_id))
        except (TypeError, ValueError):
            return {"ok": False, "error": "position_not_found"}
        if not pos:
            return {"ok": False, "error": "position_not_found"}

        account = await self.repo.get_account(_as_uuid(pos.account_id))
        if account is None or str(account.owner_id) != str(owner_uuid):
            return {"ok": False, "error": "position_not_found"}

        key = idempotency_key or f"close-{position_id}-{uuid4()}"
        try:
            cmd = await self.repo.submit_command(
                command_id=uuid4(),
                owner_id=owner_uuid,
                idempotency_key=key,
                command_type=CommandType("close_position"),
                payload={
                    "position_id": position_id,
                    "partial_qty": str(partial_qty) if partial_qty else None,
                },
                account_id=_as_uuid(pos.account_id),
                day_id=_as_uuid(pos.day_id),
            )
        except IdempotencyConflict:
            existing = await self.repo.find_command_by_idempotency_key(owner_uuid, key)
            return {
                "ok": False,
                "error": "idempotency_conflict",
                "command_id": str(existing.command_id) if existing else None,
            }
        return {"ok": True, "command_id": str(cmd.command_id)}

    async def pause_auto(self, owner_id: str) -> dict[str, Any]:
        """Pause automatic trading (new entries blocked, positions protected)."""
        day = await self.repo.get_active_day(_as_uuid(owner_id))
        if not day:
            return {"ok": False, "error": "no_active_day"}

        auto = await self.repo.get_account_by_kind(_as_uuid(owner_id), AccountKind("auto"))
        if not auto:
            return {"ok": False, "error": "no_auto_account"}

        cmd = await self.repo.submit_command(
            command_id=uuid4(),
            owner_id=UUID(owner_id) if isinstance(owner_id, str) else owner_id,
            idempotency_key=f"pause-{day.day_id}",
            command_type=CommandType("pause_auto"),
            payload={},
            account_id=UUID(str(auto.account_id)),
            day_id=UUID(str(day.day_id)),
        )
        return {"ok": True, "command_id": str(cmd.command_id)}

    async def resume_auto(self, owner_id: str) -> dict[str, Any]:
        """Resume automatic trading."""
        day = await self.repo.get_active_day(_as_uuid(owner_id))
        if not day:
            return {"ok": False, "error": "no_active_day"}

        auto = await self.repo.get_account_by_kind(_as_uuid(owner_id), AccountKind("auto"))
        if not auto:
            return {"ok": False, "error": "no_auto_account"}

        cmd = await self.repo.submit_command(
            command_id=uuid4(),
            owner_id=UUID(owner_id) if isinstance(owner_id, str) else owner_id,
            idempotency_key=f"resume-{day.day_id}",
            command_type=CommandType("resume_auto"),
            payload={},
            account_id=UUID(str(auto.account_id)),
            day_id=UUID(str(day.day_id)),
        )
        return {"ok": True, "command_id": str(cmd.command_id)}

    async def finish_day(self, owner_id: str) -> dict[str, Any]:
        """Finish the trading day — closeout both accounts."""
        day = await self.repo.get_active_day(_as_uuid(owner_id))
        if not day:
            return {"ok": False, "error": "no_active_day"}

        cmd = await self.repo.submit_command(
            command_id=uuid4(),
            owner_id=UUID(owner_id) if isinstance(owner_id, str) else owner_id,
            idempotency_key=f"finish-{day.day_id}",
            command_type=CommandType("finish_day"),
            payload={},
            day_id=UUID(str(day.day_id)),
        )
        return {"ok": True, "command_id": str(cmd.command_id)}

    async def get_command_status(self, command_id: str, owner_id: str | None = None) -> dict[str, Any]:
        """Get command status by ID.

        ``owner_id`` is optional for compatibility, but every console that
        shows a command to a human must pass it: ``paper_v2_commands`` rows
        belong to an owner, and answering for someone else's command would
        leak their order parameters. A foreign id answers the same
        ``command_not_found`` as a missing one.
        """
        try:
            cmd = await self.repo.get_command(_as_uuid(command_id))
        except (TypeError, ValueError):
            return {"ok": False, "error": "command_not_found"}
        if not cmd:
            return {"ok": False, "error": "command_not_found"}
        if owner_id is not None and str(cmd.owner_id) != str(_as_uuid(owner_id)):
            return {"ok": False, "error": "command_not_found"}
        return {
            "ok": True,
            "command_id": str(cmd.command_id),
            "owner_id": str(cmd.owner_id),
            "status": cmd.status,
            "result": cmd.result,
            "error": cmd.error,
        }

    # ------------------------------------------------------------------
    # Delegate methods (thin wrappers over repository)
    # ------------------------------------------------------------------

    async def get_account_by_kind(self, owner_id, kind) -> Account | None:
        """Get the account for an owner with the given kind, or None."""
        from uuid import UUID
        owner_uuid = UUID(owner_id) if isinstance(owner_id, str) else owner_id
        kind_enum = AccountKind(kind) if isinstance(kind, str) else kind
        return await self.repo.get_account_by_kind(owner_uuid, kind_enum)

    async def get_open_positions_by_account(self, account_id) -> list[Position]:
        """Get all open positions for an account."""
        from uuid import UUID
        acct_uuid = UUID(account_id) if isinstance(account_id, str) else account_id
        return await self.repo.get_open_positions_by_account(acct_uuid)

    async def get_account_balance(self, account_id) -> Decimal:
        """Return the current balance (sum of postings) for an account."""
        from uuid import UUID
        acct_uuid = UUID(account_id) if isinstance(account_id, str) else account_id
        return await self.repo.get_account_balance(acct_uuid)

    async def get_position_by_id(self, position_id) -> Position | None:
        """Get a position by its ID, or None."""
        from uuid import UUID
        pos_uuid = UUID(position_id) if isinstance(position_id, str) else position_id
        return await self.repo.get_position_by_id(pos_uuid)

    # ------------------------------------------------------------------
    # Day reports (F05)
    # ------------------------------------------------------------------

    async def list_day_reports(self, owner_id: str, limit: int = 30) -> list[dict[str, Any]]:
        """Return summary cards for recent closed days."""
        days = await self.repo.get_closed_days(_as_uuid(owner_id), limit=limit)
        results = []
        for day in days:
            positions = await self.repo.get_all_positions_by_day(day.day_id)
            closed = [p for p in positions if p.status.value == "closed"]
            # Position.realized_net_pnl is already net of fees (see execution.
            # close_position: realized_net = gross - close_fee - allocated_entry_fee).
            # The card shows net_pnl directly and fees alongside as a breakdown,
            # NOT a value to be subtracted from net_pnl again.
            net_pnl_total = sum((p.realized_net_pnl for p in closed), Decimal(0))
            total_fees = sum((p.entry_fee + p.exit_fee for p in closed), Decimal(0))
            wins = sum(1 for p in closed if p.realized_net_pnl > Decimal(0))
            results.append({
                "day_id": str(day.day_id),
                "date": day.end_utc.strftime("%Y-%m-%d") if day.end_utc else "—",
                "state": day.state.value,
                "strategy_version": day.strategy_version,
                "trades": len(closed),
                "wins": wins,
                "losses": len(closed) - wins,
                "net_pnl": str(net_pnl_total),
                "fees": str(total_fees),
            })
        return results

    async def get_day_report(self, owner_id: str, day_id: str) -> dict[str, Any] | None:
        """Build a full day report with reconciliation.

        Returns None if the day is not found or not owned by this owner.
        """
        from paper_trading.presenters import format_report
        from paper_trading.ledger import reconcile

        day = await self.repo.get_day(_as_uuid(day_id))
        if not day or str(day.owner_id) != str(_as_uuid(owner_id)):
            return None

        # Collect positions for the day
        positions = await self.repo.get_all_positions_by_day(day.day_id)
        closed = [p for p in positions if p.status.value == "closed"]
        open_pos = [p for p in positions if p.status.value == "open"]

        # Get accounts for the owner
        manual = await self.repo.get_account_by_kind(_as_uuid(owner_id), AccountKind.manual)
        auto = await self.repo.get_account_by_kind(_as_uuid(owner_id), AccountKind.auto)

        all_reports = []
        for acct, kind_label in [(manual, "manual"), (auto, "auto")]:
            if not acct:
                continue
            acct_positions = [p for p in positions if str(p.account_id) == str(acct.account_id)]
            acct_closed = [p for p in acct_positions if p.status.value == "closed"]
            acct_open = [p for p in acct_positions if p.status.value == "open"]

            realized_net = sum((p.realized_net_pnl for p in acct_closed), Decimal(0))
            realized_gross = sum((p.realized_gross_pnl for p in acct_closed), Decimal(0))
            total_fees = sum((p.entry_fee + p.exit_fee for p in acct_closed), Decimal(0))
            wins = sum(1 for p in acct_closed if p.realized_net_pnl > Decimal(0))

            # format_report derives net_pnl = realized_pnl - total_fees, so we
            # pass the GROSS figure here. Passing the net figure used to
            # subtract fees a second time.
            report = format_report(
                day_id=str(day.day_id),
                account_id=str(acct.account_id),
                account_label=kind_label,
                opening_capital=acct.opening_deposit,
                realized_pnl=realized_gross,
                total_fees=total_fees,
                trades_count=len(acct_closed),
                wins=wins,
                losses=len(acct_closed) - wins,
                positions=acct_open,
                strategy_version=day.strategy_version,
            )

            # Ledger reconciliation
            try:
                postings = await self.repo.get_postings_for_account(acct.account_id)
                actual_cash = await self.repo.get_account_balance(acct.account_id)
                # ``get_account_balance`` sums non-pnl buckets, which already
                # includes the opening deposit posting inserted in
                # ``repo.create_account``. Passing ``opening_deposit`` as
                # ``expected_cash`` on top of that used to double-count it.
                # Similarly, ledger posts realised PnL under the *gross*
                # bucket; comparing it to the position's net value was a
                # unit mismatch. Reconcile gross-to-gross and net-to-net here.
                recon = reconcile(
                    acct.account_id,
                    postings,
                    expected_cash=Decimal(0),
                    expected_realized_pnl=Decimal(0),
                    actual_cash=actual_cash,
                    actual_realized_pnl=realized_gross,
                )
                report["reconciliation"] = {
                    "balanced": recon.balanced,
                    "mismatches": recon.mismatches,
                }
            except Exception:
                report["reconciliation"] = {"balanced": None, "mismatches": ["reconcile unavailable"]}

            report["closed_positions"] = [
                {
                    "id": str(p.position_id),
                    "side": p.side.value,
                    "instrument": p.instrument,
                    "qty": str(p.qty),
                    "entry": str(p.avg_entry_price),
                    "exit": str(p.close_price) if p.close_price else None,
                    "net_pnl": str(p.realized_net_pnl),
                    "fees": str(p.entry_fee + p.exit_fee),
                    "opened_at": p.opened_at.isoformat() if p.opened_at else None,
                    "closed_at": p.closed_at.isoformat() if p.closed_at else None,
                }
                for p in acct_closed
            ]
            all_reports.append(report)

        return {
            "day_id": str(day.day_id),
            "state": day.state.value,
            "start_utc": day.start_utc.isoformat() if day.start_utc else None,
            "end_utc": day.end_utc.isoformat() if day.end_utc else None,
            "strategy_version": day.strategy_version,
            "settings_snapshot": day.settings_snapshot,
            "accounts": all_reports,
        }
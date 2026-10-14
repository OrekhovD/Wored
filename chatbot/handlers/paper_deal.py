"""``/deal`` — ручная сделка и позиции в чате (ТЗ §5 U07, фаза P4).

Экран ничего не решает.  Каждое число deal-ticket приходит из
:func:`paper_trading.ticket.build_ticket` — того же вызова, что делает
WebUI-презентер (Q-21), — и каждое «нет» приходит из доменного
``check_order_risk``.  Собственность этого модуля — только форма разговора:
тикет показывается, живёт 30 секунд под своим идентификатором, а подтверждает
его отдельное нажатие, при котором состояние перечитывается и тикет
пересобирается (цена, маржа и позиции могли уйти).

Три правила, которые видно в коде:

* **слайд не имеет власти.**  ``deal:confirm:<8hex>`` — только указатель:
  заново резолвится Principal, тикет ищется по собственным идентификаторам
  владельца, TTL проверяется, день сверяется, гейт вызывается снова.  Устаревшая
  карточка не может отправить ни старые числа, ни чужой тикет;
* **одно действие — одна реализация** (ТЗ TB-03).  ``/deal preview`` и кнопка
  «пересобрать» зовут один :func:`services.paper_orders.preview`; подтверждение
  из чата и из WebUI проходят один и тот же доменный гейт.  Позиции рендерит
  существующий экран ``/positions``, а не его копия;
* **закрытия из чата нет** (решение куратора по объёму P4): ни одна ветка этого
  модуля не вызывает ``close_position``, и карточка говорит об этом прямо.

``/deal`` никогда не запускает и не останавливает торговый день: день остаётся
в WebUI (Q-10/Q-13), и на отсутствие дня карточка отвечает отказом, а не
позицией в песочнице.
"""
from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from application import presenters as ui
from handlers import telegram_surface as surface
from handlers.now import show_positions
from security import guards
from security import principal as principal_mod
from security.principal import Principal
from services import paper_orders as deals

log = logging.getLogger("chatbot.deal")
router = Router(name="paper_deal")

#: один тап в секунду на автора: повторная отправка защищена идемпотентным
#: ключом тикета, лимитер здесь только против случайного двойного нажатия
_write_limiter = guards.RateLimiter(1.0)


# ─── keyboards ──────────────────────────────────────────────────────────────


def _row(*pairs: tuple[str, str]) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text, callback_data=data) for text, data in pairs]


def deal_keyboard(
    *,
    can_write: bool,
    ticket: Any = None,
    command_id: str | None = None,
) -> InlineKeyboardMarkup:
    """Кнопки экрана. Подтверждение — только владельцу, всё остальное — чтение.

    ``ticket.short_id`` — 8 символов uuid: <code>deal:confirm:</code> плюс токен
    занимает 21 байт, лимит Telegram на callback_data (64 байта) не подбирается.
    Полный ``command_id`` — 48 байт, тоже в пределах.
    """
    rows: list[list[InlineKeyboardButton]] = []
    if ticket is not None:
        if can_write:
            rows.append(_row(("✅ Подтвердить заявку", f"deal:confirm:{ticket.short_id}")))
        rows.append(_row(("🔁 Пересобрать по свежим числам", f"deal:rebuild:{ticket.short_id}")))
    if command_id:
        rows.append(_row(("🔄 Статус команды", f"deal:status:{command_id}")))
    rows.append(_row(("📦 Позиции", "deal:positions"), ("🏠 Сейчас", "nav:now")))
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ─── reply helpers ──────────────────────────────────────────────────────────


async def _reply(target: Message | CallbackQuery, text: str, keyboard: InlineKeyboardMarkup) -> bool:
    """Send a card, or refresh the one already on screen (Q-16 no-op edit)."""
    try:
        if isinstance(target, Message):
            await target.answer(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await target.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as exc:
        if not surface.is_no_op_edit(exc):
            raise
        log.info("deal card already up to date (%s)", getattr(target, "id", "message"))
        return False
    return True


async def _refused(target: Message | CallbackQuery, exc: deals.PaperOrderError) -> None:
    """Отказ — прежняя карточка отказа консоли, с кодом и нарушениями домена."""
    card = ui.build_plan_refused_card(
        exc.reason_code, exc.message,
        violations=getattr(exc, "violations", None),
        entity="Сделка",
        source="paper_orders",
        next_actions=ui.DEAL_ACTIONS,
    )
    await _reply(
        target,
        f"{card.render()}\n<i>req <code>{card.request_id}</code></i>",
        deal_keyboard(can_write=False),
    )


def _tz(principal: Principal) -> str:
    return principal_mod.default_timezone()


def _denied_write(principal: Principal, what: str) -> deals.PaperOrderError:
    """Не-владелец не доходит до use case записи (ТЗ §3 роли)."""
    return deals.PaperOrderError(
        f"{what} может только владелец аккаунта", reason_code="role_denied",
    )


# ─── use cases: command and button both call these ─────────────────────────


async def show_form(target: Message | CallbackQuery, principal: Principal, *, note: str = "") -> None:
    """Форма команды и список того, чем отвечает ``/deal``."""
    keys = ", ".join(sorted(deals.ARG_KEYS))
    required = ", ".join(deals.REQUIRED_KEYS)
    instrument = deals.tradable_instrument()
    body = [
        deals.usage_preview(instrument=instrument),
        f"разрешённые ключи: {keys} · обязательные: {required}",
        "значения разбирает домен: тип заявки market|limit, сторона buy|sell, "
        "числа в USDT; объём считается по риску и стопу с округлением вниз до шага",
        f"инструмент: {instrument} (настроен для текущего дня; другой чат не обещает — "
        "ручная команда дня исполляется на нём)",
        "шаги: предпросмотр → подтверждение кнопкой → статус команды по command_id",
    ]
    if note:
        body.insert(0, note)
    text = (
        "🎯 <b>Ручная сделка</b>\n"
        + "\n".join(f"· {line}" for line in body)
        + "\n<i>числа считает доменный тикет-гейт, общее с WebUI; торговый день "
          "запускается и закрывается только в WebUI, позицию из чата не закрыть.</i>"
    )
    await _reply(target, text, deal_keyboard(can_write=principal.may_mutate))


async def show_ticket(target: Message | CallbackQuery, principal: Principal, raw: str) -> None:
    """Предпросмотр: один вызов доменного тикета, ничего не отправлено."""
    try:
        ticket = await deals.preview(principal.owner_id, raw)
    except deals.PaperOrderError as exc:
        await _refused(target, exc)
        return
    card = ui.build_deal_ticket_card(ticket, tz_name=_tz(principal))
    await _reply(
        target,
        f"{card.render()}\n<i>req <code>{card.request_id}</code> · "
        f"{ui.quality_badge(card.quality)} · тикет <code>{ticket.short_id}</code> · "
        "подтверждение — кнопкой; до неё команда не записана</i>",
        deal_keyboard(can_write=principal.may_mutate, ticket=ticket),
    )


async def rebuild_ticket(target: Message | CallbackQuery, principal: Principal, token: str) -> None:
    """Та же заявка, свежие входы: перечень аргументов берётся из тикета."""
    try:
        record = await deals.recall(principal.owner_id, token)
    except deals.PaperOrderError as exc:
        await _refused(target, exc)
        return
    if not record.raw:
        await _refused(
            target,
            deals.TicketUnknown("аргументы тикета не сохранены — введите команду заново"),
        )
        return
    await show_ticket(target, principal, record.raw)


async def confirm_deal(target: Message | CallbackQuery, principal: Principal, token: str) -> None:
    """Единственная точка, где чат записывает торговую команду."""
    try:
        receipt = await deals.confirm(principal.owner_id, token)
    except deals.PaperOrderError as exc:
        await _refused(target, exc)
        return
    text = (
        "✅ <b>Заявка передана домену</b>\n"
        f"команда <code>{ui.esc(receipt['command_id'])}</code> · "
        f"объём {ui.plain(receipt['quantity'])}\n"
        f"тикету <code>{ui.esc(str(receipt['ticket_id'])[:8])}</code> соответствует "
        f"день <code>{ui.esc(str(receipt['day_id'])[:8])}</code>\n"
        "<i>Ключ идемпотентности детерминирован, поэтому повторное нажатие на эту же "
        "карточку не создаёт вторую заявку. Исполнение подтверждает runner: статус — "
        "кнопкой или <code>/deal status &lt;command_id&gt;</code>. Закрыть позицию из "
        "чата нельзя (объём P4).</i>"
    )
    await _reply(
        target, text,
        deal_keyboard(can_write=True, command_id=str(receipt["command_id"])),
    )


async def show_status(target: Message | CallbackQuery, principal: Principal, token: str) -> None:
    """Статус команды; не дождались терминала — «не подтверждено», не «ошибка»."""
    try:
        read = await deals.command_status(principal.owner_id, token)
    except deals.PaperOrderError as exc:
        await _refused(target, exc)
        return
    lines = [
        f"команда <code>{ui.esc(read.command_id)}</code> · статус {ui.esc(read.status)} "
        f"({ui.command_status_word(read.status)})",
    ]
    if read.error:
        lines.append(f"ошибка домена: <code>{ui.esc(read.error)}</code>")
    position_id = read.position_id
    if position_id:
        lines.append(f"позиция: <code>{ui.esc(position_id)}</code>")
    if not read.settled:
        lines.append(
            "<i>терминального состояния не дождались: заявка не подтверждена и не "
            "отменена — проверьте этот же command_id позже, не повторяя заявку</i>"
        )
    else:
        lines.append("<i>дальше — <code>/deal positions</code> или <code>/positions</code></i>")
    await _reply(
        target,
        "📄 <b>Статус команды</b>\n" + "\n".join(lines),
        deal_keyboard(can_write=principal.may_mutate, command_id=read.command_id),
    )


async def show_deal_positions(target: Message | CallbackQuery, principal: Principal) -> None:
    """Позиции — существующий экран консоли, вторая копия не вводится (TB-03)."""
    await show_positions(target, principal)


# ─── command surface ────────────────────────────────────────────────────────


@router.message(Command("deal"))
async def cmd_deal(message: Message, principal: Principal, command: CommandObject) -> None:
    args = (command.args or "").strip()
    if not args:
        await show_form(message, principal)
        return
    parts = args.split(None, 1)
    sub = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if sub in {"help", "?"}:
        await show_form(message, principal)
        return
    if sub in {"preview", "new", "ticket"}:
        if not rest:
            await show_form(message, principal, note="нужны аргументы заявки в форме key=value")
            return
        await show_ticket(message, principal, rest)
        return
    if sub == "confirm":
        if not principal.may_mutate:
            await _refused(message, _denied_write(principal, "подтверждать заявку"))
            return
        if not rest:
            await show_form(message, principal, note="подтверждение — по идентификатору тикета")
            return
        await confirm_deal(message, principal, rest)
        return
    if sub == "status":
        if not rest:
            await message.answer(
                "Формат: <code>/deal status &lt;command_id&gt;</code> — идентификатор "
                "из карточки подтверждения",
                parse_mode="HTML",
            )
            return
        await show_status(message, principal, rest)
        return
    if sub == "positions":
        await show_deal_positions(message, principal)
        return
    await show_form(
        message, principal,
        note=f"подкоманда <code>/deal {ui.esc(sub)}</code> не понята",
    )


# ─── callback surface ───────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("deal:confirm:"))
async def cb_deal_confirm(call: CallbackQuery, principal: Principal) -> None:
    """Запись торговой команды: роль, лимитер, затем повторная доменная проверка."""
    if not principal.may_mutate:
        await call.answer("🔒 Подтверждать может только владелец", show_alert=False)
        await _refused(call, _denied_write(principal, "подтверждать заявку"))
        return
    parts = (call.data or "").split(":")
    if len(parts) < 3 or not parts[2]:
        await call.answer("🤔 Карточка устарела", show_alert=False)
        return
    if not _write_limiter.allow(principal.telegram_user_id, f"deal:confirm:{parts[2]}"):
        await call.answer("⏳ Эта заявка уже обрабатывается", show_alert=False)
        return
    await call.answer()
    await confirm_deal(call, principal, parts[2])


@router.callback_query(F.data.startswith("deal:rebuild:"))
async def cb_deal_rebuild(call: CallbackQuery, principal: Principal) -> None:
    await call.answer()
    parts = (call.data or "").split(":")
    if len(parts) < 3 or not parts[2]:
        await call.answer("🤔 Карточка устарела", show_alert=False)
        return
    await rebuild_ticket(call, principal, parts[2])


@router.callback_query(F.data.startswith("deal:status:"))
async def cb_deal_status(call: CallbackQuery, principal: Principal) -> None:
    await call.answer()
    parts = (call.data or "").split(":", 1)
    if len(parts) < 2 or not parts[1]:
        await call.answer("🤔 Карточка устарела", show_alert=False)
        return
    await show_status(call, principal, parts[1])


@router.callback_query(F.data == "deal:positions")
async def cb_deal_positions(call: CallbackQuery, principal: Principal) -> None:
    await call.answer()
    await show_deal_positions(call, principal)

"""``/day`` — управление торговым днём из чата (ТЗ §6.1, срез P4b / Q-24).

Экран — единственный адресат для четырёх действий, которые раньше жили на
легаси-кнопках ``пауза / ужесточить / снизить / закрыть всё``: те писали
``trading_sessions``, которой у живого дня нет, и отвечали ``нет сессии``
оператору с идущим ``paper_v2_days``.  Здесь каждое действие становится
доменной командой через :mod:`paper_trading.adapter` — тем же вызовом, которым
пользуется WebUI (ТЗ TB-03), и тем же исполнением в runner'е.

Что видно в коде:

* **подтверждение обязано.**  Ни одно mutating-действие не исполняется по одному
  тапу: сначала карточка (:func:`services.day_control.preview`), потом
  ``daycmd:confirm:<8hex>``, при котором состояние перечитывается, а для лимитов
  сверяется показанное (``expected`` уходит в доменный compare-and-swap);
* **слайд не имеет власти.**  Кнопка — только указатель: Principal резолвится
  заново, тикет ищется среди карточек этого владельца и только вида ``day``,
  TTL проверяется, день и лимиты сверяются.  Оттого же идентификатор
  ``/deal confirm`` не может подтвердить паузу: магазин один, виды разные;
* **ключ принадлежит карточке.**  ``tg-<owner>-<ticket>`` — повторный тап по
  этой же карточке не создаёт вторую команду, а вторая попытка того же действия
  позже — это новая карточка и новая команда (в этом и состоял Q-24:
  ``pause-<day>`` превращал её в молчаливый replay);
* **исполняет runner.**  Экран сообщает ``command_id`` и как узнать статус, но не
  выдаёт ``completed`` за собственный вызов.

``/day`` не открывает и не закрывает счёт, не выставляет заявки и не закрывает
позицию по идентификатору — это ``/deal`` и WebUI (решение куратора: закрытие
конкретной позиции из чата остаётся за срезом P4c).
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
from services import day_control as control
from services import paper_orders as deals

log = logging.getLogger("chatbot.day")
router = Router(name="day_control")

#: один тап в секунду на автора: повтор защищён ключом карточки, лимитер здесь
#: только против случайного двойного нажатия
_write_limiter = guards.RateLimiter(1.0)

#: чем подписать кнопку подтверждения по действию
_CONFIRM_LABEL = {
    control.ACTION_PAUSE: "⏸ Подтвердить паузу",
    control.ACTION_RESUME: "▶️ Подтвердить продолжение",
    control.ACTION_FINISH: "🏁 Подтвердить завершение дня",
    control.ACTION_CLOSE_ALL: "🛑 Подтвердить стоп авто",
    control.ACTION_LIMITS: "🛡 Подтвердить новые лимиты",
}


# ─── keyboards ──────────────────────────────────────────────────────────────


def _row(*pairs: tuple[str, str]) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=text, callback_data=data) for text, data in pairs]


def day_keyboard(
    *,
    can_write: bool,
    ticket: Any = None,
    command_id: str | None = None,
) -> InlineKeyboardMarkup:
    """Кнопки экрана. Подтверждение — только владельцу, остальное — чтение."""
    rows: list[list[InlineKeyboardButton]] = []
    if ticket is not None and can_write:
        rows.append(_row((
            _CONFIRM_LABEL.get(ticket.action, "✅ Подтвердить"),
            f"daycmd:confirm:{ticket.short_id}",
        )))
    if command_id:
        rows.append(_row(("🔄 Статус команды", f"daycmd:status:{command_id}")))
    rows.append(_row(("📊 День", "daycmd:status"), ("📦 Позиции", "daycmd:positions")))
    rows.append(_row(("🏠 Сейчас", "nav:now")))
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
        log.info("day card already up to date (%s)", getattr(target, "id", "message"))
        return False
    return True


async def _refused(target: Message | CallbackQuery, exc: control.DayControlError) -> None:
    """Отказ — тот же шаблон консоли, что у сделки и планов."""
    card = ui.build_plan_refused_card(
        exc.reason_code, exc.message,
        violations=getattr(exc, "violations", None),
        entity="День",
        source="day_control",
        next_actions=ui.DAY_CONTROL_ACTIONS,
    )
    await _reply(
        target,
        f"{card.render()}\n<i>req <code>{card.request_id}</code></i>",
        day_keyboard(can_write=False),
    )


def _tz(principal: Principal) -> str:
    return principal_mod.default_timezone()


def _denied_write(what: str) -> control.DayControlError:
    """Не-владелец не доходит до use case записи (ТЗ §3 роли)."""
    return control.DayControlInvalid(
        f"{what} может только владелец аккаунта", reason_code="role_denied",
    )


# ─── use cases: command and button both call these ─────────────────────────


async def show_form(target: Message | CallbackQuery, principal: Principal, *, note: str = "") -> None:
    """Форма команды и честный список того, чем отвечает ``/day``."""
    body = [ui.esc(control.usage_preview())]
    for action in (
        control.ACTION_PAUSE, control.ACTION_RESUME, control.ACTION_FINISH,
        control.ACTION_CLOSE_ALL, control.ACTION_LIMITS,
    ):
        spec = control.ACTIONS[action]
        body.append(f"{spec.title} — {spec.effect}")
    body.append(
        "шаги: карточка → подтверждение кнопкой → статус команды по command_id; "
        "исполняет команду runner, экран лишь передаёт намерение"
    )
    if note:
        body.insert(0, note)
    text = (
        "📅 <b>Управление днём</b>\n"
        + "\n".join(f"· {line}" for line in body)
        + "\n<i>легаси-кнопки «пауза/ужесточить/снизить/закрыть всё» больше ничего "
          "не пишут: одно действие — одна доменная команда. Позицию по "
          "идентификатору закрывает WebUI, заявку выставляет /deal.</i>"
    )
    await _reply(target, text, day_keyboard(can_write=principal.may_mutate))


async def show_status(target: Message | CallbackQuery, principal: Principal) -> None:
    """Состояние дня и действующие лимиты — только чтение домена."""
    try:
        summary = await control.status(principal.owner_id)
    except control.DayControlError as exc:
        await _refused(target, exc)
        return
    lines = [
        f"день <code>{ui.esc(summary['day_id'][:8])}</code> · состояние "
        f"{ui.esc(summary['day_state'])}",
        "лимиты дня (settings_snapshot):",
    ]
    for key in sorted(summary["limits"]):
        lines.append(f"· {ui.esc(key)} = {ui.plain(summary['limits'][key])}")
    notes = [str(item) for item in summary["settings_notes"]]
    if notes:
        lines.append(
            "⚠️ нет в снапшоте дня, действует дефолт RiskSettings: "
            + ui.esc(", ".join(notes))
        )
    lines.append(
        "<i>посреди дня лимиты меняются этим экраном (/day limits) и только через "
        "подтверждение; условия нового дня — WebUI</i>"
    )
    await _reply(
        target,
        "📊 <b>День</b>\n" + "\n".join(lines),
        day_keyboard(can_write=principal.may_mutate),
    )


async def show_ticket(target: Message | CallbackQuery, principal: Principal, word: str, raw: str) -> None:
    """Карточка действия; до подтверждения не записано ничего."""
    try:
        ticket = await control.preview(principal.owner_id, word, raw)
    except control.DayControlError as exc:
        await _refused(target, exc)
        return
    spec = control.ACTIONS[ticket.action]
    changed = tuple(
        (key, ticket.expected.get(key), ticket.limits[key])
        for key in sorted(ticket.limits)
    )
    card = ui.build_day_control_card(
        ticket,
        title=spec.title,
        effect=spec.effect,
        tz_name=_tz(principal),
        changed=changed,
        open_positions=ticket.open_auto_positions,
    )
    await _reply(
        target,
        f"{card.render()}\n<i>req <code>{card.request_id}</code> · тикет "
        f"<code>{ui.esc(ticket.short_id)}</code> · "
        + ("подтверждение — кнопкой; до неё команда не записана</i>"
           if principal.may_mutate
           else "подтвердить может только владелец; команда не записана</i>"),
        day_keyboard(can_write=principal.may_mutate, ticket=ticket),
    )


async def confirm_action(target: Message | CallbackQuery, principal: Principal, token: str) -> None:
    """Единственная точка, где чат записывает команду управления днём."""
    try:
        receipt = await control.confirm(principal.owner_id, token)
    except control.DayControlError as exc:
        await _refused(target, exc)
        return
    spec = control.ACTIONS[receipt["action"]]
    lines = [
        f"{spec.title} · команда <code>{ui.esc(receipt['command_id'])}</code>",
        f"день <code>{ui.esc(str(receipt['day_id'])[:8])}</code> · тикету "
        f"<code>{ui.esc(str(receipt['ticket_id'])[:8])}</code> соответствует",
    ]
    if receipt["limits"]:
        lines.append("записываемые лимиты: " + ", ".join(
            f"{ui.esc(key)}={ui.plain(value)}" for key, value in sorted(receipt["limits"].items())
        ))
    lines.append(
        "<i>Ключ идемпотентности принадлежит карточке, поэтому повторное нажатие "
        "на неё не создаёт вторую команду. Исполняет команду runner: статус — "
        "кнопкой или <code>/day status &lt;command_id&gt;</code>.</i>"
    )
    await _reply(
        target,
        "✅ <b>Команда передана домену</b>\n" + "\n".join(lines),
        day_keyboard(can_write=True, command_id=str(receipt["command_id"])),
    )


async def show_command_status(target: Message | CallbackQuery, principal: Principal, token: str) -> None:
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
    if not read.settled:
        lines.append(
            "<i>терминального состояния не дождались: команда не подтверждена и не "
            "отменена — проверьте этот же command_id позже, не повторяя действие</i>"
        )
    else:
        lines.append("<i>дальше — <code>/day status</code> по действующим лимитам</i>")
    await _reply(
        target,
        "📄 <b>Статус команды</b>\n" + "\n".join(lines),
        day_keyboard(can_write=principal.may_mutate, command_id=read.command_id),
    )


async def show_positions_card(target: Message | CallbackQuery, principal: Principal) -> None:
    """Позиции — существующий экран консоли, вторая копия не вводится (TB-03)."""
    await show_positions(target, principal)


# ─── command surface ────────────────────────────────────────────────────────


@router.message(Command("day"))
async def cmd_day(message: Message, principal: Principal, command: CommandObject) -> None:
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
    if sub == "status":
        if rest:
            await show_command_status(message, principal, rest)
        else:
            await show_status(message, principal)
        return
    if sub == "confirm":
        if not principal.may_mutate:
            await _refused(message, _denied_write("подтверждать команду дня"))
            return
        if not rest:
            await show_form(message, principal, note="подтверждение — по идентификатору карточки")
            return
        await confirm_action(message, principal, rest)
        return
    if sub in {"limits", "tighten", "reduce"}:
        await show_ticket(message, principal, sub, rest)
        return
    if sub in control.ALIASES:
        if control.ALIASES[sub] == control.ACTION_LIMITS:
            await show_ticket(message, principal, sub, rest)
        else:
            await show_ticket(message, principal, sub, "")
        return
    await show_form(message, principal, note=f"подкоманда <code>/day {ui.esc(sub)}</code> не понята")


# ─── callback surface ───────────────────────────────────────────────────────


@router.callback_query(F.data.startswith("daycmd:confirm:"))
async def cb_day_confirm(call: CallbackQuery, principal: Principal) -> None:
    """Запись команды управления: роль, лимитер, затем повторная доменная сверка."""
    if not principal.may_mutate:
        await call.answer("🔒 Подтверждать может только владелец", show_alert=False)
        await _refused(call, _denied_write("подтверждать команду дня"))
        return
    parts = (call.data or "").split(":")
    if len(parts) < 3 or not parts[2]:
        await call.answer("🤔 Карточка устарела", show_alert=False)
        return
    if not _write_limiter.allow(principal.telegram_user_id, f"daycmd:confirm:{parts[2]}"):
        await call.answer("⏳ Эта команда уже обрабатывается", show_alert=False)
        return
    await call.answer()
    await confirm_action(call, principal, parts[2])


@router.callback_query(F.data == "daycmd:status")
async def cb_day_status(call: CallbackQuery, principal: Principal) -> None:
    await call.answer()
    await show_status(call, principal)


@router.callback_query(F.data.startswith("daycmd:status:"))
async def cb_day_command_status(call: CallbackQuery, principal: Principal) -> None:
    await call.answer()
    parts = (call.data or "").split(":", 1)
    if len(parts) < 2 or not parts[1]:
        await call.answer("🤔 Карточка устарела", show_alert=False)
        return
    await show_command_status(call, principal, parts[1])


@router.callback_query(F.data == "daycmd:positions")
async def cb_day_positions(call: CallbackQuery, principal: Principal) -> None:
    await call.answer()
    await show_positions_card(call, principal)

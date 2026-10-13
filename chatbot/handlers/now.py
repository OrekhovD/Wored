"""Read-only console screens: «Сейчас» and the first-level navigation (ТЗ §2, §4 U01–U03, U06–U08).

One use case per action: a slash command and its navigation button call the same
function, so there is never a second implementation of «show the market».  Every
number on these screens is read from the shared source through
:mod:`integrations.wored_api`; nothing here recalculates a financial value.

Callbacks are navigation only (``nav:<screen>`` / ``now:refresh``): they carry no
resource id and no authority, the handler re-resolves the Principal and re-reads
the source.  A stale card therefore cannot execute anything — it can only render
fresh data.  Opaque action ids with version/nonce/expiry belong to mutating
actions and arrive with P3/P4 (``bot_callbacks``).
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from application import presenters as ui
from handlers import telegram_surface as surface
from integrations import wored_api as api
from security import guards
from security import principal as principal_mod
from security.principal import Principal

log = logging.getLogger("chatbot.console")
router = Router(name="console")

MODE_LABEL = "LIVE PAPER"

#: first-level navigation (ТЗ §2): Сейчас / Прогноз / Сессия / Позиции / Итоги / Ещё
_NAV_ROWS = (
    (("Сейчас", "nav:now"), ("Прогноз", "nav:forecast"), ("Сессия", "nav:session")),
    (("Позиции", "nav:positions"), ("Итоги", "nav:report"), ("Ещё", "nav:more")),
)


def nav_keyboard(*, include_refresh: bool = True, with_plans: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
        for row in _NAV_ROWS
    ]
    if with_plans:
        # the plan screens are a different router (``handlers.simulation_v3``);
        # the callback carries no authority, only the intent to list
        rows.append([InlineKeyboardButton(text="📋 Планы", callback_data="plan:list")])
    if include_refresh:
        rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="now:refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _miniapp_button() -> InlineKeyboardButton | None:
    """Mini App deep link, offered only on a public HTTPS origin.

    ``localhost`` on a phone is not the WORED machine (ТЗ §3), so the button is
    omitted rather than shipped broken — every read stays available in the chat.
    The button is a real ``web_app`` launch, not just a browser link: ТЗ §3 says a
    reply-keyboard web_app button alone is not enough, so it lives next to the
    inline navigation of every card.
    """
    from aiogram.types import WebAppInfo

    from integrations.webui_client import get_webui_public_base_url

    base = get_webui_public_base_url()
    if not base.startswith("https://"):
        return None
    return InlineKeyboardButton(
        text="📊 График (Mini App)", web_app=WebAppInfo(url=f"{base}/command-deck")
    )


async def _read_all(owner_id: str) -> tuple[Any, Any, Any]:
    """Market + forecast + session, each independently degrading to N/A."""
    market = await api.read_market_snapshot()
    forecast = await api.read_latest_forecast()
    session = await api.read_session_state(owner_id)
    return market, forecast, session


# ─── Screens ───────────────────────────────────────────────────────────


async def show_now(target: Message | CallbackQuery, principal: Principal) -> None:
    """The single main card — one screen for market, forecast, session, risk."""
    market, forecast, session = await _read_all(principal.owner_id)
    card = ui.build_now_card(
        market, forecast, session,
        tz_name=principal_mod.default_timezone(),
        mode_label=MODE_LABEL,
    )
    text = (
        f"{card.render()}\n\n"
        f"<i>req <code>{card.request_id}</code> · {ui.quality_badge(card.quality)} "
        f"{ui.plain(market.source)}</i>"
    )
    keyboard = nav_keyboard()
    # every console card leaves the process through :func:`_reply`, so the no-op
    # re-render rule has exactly one implementation here too (TB-03, Q-16)
    await _reply(target, text, keyboard)


async def show_market_detail(target: Message | CallbackQuery, principal: Principal) -> None:
    market = await api.read_market_snapshot()
    card = ui.build_market_detail_card(market, tz_name=principal_mod.default_timezone())
    rows = nav_keyboard(include_refresh=False).inline_keyboard
    button = _miniapp_button()
    extra = [[button]] if button else [[InlineKeyboardButton(text="⬅️ Сейчас", callback_data="nav:now")]]
    # ``inline_keyboard`` is a list of ROWS, so the extra button is wrapped in a
    # row itself; passing bare buttons here makes aiogram reject the whole markup.
    await _reply(target, card.render(), InlineKeyboardMarkup(inline_keyboard=rows + extra))


async def show_forecast(target: Message | CallbackQuery, principal: Principal) -> None:
    market = await api.read_market_snapshot()
    forecast = await api.read_latest_forecast()
    card = ui.build_forecast_card(forecast, market, tz_name=principal_mod.default_timezone())
    await _reply(target, card.render(), nav_keyboard(include_refresh=False))


async def show_session(target: Message | CallbackQuery, principal: Principal) -> None:
    session = await api.read_session_state(principal.owner_id)
    tz = principal_mod.default_timezone()

    if not session.has_session:
        body = (
            "⚪ <b>Сессия не запущена</b>\n"
            f"{ui.reason_text(session.reason_codes)}\n"
            "Планы — /plans: здесь черновик создаётся, правится и утверждается. "
            "Запуск сессии остаётся в WebUI (решение Q-10); действий запуска в боте нет."
        )
        await _reply(
            target, f"🎯 Сессия\n{body}",
            nav_keyboard(include_refresh=False, with_plans=True),
        )
        return

    lines = [
        f"ID: <code>{ui.esc((session.day_id or '')[:8])}</code> · состояние: "
        f"<b>{ui.esc(session.state.upper())}</b>",
        f"начало: {ui.format_time(session.started_at, tz)[0]} {ui.format_time(session.started_at, tz)[1]}",
        f"окончание: {ui.format_time(session.ends_at, tz)[0]} {ui.format_time(session.ends_at, tz)[1]}",
    ]
    for account in session.accounts:
        lines.append(
            f"счет {ui.esc(account.kind)}: <code>{ui.esc(account.account_id[:8])}</code> · "
            f"кэш {ui.money(account.cash, account.currency)} · "
            f"открытых позиций {account.open_positions}"
        )
    lines.append(
        "действия (пауза/продолжить/ужесточить/завершить) появятся в P4; "
        "бот не выставляет статус по факту своего вызова"
    )
    await _reply(
        target, "🎯 <b>Сессия</b>\n" + "\n".join(lines),
        nav_keyboard(include_refresh=False, with_plans=True),
    )


async def show_positions(target: Message | CallbackQuery, principal: Principal) -> None:
    session = await api.read_session_state(principal.owner_id)
    if not session.accounts:
        body = "⚪ Счетов нет — запущенной paper-сессии для этого владельца не найдено."
        await _reply(target, f"📦 <b>Позиции</b>\n{body}", nav_keyboard(include_refresh=False))
        return

    lines: list[str] = []
    total = 0
    for account in session.accounts:
        lines.append(f"— {ui.esc(account.kind)} (<code>{ui.esc(account.account_id[:8])}</code>)")
        if not account.positions:
            lines.append("  нет открытых позиций")
            continue
        for p in account.positions:
            total += 1
            lines.append(
                "  <code>{pid}</code> {side} qty {qty} @ {entry} · SL {sl} · TP {tp}".format(
                    pid=ui.esc(p.position_id[:8]),
                    side=ui.esc(p.side.upper()),
                    qty=ui.plain(p.qty),
                    entry=ui.money(p.avg_entry, p.unit, dp=1),
                    sl=ui.money(p.stop, p.unit, dp=1),
                    tp=ui.money(p.target, p.unit, dp=1),
                )
            )
    lines.append("")
    lines.append(
        f"итого открытых: {total} · P&L и ликвидационный порог не пересчитываются ботом — "
        "они в отчёте домена (P5)"
    )
    await _reply(target, "📦 <b>Позиции</b>\n" + "\n".join(lines), nav_keyboard(include_refresh=False))


async def show_reports(target: Message | CallbackQuery, principal: Principal) -> None:
    tz = principal_mod.default_timezone()
    lines: list[str] = []
    quality = "live"
    try:
        from paper_trading.adapter import list_day_reports

        reports = await list_day_reports(principal.owner_id, limit=5)
    except Exception as exc:  # domain read failure is an error, not an empty list
        log.warning("report read failed: %s", exc)
        reports = []
        quality = "error"
        lines.append("🔴 Отчёты недоступны: сервис данных не ответил (BACKEND_UNAVAILABLE).")

    if quality != "error":
        if not reports:
            lines.append("⚪ Закрытых сессий ещё нет — итоговый отчёт строится только после closed.")
        for r in reports:
            # the domain ledger already reports net after fees; showing both
            # numbers must not invite subtracting them again (paper_trading/service)
            unit = str(r.get("currency") or api.USDT)
            lines.append(
                "<code>{day}</code> {date} · сделок {trades} (±: {wins}/{losses}) · "
                "net {net} · комиссии {fees}".format(
                    day=ui.esc(str(r.get("day_id", ""))[:8]),
                    date=ui.esc(r.get("date")),
                    trades=r.get("trades", 0),
                    wins=r.get("wins", 0),
                    losses=r.get("losses", 0),
                    net=ui.money(r.get("net_pnl"), unit, dp=4),
                    fees=ui.money(r.get("fees"), unit, dp=4),
                )
            )
        lines.append("")
        lines.append(
            "net уже учитывает комиссии (считает домен); полный отчёт и экспорт — P5 · "
            f"{ui.zone_label(tz)}"
        )

    await _reply(target, "📈 <b>Итоги</b>\n" + "\n".join(lines), nav_keyboard(include_refresh=False))


async def show_more(target: Message | CallbackQuery, principal: Principal) -> None:
    warning = principal_mod.configuration_warning()
    lines = [
        "⚙️ <b>Ещё</b>",
        "Разделы в разработке: Рынок/список, Оповещения, Журнал, Обучение, AI-вопрос, Настройки.",
        (
            "🔴 Mini App: детальный график недоступен — публичный HTTPS origin не настроен "
            "(все чтения остаются в чате)."
            if _miniapp_button() is None
            else "🟢 Mini App: ссылка на Command Deck доступна."
        ),
        f"⏰ Часовой пояс: {ui.esc(principal_mod.default_timezone())}",
        f"📡 Свежесть рынка: {api.market_max_age():.0f} с",
        (f"⚠️ Конфигурация прав: {ui.esc(warning)}" if warning else "🟢 Права: allowlist настроен"),
        "ℹ️ /help — что означают числа; /plans — ваши планы; /cancel — сброс черновика.",
    ]
    await _reply(target, "\n".join(lines), nav_keyboard(include_refresh=False))


# ─── Command surface ───────────────────────────────────────────────────


async def render_screen(screen, target, principal) -> None:  # pragma: no cover - alias
    """Public alias of :func:`_render` for the redirect and button layers."""
    await _render(screen, target, principal)


async def _render(screen, target: Message | CallbackQuery, principal: Principal) -> None:
    """Run one screen; any unexpected failure becomes a normalized error card.

    No traceback, no partial numbers, and a ``request_id`` the user can quote
    (ТЗ §6 error taxonomy).  A «nothing changed» re-render is *not* such a
    failure: :func:`_reply` absorbs it, so an idempotent second tap on the same
    navigation button never claims the data service is down (Q-16).
    """
    try:
        await screen(target, principal)
    except Exception as exc:
        log.error("console screen %s failed: %s", getattr(screen, "__name__", screen), exc)
        card = ui.build_error_card("BACKEND_UNAVAILABLE", detail="экран не сформирован")
        await _reply(
            target,
            f"{card.body}\n<i>req <code>{card.request_id}</code></i>",
            nav_keyboard(include_refresh=False),
        )


@router.message(CommandStart())
async def cmd_start(message: Message, principal: Principal) -> None:
    """U01 — short card plus three concrete next steps, no risky button."""
    from handlers.start import get_main_keyboard

    await _render(show_now, message, principal)
    steps = (
        "Три шага:\n"
        "1️⃣ <b>Рынок</b> — /market (последние достоверные цены и свежесть)\n"
        "2️⃣ <b>Прогноз</b> — /forecast (модель, цель, статус, причина)\n"
        "3️⃣ <b>Сессия</b> — /session (состояние paper-дня и счета)\n\n"
        "<i>Бот только читает и показывает; торговые действия включаются с P4.</i>"
    )
    await message.answer(
        steps, reply_markup=get_main_keyboard(), parse_mode="HTML"
    )


@router.message(Command("now"))
async def cmd_now(message: Message, principal: Principal) -> None:
    await _render(show_now, message, principal)


@router.message(Command("market"))
async def cmd_market(message: Message, principal: Principal) -> None:
    await _render(show_market_detail, message, principal)


@router.message(Command("forecast"))
async def cmd_forecast(message: Message, principal: Principal) -> None:
    await _render(show_forecast, message, principal)


@router.message(Command("session"))
async def cmd_session(message: Message, principal: Principal) -> None:
    await _render(show_session, message, principal)


@router.message(Command("positions"))
async def cmd_positions(message: Message, principal: Principal) -> None:
    await _render(show_positions, message, principal)


@router.message(Command("report"))
async def cmd_report(message: Message, principal: Principal) -> None:
    await _render(show_reports, message, principal)


async def show_help(target: Message | CallbackQuery, principal: Principal) -> None:
    card = ui.build_help_card(
        MODE_LABEL,
        principal_mod.default_timezone(),
        market_max_age=api.market_max_age(),
    )
    await _reply(target, f"{card.render()}\n<i>req <code>{card.request_id}</code></i>",
                 nav_keyboard(include_refresh=False))


@router.message(Command("help"))
async def cmd_help(message: Message, principal: Principal) -> None:
    await _render(show_help, message, principal)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, principal: Principal) -> None:
    """ТЗ U01: /cancel отменяет только черновик/диалог."""
    await message.answer(
        "Незавершённого диалога нет — черновик плана живёт в хранилище планов, а не "
        "в этом чате, поэтому отменять в диалоге нечего.\n"
        "Дальше: /plans покажет ваши черновики; неутверждённый черновик просто остаётся "
        "черновиком и истекает по TTL. Утверждённую версию отмена не откатывает — "
        "она правится только как новая версия.",
        reply_markup=nav_keyboard(include_refresh=False, with_plans=True),
        parse_mode="HTML",
    )


# ─── Callback surface ──────────────────────────────────────────────────

_SCREENS = {
    "nav:now": show_now,
    "nav:forecast": show_forecast,
    "nav:session": show_session,
    "nav:positions": show_positions,
    "nav:report": show_reports,
    "nav:more": show_more,
    "nav:help": show_help,
    "now:refresh": show_now,
}


@router.callback_query(F.data.in_(tuple(_SCREENS)))
async def cb_navigate(call: CallbackQuery, principal: Principal) -> None:
    """ACK first (ТЗ ≤1 s), then re-read and re-render."""
    if call.data == "now:refresh":
        if not guards.refresh_limiter().allow(principal.telegram_user_id, "now"):
            await call.answer("⏳ Обновлять можно не чаще раза в пару секунд", show_alert=False)
            return
    await call.answer()
    await _render(_SCREENS[call.data], call, principal)


# ─── helpers ───────────────────────────────────────────────────────────


async def _reply(target: Message | CallbackQuery, text: str, keyboard: InlineKeyboardMarkup) -> bool:
    """Send or refresh a card; ``False`` means Telegram answered "not modified".

    Re-opening a screen whose content has not changed is Telegram's normal no-op,
    not a service failure, and it is absorbed in exactly one place — here, where
    every console card leaves the process (Q-16).
    """
    try:
        if isinstance(target, Message):
            await target.answer(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await target.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except TelegramBadRequest as exc:
        if not surface.is_no_op_edit(exc):
            raise
        log.info("console card already up to date (%s)", getattr(target, "id", "message"))
        return False
    return True

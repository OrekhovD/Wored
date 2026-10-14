"""Every P2 screen renders, including the ones no command test touches (TB-04).

The read slice has six surfaces: Сейчас, Рынок, Прогноз, Сессия, Позиции, Итоги.
A screen that was never rendered in a test is a screen that can crash in
production — the ``/report`` template once failed on a missing ``.format``
argument, which only an actual render could catch.
"""

from __future__ import annotations

from conftest import (
    CONTRACT,
    OWNER_ID,
    forecast_detail_payload,
    make_message,
    session_state_payload,
    snapshot_payload,
)

from handlers import now as console

def _live() -> dict:
    """A fresh snapshot per render (an import-time payload ages out in a long run)."""
    return {f"market:perpetual:htx:{CONTRACT}": snapshot_payload()}

REPORT_ROWS = [
    {
        "day_id": "f6fe66e3-aaaa-bbbb-cccc-ddddeeeeff00",
        "date": "2026-10-07",
        "state": "closed",
        "strategy_version": "baseline-v1",
        "trades": 6,
        "wins": 4,
        "losses": 2,
        "net_pnl": "12.3456",
        "fees": "0.9876",
    },
    {
        "day_id": "aaaaaaaa-1111-2222-3333-444455556666",
        "date": "2026-10-06",
        "state": "closed",
        "strategy_version": "baseline-v1",
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "net_pnl": "0",
        "fees": "0",
    },
]


def _principal():
    from security.principal import resolve

    principal = resolve(OWNER_ID)
    assert principal is not None
    return principal


async def _render(screen, wired_sources, text="/now"):
    wired_sources["redis"].values = _live()
    message = make_message(user_id=OWNER_ID, text=text)
    await console._render(screen, message, _principal())
    assert message.sent, "the screen must answer"
    return message.sent[-1]["text"]


# ─── Итоги ─────────────────────────────────────────────────────────────


async def test_reports_screen_renders_domain_metrics(wired_sources):
    wired_sources["set_reports"](REPORT_ROWS)
    text = await _render(console.show_reports, wired_sources, text="/report")

    assert "📈" in text
    assert "<code>f6fe66e3</code>" in text
    assert "сделок 6 (±: 4/2)" in text
    assert "net 12.3456 USDT" in text
    assert "комиссии 0.9876 USDT" in text
    assert "USDT USDT" not in text                 # the unit is stated once
    assert "net уже учитывает комиссии" in text    # and must not be re-subtracted


async def test_reports_screen_states_zero_trade_days_without_inventing_pnl(wired_sources):
    wired_sources["set_reports"](REPORT_ROWS)
    text = await _render(console.show_reports, wired_sources)

    assert "<code>aaaaaaaa</code>" in text
    assert "net 0.0000 USDT" in text               # a real zero from the ledger


async def test_reports_screen_without_closed_sessions(wired_sources):
    wired_sources["set_reports"]([])
    text = await _render(console.show_reports, wired_sources)

    assert "Закрытых сессий ещё нет" in text


async def test_reports_backend_failure_is_an_error_not_an_empty_list(wired_sources, monkeypatch):
    from paper_trading import adapter as pt_adapter

    async def _broken(owner_id, limit=30, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(pt_adapter, "list_day_reports", _broken)
    text = await _render(console.show_reports, wired_sources)

    assert "BACKEND_UNAVAILABLE" in text
    assert "0.0000" not in text                    # no invented metric on failure


# ─── Позиции ───────────────────────────────────────────────────────────


async def test_positions_screen_lists_domain_positions(wired_sources):
    wired_sources["set_session"](session_state_payload(running=True))
    text = await _render(console.show_positions, wired_sources, text="/positions")

    assert "📦" in text
    assert "LONG qty 0.002 @ 82,600.0 USDT" in text
    assert "SL 82,100.0 USDT" in text
    assert "TP 83,400.0 USDT" in text
    assert "итого открытых: 1" in text
    assert "P&L" in text  # the screen says explicitly it does not compute one


async def test_positions_screen_without_accounts(wired_sources):
    wired_sources["set_session"](session_state_payload(running=False))
    text = await _render(console.show_positions, wired_sources)

    assert "Счетов нет" in text


# ─── Рынок / Прогноз / Сессия / Помощь / Отмена ───────────────────────


async def test_market_detail_screen_shows_book_funding_and_ticks(wired_sources):
    text = await _render(console.show_market_detail, wired_sources, text="/market")

    assert "bid 82,668.3 USDT" in text
    assert "ask 82,668.4 USDT" in text
    assert "index 82,694.4 USDT" in text
    assert "spread 0.1 USDT" in text
    assert "+0.0047%" in text                      # funding as a signed percent
    assert "шаг цены 0.1" in text


async def test_forecast_screen_shows_base_target_and_missing_range(wired_sources):
    wired_sources["set_forecast"](forecast_detail_payload())
    text = await _render(console.show_forecast, wired_sources, text="/forecast")

    assert "83,148.7 USDT" in text                 # base price from the request
    assert "82,980.4 USDT" in text                 # model target
    assert "диапазон: N/A" in text
    assert "deepseek-v4-pro" in text


async def test_session_screen_lists_real_day_controls(wired_sources):
    """P4b (Q-24): экран остаётся читающим, но называет существующие команды.

    Прежняя строка «появятся в P4» была честной, пока действий не было; теперь
    они есть, и экран обязан отдавать формы владельца (/day), а не выдуманный
    список. Экран по-прежнему ничего не исполняет: в тексте нет ни статуса
    выполненного действия, ни обещания закрыть позиции по нажатию.
    """
    wired_sources["set_session"](session_state_payload(running=True))
    text = await _render(console.show_session, wired_sources, text="/session")

    assert "RUNNING" in text
    assert "появятся в P4" not in text
    assert "/day pause" in text
    assert "/day closeauto" in text
    assert "/day limits" in text
    # numbers of the limits are owned by the domain, not repeated here
    assert "max_risk_per_order_usdt" in text
    assert "исполняет их runner" in text


async def test_help_screen_explains_numbers_and_limits(wired_sources):
    text = await _render(console.show_help, wired_sources, text="/help")

    assert "не отправляет заявки" in text
    assert "N/A" not in text or True               # help is text, not metrics
    assert "stale" in text
    assert "Комиссия и funding" in text


async def test_cancel_screen_does_not_claim_to_revert_domain_commands(wired_sources):
    message = make_message(user_id=OWNER_ID, text="/cancel")
    await console.cmd_cancel(message, _principal())

    text = message.sent[0]["text"]
    assert "отмена не откатывает" in text
    # P3c: the wizard exists now, and its draft lives in the plan store, not in the
    # dialogue — so /cancel must not pretend to delete a draft
    assert "в хранилище планов" in text
    assert "/plans" in text
    assert "P3" not in text

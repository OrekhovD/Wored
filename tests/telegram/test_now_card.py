"""TB-02 — the «Сейчас» card through the real read chain (ТЗ §2, §8 P2 gate).

Nothing here mocks the presenter or the read layer: ``handlers.now`` →
``integrations.wored_api`` → ``application.presenters`` all run for real, and only
Redis / Postgres / the paper_trading service are replaced by fixtures shaped like
the live payloads.  The assertions therefore check the contract the ТЗ fixes:
provenance, freshness, timezone, ``N/A`` instead of ``0``, and HTML escaping.
"""

from __future__ import annotations

import pytest
from conftest import (
    CONTRACT,
    OWNER_ID,
    forecast_detail_payload,
    make_message,
    session_state_payload,
    snapshot_payload,
)

from application import presenters as ui
from handlers import now as console
from integrations import wored_api as api


async def _show_now(wired, *, redis_values=None):
    """Render /now against the wired sources and return the outgoing text."""
    if redis_values is not None:
        wired["redis"].values = redis_values
    message = make_message(user_id=OWNER_ID, text="/now")
    await console.show_now(message, _principal())
    assert message.sent, "the console must answer"
    return message.sent[0]["text"]


def _principal():
    from security.principal import resolve

    principal = resolve(OWNER_ID)
    assert principal is not None
    return principal


def _live() -> dict:
    """A fresh live snapshot **per test**.

    Building it at import time made the «live» fixtures age out: inside a long
    suite run the module was imported minutes before the test executed, the
    payload went past the 5 s window and the card honestly turned 🟠 — the test
    then failed on wall-clock arithmetic rather than on behaviour.
    """
    return {f"market:perpetual:htx:{CONTRACT}": snapshot_payload()}


# ─── Market block ──────────────────────────────────────────────────────


async def test_live_market_shows_source_value_age_and_provenance(wired_sources):
    text = await _show_now(wired_sources, redis_values=_live())

    assert "🟢 Рынок:" in text
    assert "82,650.7 USDT" in text          # last, exactly as the feed reports it
    assert "mark 82,656.6 USDT" in text
    assert "htx-public-rest" in text        # source is named, never invented
    assert "req " in text                   # every card carries a request id


async def test_stale_market_keeps_last_confirmed_value_and_names_the_reason(wired_sources):
    values = {f"market:perpetual:htx:{CONTRACT}": snapshot_payload(age_seconds=600.0)}
    text = await _show_now(wired_sources, redis_values=values)

    assert "🟠" in text
    assert "последнее достоверное 82,650.7 USDT" in text
    assert "MARKET_STALE" in text
    assert "10 мин" in text                 # the age is stated, not hidden
    assert "обновлено" not in text           # and it is not sold as fresh


async def test_missing_snapshot_renders_na_and_never_zero(wired_sources):
    text = await _show_now(wired_sources, redis_values={})

    assert "⚪" in text
    assert "MARKET_MISSING" in text
    assert "N/A" in text
    assert "82,650" not in text             # no value from a previous render
    assert "0 USDT" not in text             # absence is never a zero price


async def test_invalid_payload_is_error_quality_but_keeps_the_last_timestamp(wired_sources):
    broken = snapshot_payload()
    broken["ask"] = "1"                     # crossed book → domain validation fails
    broken["source_at"] = "2026-10-08T10:40:38+00:00"
    values = {f"market:perpetual:htx:{CONTRACT}": broken}
    text = await _show_now(wired_sources, redis_values=values)

    assert "🔴" in text
    assert "MARKET_INVALID" in text
    assert ":40:38" in text                 # when the feed was last trustworthy


async def test_market_is_read_exactly_once_per_card(wired_sources):
    await _show_now(wired_sources, redis_values=_live())

    assert wired_sources["redis"].requested == [f"market:perpetual:htx:{CONTRACT}"]


# ─── Forecast block ────────────────────────────────────────────────────


async def test_forecast_shows_model_value_horizon_and_id(wired_sources):
    wired_sources["set_forecast"](forecast_detail_payload())
    text = await _show_now(wired_sources, redis_values=_live())

    assert "Прогноз 4ч:" in text
    assert "-0.20%" in text
    assert "82,980.4 USDT" in text
    assert "<code>191</code>" in text
    assert "completed" in text


async def test_calibrated_range_is_na_because_the_shared_read_model_has_none(wired_sources):
    wired_sources["set_forecast"](forecast_detail_payload())
    text = await _show_now(wired_sources, redis_values=_live())

    assert "диапазон N/A" in text


async def test_forecast_symbol_is_mapped_to_the_shared_ingestion_symbol(wired_sources):
    wired_sources["set_forecast"](forecast_detail_payload())
    await _show_now(wired_sources, redis_values=_live())

    assert wired_sources["calls"]["forecast"] == ["btcusdt"]


async def test_absent_forecast_is_declared_instead_of_fabricated(wired_sources):
    wired_sources["set_forecast"](None)
    text = await _show_now(wired_sources, redis_values=_live())

    assert "нет проверенного прогноза" in text
    assert "FORECAST_NONE" in text


async def test_expired_forecast_window_is_marked_stale(wired_sources):
    from datetime import datetime, timedelta, timezone

    detail = forecast_detail_payload()
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    detail["models"][0]["points"][0]["target_time"] = past
    wired_sources["set_forecast"](detail)

    text = await _show_now(wired_sources, redis_values=_live())

    assert "expired" in text
    assert "FORECAST_STALE" in text


# ─── Session and risk block ────────────────────────────────────────────


async def test_session_block_uses_the_domain_state_verbatim(wired_sources):
    wired_sources["set_session"](session_state_payload(running=True))
    text = await _show_now(wired_sources, redis_values=_live())

    assert "Сессия: RUNNING" in text
    assert "<code>faaf2226</code>" in text
    assert "manual 998.45 USDT" in text     # balance copied from the domain
    assert "Риск: 1 откр. поз." in text


async def test_domain_enum_never_leaks_its_type_name(wired_sources):
    """Regression: ``get_current_state`` returns a ``DayState`` member.

    Found by the in-container live probe, not by the earlier green suite: the
    fixture used a tidy string, production ships an enum, and ``str(enum)`` is
    ``"DayState.running"`` — an internal type name on a user-facing card that the
    WebUI renders as ``running`` (JSON path).  The boundary must normalise.
    """
    wired_sources["set_session"](session_state_payload(running=True))

    text = await _show_now(wired_sources, redis_values=_live())

    assert "DayState" not in text
    assert "Сессия: RUNNING" in text


async def test_no_session_says_so_instead_of_showing_empty_accounts(wired_sources):
    wired_sources["set_session"](session_state_payload(running=False))
    text = await _show_now(wired_sources, redis_values=_live())

    assert "Сессия: не запущена" in text
    assert "SESSION_NONE" in text


async def test_session_owner_id_is_derived_server_side(wired_sources):
    await _show_now(wired_sources, redis_values=_live())

    assert wired_sources["calls"]["session"] == [_principal().owner_id]


async def test_risk_line_never_computes_pnl_or_drawdown(wired_sources):
    wired_sources["set_session"](session_state_payload(running=True))
    text = await _show_now(wired_sources, redis_values=_live())

    assert f"просадка {ui.NA}" in text
    assert f"лимит {ui.NA}" in text
    assert "P&L" not in text                # the bot must not state a P&L at all


# ─── Formatting contract ───────────────────────────────────────────────


async def test_timestamps_are_rendered_with_an_explicit_zone(wired_sources):
    text = await _show_now(wired_sources, redis_values=_live())

    # the label must be arithmetically true, not merely present: the live card
    # once said "UTC+0" while displaying an Asia/Bangkok clock
    assert "UTC+7 (Asia/Bangkok)" in text
    assert "UTC+0" not in text


def test_offset_suffix_is_computed_from_the_offset_not_a_string_slice():
    from datetime import datetime, timedelta, timezone as dt_timezone

    assert ui._offset_suffix(datetime(2026, 10, 12, 7, 26, tzinfo=dt_timezone.utc)) == "UTC"
    assert ui._offset_suffix(
        datetime(2026, 10, 12, 14, 26, tzinfo=dt_timezone(timedelta(hours=7)))
    ) == "UTC+7"
    assert ui._offset_suffix(
        datetime(2026, 10, 12, 13, 0, tzinfo=dt_timezone(timedelta(hours=5, minutes=30)))
    ) == "UTC+5:30"
    assert ui._offset_suffix(
        datetime(2026, 10, 12, 2, 0, tzinfo=dt_timezone(timedelta(hours=-5)))
    ) == "UTC-5"


async def test_foreign_source_text_is_escaped_before_telegram(wired_sources):
    hostile = snapshot_payload()
    hostile["source"] = "<script>alert(1)</script>"
    values = {f"market:perpetual:htx:{CONTRACT}": hostile}
    text = await _show_now(wired_sources, redis_values=values)

    assert "&lt;script&gt;" in text
    assert "<script>" not in text


@pytest.mark.parametrize(
    "code",
    ["AUTH_DENIED", "MARKET_STALE", "BACKEND_UNAVAILABLE", "CONTEXT_EXPIRED", "NOT_IMPLEMENTED"],
)
def test_error_taxonomy_renders_the_code_not_internals(code):
    card = ui.build_error_card(code, detail="проверь collector")
    assert code in card.body
    assert card.quality == "error"
    assert card.request_id
    assert "Traceback" not in card.body


def test_unknown_error_code_is_still_labeled():
    card = ui.build_error_card("SOMETHING_UNEXPECTED")
    assert "SOMETHING_UNEXPECTED" in card.body


def test_money_and_pct_render_na_for_absence():
    assert ui.money(None) == ui.NA
    assert ui.money("not-a-number") == ui.NA
    assert ui.pct(None) == ui.NA
    assert ui.plain(None) == ui.NA
    assert ui.age_text(None) == ui.NA


def test_snapshot_dto_never_turns_absence_into_zero():
    missing = api.MarketSnapshotV1(
        schema_version=1, instrument=CONTRACT, venue="htx", market_type="linear-swap",
        last=None, bid=None, ask=None, mark=None, index=None, funding_rate=None,
        next_funding_at=None, price_tick=None, quantity_step=None, source="redis",
        source_quality="", quality="missing", as_of=None, age_seconds=None,
        is_fresh=False, unit="USDT", reason_codes=("MARKET_MISSING",),
    )
    assert missing.last is None
    assert missing.spread is None
    assert ui.money(missing.last) == ui.NA

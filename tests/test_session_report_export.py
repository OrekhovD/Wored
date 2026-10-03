"""P6.3 — report exports (JSON / CSV / HTML) rendered from the frozen snapshot.

Pure host tests, no database. They lock the three promises that make an export
trustworthy next to the screen (ТЗ §6 / MC-18 round-trip, MC-20 exports):

  * **parity** — the JSON/CSV/HTML carry the *same* canonical numbers and ids the
    report model produced (parsed back and compared field-for-field);
  * **honest nulls** — a metric the model marks ``None`` (insufficient sample,
    division by zero) becomes an empty cell, never a fabricated ``0``;
  * **no pooling** — ``manual`` and ``auto`` stay separate rows, there is no
    combined owner total;
  * **determinism + integrity** — the same report serialises to identical bytes,
    and every format embeds/agrees with the ``canonical_hash`` stored in P6.2.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from paper_trading.report_export import (
    ACCOUNT_COLUMNS,
    accounts_rows,
    export_bundle,
    to_csv,
    to_html,
    to_json,
)
from paper_trading.session_report import (
    Trade,
    build_account_report,
    build_session_report,
    canonical_hash,
)

_BASE = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _mk(nets: list[str]) -> list[Trade]:
    trades: list[Trade] = []
    for i, net in enumerate(nets):
        opened = _BASE + timedelta(hours=i)
        trades.append(
            Trade(
                position_id=f"pos-{i}",
                realized_net=Decimal(net),
                realized_gross=Decimal(net),
                entry_fee=Decimal("1"),
                exit_fee=Decimal("1"),
                funding_cashflow=Decimal("0"),
                is_liquidated=False,
                opened_at=opened,
                closed_at=opened + timedelta(minutes=30),
                notional=Decimal("10000"),
            )
        )
    return trades


def _report(*, manual_nets=None, auto_nets=None, manual_budget="1000",
            auto_budget="1000", sid="sess-1"):
    accounts = {}
    if manual_nets is not None:
        accounts["manual"] = build_account_report(
            "manual", _mk(manual_nets), initial_budget=manual_budget, currency="USDT"
        )
    if auto_nets is not None:
        accounts["auto"] = build_account_report(
            "auto", _mk(auto_nets), initial_budget=auto_budget, currency="USDT"
        )
    return build_session_report(
        session_id=sid,
        day_id="day-1",
        instrument_key="BTC-USDT",
        as_of=_BASE,
        accounts=accounts,
        settlement_complete=True,
        reconciliation_ok=True,
        open_positions=0,
    )


# a manual account past MIN_TRADES so win-rate/ROI/profit-factor are populated
MANUAL = ["10", "-4", "8", "-2", "12", "-6", "5", "-1", "9", "-3"]
# an auto account under MIN_TRADES so sample metrics are honest nulls
AUTO = ["5", "-2"]


def test_json_roundtrip_equals_model_projection():
    report = _report(manual_nets=MANUAL, auto_nets=AUTO)
    parsed = json.loads(to_json(report))
    # the export is exactly the model's own to_dict — same ids, same canonical numbers
    assert parsed == report.to_dict()
    # pretty variant changes whitespace only, the parsed object is identical
    assert json.loads(to_json(report, pretty=True)) == parsed
    # net_pnl survives as the canonical Decimal string, not a lossy float
    assert parsed["accounts"]["manual"]["net_pnl"] == report.accounts["manual"].net_pnl
    assert Decimal(parsed["accounts"]["manual"]["net_pnl"]) == Decimal("28")


def test_csv_screen_parity_and_separate_accounts():
    report = _report(manual_nets=MANUAL, auto_nets=AUTO)
    rows = list(csv.DictReader(io.StringIO(to_csv(report))))
    # manual then auto, never a pooled third row
    assert [r["kind"] for r in rows] == ["manual", "auto"]
    manual = rows[0]
    d = report.to_dict()["accounts"]["manual"]
    # field-for-field parity with the on-screen figures (CSV cells are the string
    # projection of each model value)
    for key in ("net_pnl", "roi", "final_equity", "total_fees", "trades_closed"):
        assert manual[key] == str(d[key])
    # profit_factor (the float) is carried through, not stringified from a float guess
    assert manual["profit_factor"] == repr(d["profit_factor"])


def test_csv_honest_null_is_empty_cell_not_zero():
    report = _report(manual_nets=MANUAL, auto_nets=AUTO)
    rows = {r["kind"]: r for r in accounts_rows(report)}
    auto = rows["auto"]
    # below MIN_TRADES the auto win-rate is null → empty cell, and the reason is kept
    assert report.accounts["auto"].win_rate is None
    assert auto["win_rate"] == ""
    assert "win_rate:insufficient_sample" in auto["null_reasons"]
    # counts are still real zeros where they legitimately are (auto has 0 liquidations)
    assert auto["trades_liquidated"] == "0"


def test_csv_zero_budget_roi_is_blank_not_zero():
    report = _report(manual_nets=MANUAL, manual_budget="0")
    row = accounts_rows(report)[0]
    assert report.accounts["manual"].roi is None
    assert report.accounts["manual"].reasons["roi"] == "division_by_zero"
    assert row["roi"] == ""  # never "0"


def test_exports_are_byte_deterministic():
    report = _report(manual_nets=MANUAL, auto_nets=AUTO)
    assert to_csv(report) == to_csv(report)
    assert to_json(report) == to_json(report)
    assert to_html(report) == to_html(report)


def test_html_shows_numbers_escapes_and_flags_provisional():
    report = _report(manual_nets=MANUAL, auto_nets=AUTO, sid="s\"><script>x</script>")
    out = to_html(report)
    # injected markup in an id must be escaped, never emitted raw
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    # real numbers from the report appear verbatim
    assert report.accounts["manual"].net_pnl in out
    # null auto win-rate is an em-dash cell, not a zero
    assert "—" in out
    # the frozen snapshot hash rides along so the page can cite the stored row
    assert f'data-report-hash="{canonical_hash(report)}"' in out


def test_html_marks_non_final_report():
    report = build_session_report(
        session_id="sess-9", day_id="day-1", instrument_key="BTC-USDT",
        as_of=_BASE,
        accounts={"manual": build_account_report("manual", _mk(MANUAL), initial_budget="1000")},
        settlement_complete=False, reconciliation_ok=True, open_positions=0,
    )
    out = to_html(report)
    assert "v3-report-provisional" in out
    assert "settlement_pending" in out


def test_export_bundle_all_formats_agree_with_hash():
    report = _report(manual_nets=MANUAL, auto_nets=AUTO)
    bundle = export_bundle(report)
    assert set(bundle) == {"hash", "json", "csv", "html"}
    assert bundle["hash"] == canonical_hash(report)
    assert json.loads(bundle["json"]) == report.to_dict()
    # the CSV and HTML both describe exactly the two accounts present
    assert len(list(csv.DictReader(io.StringIO(bundle["csv"])))) == 2
    assert bundle["html"].count('data-report-hash=') == 1


def test_account_columns_cover_every_reportable_field():
    # the CSV header must include all the financial + count fields the screen shows
    for col in ("net_pnl", "roi", "max_drawdown", "win_rate", "expectancy",
                "profit_factor", "trades_closed", "trades_liquidated",
                "total_fees", "funding_cashflow", "null_reasons"):
        assert col in ACCOUNT_COLUMNS

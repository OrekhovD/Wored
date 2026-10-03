"""paper_trading.report_export — render a frozen V3 report to JSON / CSV / HTML (P6.3).

These renderers take a :class:`~paper_trading.session_report.SessionReportV1` (or
its already-canonical ``to_dict()``) and serialise it. They are **pure**: no DB,
no clock, no randomness. Everything downstream — the day-report screen, a JSON
download, a CSV row — is produced from the *same* ``to_dict()`` projection, so
"what the chart/table shows" and "what the export carries" cannot disagree
(ТЗ §6 / MC-18 round-trip, MC-20 exports).

Honesty rules baked in here:

  * a ``None`` metric is an **empty cell**, never a fabricated ``0`` — an
    ``insufficient_sample`` / ``division_by_zero`` ROI/win-rate stays blank and
    its reason is preserved;
  * ``manual`` and ``auto`` accounts are emitted as **separate rows** and are
    never summed together (no pooled total that would double-count the owner);
  * output is byte-deterministic for a given report, so an export can be
    re-checked against the ``canonical_hash`` stored on the ``simulation_reports``
    row (P6.2).
"""
from __future__ import annotations

import csv
import html
import io
import json
from typing import Any

from paper_trading.session_report import (
    SessionReportV1,
    canonical_hash,
)

__all__ = [
    "to_json",
    "accounts_rows",
    "to_csv",
    "to_html",
    "export_bundle",
]


# Ordered, stable columns for the account CSV — the screen table uses the same
# set, so a parsed export reproduces the on-screen figures field-for-field.
ACCOUNT_COLUMNS: tuple[str, ...] = (
    "kind",
    "currency",
    "initial_budget",
    "final_equity",
    "net_pnl",
    "realized_gross",
    "gross_traded",
    "entry_fees",
    "exit_fees",
    "total_fees",
    "funding_cashflow",
    "slippage_included",
    "roi",
    "max_drawdown",
    "win_rate",
    "expectancy",
    "profit_factor",
    "trades_closed",
    "trades_liquidated",
    "wins",
    "losses",
    "time_in_market_seconds",
    "rejected_entries",
    "null_reasons",
)


def _payload(report: SessionReportV1 | dict[str, Any]) -> dict[str, Any]:
    if isinstance(report, SessionReportV1):
        return report.to_dict()
    return report


def _cell(value: Any) -> str:
    """Project one field to a CSV/HTML cell.

    ``None`` becomes an empty string (never ``0``); booleans become lower-case
    ``true``/``false``; the float ``profit_factor`` is rendered through ``repr``
    so a given value is byte-stable across runs.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        # profit_factor is the only float; repr() is byte-stable for a given
        # value across runs, so it never drifts the export digest.
        return repr(value)
    return str(value)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def to_json(report: SessionReportV1 | dict[str, Any], *, pretty: bool = False) -> str:
    """Canonical JSON of the report snapshot (key-sorted, stable).

    ``pretty`` only changes whitespace, never a value, so the digest of the
    parsed object is identical either way.
    """
    payload = _payload(report)
    if pretty:
        return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    return json.dumps(
        payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False, default=str
    )


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def accounts_rows(report: SessionReportV1 | dict[str, Any]) -> list[dict[str, str]]:
    """One ordered dict per account (manual then auto), cells already projected.

    The ``null_reasons`` column folds the per-metric null reasons into a compact
    ``metric:reason; …`` string so a blank cell in the CSV is self-explaining.
    """
    payload = _payload(report)
    accounts = payload.get("accounts", {})
    rows: list[dict[str, str]] = []
    for kind in ("manual", "auto"):
        acct = accounts.get(kind)
        if acct is None:
            continue
        reasons = acct.get("reasons") or {}
        row = {col: _cell(acct.get(col)) for col in ACCOUNT_COLUMNS if col != "null_reasons"}
        row["kind"] = kind  # always present even if account omitted kind
        row["null_reasons"] = "; ".join(f"{k}:{v}" for k, v in sorted(reasons.items()))
        rows.append({col: row.get(col, "") for col in ACCOUNT_COLUMNS})
    return rows


def to_csv(report: SessionReportV1 | dict[str, Any]) -> str:
    """Account-level CSV with a stable header and ``\\n`` line endings.

    Deterministic: the column order is fixed by :data:`ACCOUNT_COLUMNS` and rows
    are always manual-then-auto, so two runs over one report yield identical text.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(ACCOUNT_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in accounts_rows(report):
        writer.writerow(row)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# HTML (server-rendered fragment — no JS, escaped values)
# ---------------------------------------------------------------------------


def _esc(value: Any) -> str:
    return html.escape(_cell(value), quote=True)


def to_html(report: SessionReportV1 | dict[str, Any]) -> str:
    """A small, escaped HTML fragment: a status banner + the account table.

    This is the *report body* the day-report page (P6.5) embeds; it deliberately
    carries no script and escapes every cell, so a numeric string can never turn
    into markup. Null metrics render as an empty ``—`` cell, not ``0``.
    """
    payload = _payload(report)
    is_final = bool(payload.get("is_final"))
    reason = payload.get("not_final_reason")
    status_cls = "v3-report-final" if is_final else "v3-report-provisional"
    status_text = "Final" if is_final else f"Provisional ({_esc(reason or 'not final')})"

    out: list[str] = []
    out.append('<div class="v3-report" data-report-hash="' + _esc(canonical_hash(payload)) + '">')
    out.append(
        '<header class="v3-report-meta"><h2>Session '
        + _esc(payload.get("session_id"))
        + "</h2>"
        + '<p class="v3-report-asof">as of '
        + _esc(payload.get("as_of"))
        + " · instrument "
        + _esc(payload.get("instrument_key"))
        + " · open positions "
        + _esc(payload.get("open_positions"))
        + "</p>"
        + '<span class="v3-report-status '
        + status_cls
        + '">'
        + status_text
        + "</span></header>"
    )

    out.append('<table class="v3-report-table"><thead><tr>')
    out.append("<th>Account</th><th>Net P&amp;L</th><th>Final equity</th><th>ROI</th>")
    out.append("<th>Max DD</th><th>Win rate</th><th>Expectancy</th><th>Profit factor</th>")
    out.append("<th>Closed</th><th>Liquidated</th><th>Fees</th><th>Funding</th>")
    out.append("</tr></thead><tbody>")
    accounts = payload.get("accounts", {})
    for kind in ("manual", "auto"):
        acct = accounts.get(kind)
        if acct is None:
            continue
        out.append("<tr>")
        out.append("<td>" + _esc(kind) + "</td>")
        for key in (
            "net_pnl", "final_equity", "roi", "max_drawdown", "win_rate",
            "expectancy", "profit_factor", "trades_closed", "trades_liquidated",
            "total_fees", "funding_cashflow",
        ):
            cell = _esc(acct.get(key))
            out.append("<td>" + (cell if cell != "" else "—") + "</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    out.append("</div>")
    return "".join(out)


# ---------------------------------------------------------------------------
# bundle
# ---------------------------------------------------------------------------


def export_bundle(report: SessionReportV1 | dict[str, Any]) -> dict[str, str]:
    """All three serialisations plus the canonical hash, as one dict.

    Handy for the API (return a format) and for the acceptance test that checks
    the JSON, CSV and HTML agree with the stored snapshot's hash.
    """
    payload = _payload(report)
    return {
        "hash": canonical_hash(payload),
        "json": to_json(payload),
        "csv": to_csv(payload),
        "html": to_html(payload),
    }

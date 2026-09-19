"""Evidence and promotion gates for the daily paper-trading review."""
from __future__ import annotations

from webui.paper_learning import build_learning_review, compare_accounts


def account_report(net: str, trades: int) -> dict[str, object]:
    return {"realized_net": net, "trades_count": trades}


def trade(*, gross: str = "0", fees: str = "1") -> dict[str, str]:
    return {"gross_pnl": gross, "total_fees": fees}


def test_comparison_requires_closed_trades_on_both_accounts() -> None:
    result = compare_accounts(account_report("5", 1), account_report("0", 0))
    assert result["status"] == "insufficient_data"
    assert result["winner"] is None


def test_small_sample_creates_evidence_but_applies_no_rule() -> None:
    result = build_learning_review(
        account_report("-1", 1), account_report("0", 0), [trade()]
    )
    assert result["sample_size"] == 1
    assert {finding["code"] for finding in result["findings"]} == {
        "sample_too_small",
        "fee_drag",
    }
    assert result["promotion"]["status"] == "not_ready"
    assert result["promotion"]["applied_rules"] == []


def test_large_sample_only_advances_candidate_to_validation() -> None:
    trades = [trade(gross="0.1", fees="0.2") for _ in range(20)]
    result = build_learning_review(
        account_report("-2", 10), account_report("-2", 10), trades
    )
    assert result["promotion"]["status"] == "ready_for_validation"
    assert result["promotion"]["applied_rules"] == []

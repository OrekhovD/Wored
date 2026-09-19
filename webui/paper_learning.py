"""Deterministic daily review for paper-trading results.

The review produces evidence and candidate rules. It never claims that a rule
was learned or promoted when the available sample is too small.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


def _number(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def compare_accounts(manual: dict[str, Any], auto: dict[str, Any]) -> dict[str, Any]:
    manual_trades = int(manual.get("trades_count", 0))
    auto_trades = int(auto.get("trades_count", 0))
    if manual_trades == 0 or auto_trades == 0:
        return {
            "status": "insufficient_data",
            "winner": None,
            "reason": "Для сравнения оба счёта должны иметь закрытые сделки",
            "manual_trades": manual_trades,
            "auto_trades": auto_trades,
        }
    manual_net = _number(manual.get("realized_net"))
    auto_net = _number(auto.get("realized_net"))
    winner = "tie" if manual_net == auto_net else ("manual" if manual_net > auto_net else "auto")
    return {
        "status": "ready",
        "winner": winner,
        "reason": "Сравнение по чистому результату после комиссий",
        "manual_trades": manual_trades,
        "auto_trades": auto_trades,
        "net_difference": format(manual_net - auto_net, "f"),
    }


def build_learning_review(
    manual: dict[str, Any],
    auto: dict[str, Any],
    closed_trades: list[dict[str, Any]],
) -> dict[str, Any]:
    sample_size = len(closed_trades)
    total_fees = sum((_number(item.get("total_fees")) for item in closed_trades), Decimal(0))
    gross_pnl = sum((_number(item.get("gross_pnl")) for item in closed_trades), Decimal(0))
    findings: list[dict[str, Any]] = []

    if sample_size < 3:
        findings.append({
            "code": "sample_too_small",
            "severity": "info",
            "evidence": f"Закрытых сделок: {sample_size}; минимум для кандидата правила: 3",
            "hypothesis": "Данных недостаточно для изменения торгового правила",
            "proposed_change": "Не менять стратегию; продолжить сбор одинаково рассчитанных сделок",
            "status": "insufficient_data",
        })
    if sample_size and total_fees >= abs(gross_pnl):
        findings.append({
            "code": "fee_drag",
            "severity": "warning",
            "evidence": (
                f"Комиссии {format(total_fees, 'f')} USDT; "
                f"валовый PnL {format(gross_pnl, 'f')} USDT"
            ),
            "hypothesis": "Издержки поглощают валовое преимущество текущих входов",
            "proposed_change": "Проверить минимальный ожидаемый ход относительно двух комиссий",
            "status": "candidate",
        })

    promotable = sample_size >= 20 and any(item["status"] == "candidate" for item in findings)
    return {
        "schema_version": 1,
        "status": "candidate_review" if findings else "no_findings",
        "sample_size": sample_size,
        "comparison": compare_accounts(manual, auto),
        "findings": findings,
        "promotion": {
            "status": "ready_for_validation" if promotable else "not_ready",
            "minimum_sample": 20,
            "applied_rules": [],
            "reason": (
                "Кандидат должен пройти replay и out-of-sample проверку"
                if promotable
                else "Недостаточно наблюдений для автоматического изменения стратегии"
            ),
        },
    }

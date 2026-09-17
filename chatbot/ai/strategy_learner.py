"""Create reviewable strategy candidates from closed simulation results.

LLM output is advisory and always stored with ``candidate`` status. Activation
requires a separate deterministic replay/out-of-sample validation step.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from ai.contracts import NormalizedRequest, RoutingMode
from ai.provider_gateway import ProviderGateway


log = logging.getLogger(__name__)

STRATEGY_SCHEMA = {
    "type": "object",
    "required": ["adjustments", "summary", "confidence"],
    "properties": {
        "adjustments": {"type": "array"},
        "summary": {"type": "string"},
        "confidence": {"enum": ["high", "medium", "low"]},
    },
}

STRATEGY_LEARNER_PROMPT = """Ты — Daily Learner симулятора WORED.
Проанализируй только закрытые прогнозы и симуляционные позиции. Создай кандидаты
корректировок; не объявляй их активными и не утверждай, что результат гарантирован.

Метрики:
- Winrate: {winrate}%
- Средний PnL: {avg_pnl}
- Max Drawdown: {max_drawdown}
- Доля ликвидаций: {liquidation_rate}%
- Всего закрытых позиций: {total}
- Побед/Поражений: {wins}/{losses}
- Лучший/Худший PnL: {best_pnl}/{worst_pnl}

Активные правила:
{current_rules}

Верни строго JSON:
{{
  "adjustments": [
    {{"parameter": "max_leverage", "old": 10, "new": 8, "reason": "причина"}}
  ],
  "summary": "краткий вывод на русском",
  "confidence": "high|medium|low"
}}
"""


def _candidate_keys() -> list[str]:
    models = [
        os.getenv("OLLAMA_PREMIUM_MODEL", "glm-5.2"),
        os.getenv("OLLAMA_ANALYST_MODEL", "deepseek-v4-pro"),
        os.getenv("OLLAMA_ORACLE_MODEL", "minimax-m3"),
    ]
    return list(dict.fromkeys(f"ollama-cloud/{model.strip()}" for model in models if model.strip()))


def _validate_candidate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("strategy result must be an object")
    adjustments = value.get("adjustments")
    if not isinstance(adjustments, list) or len(adjustments) > 20:
        raise ValueError("adjustments must be a list with at most 20 items")
    normalized: list[dict[str, Any]] = []
    for item in adjustments:
        if not isinstance(item, dict):
            raise ValueError("each adjustment must be an object")
        parameter = str(item.get("parameter", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if not parameter or not reason or len(reason) > 500:
            raise ValueError("adjustment requires parameter and bounded reason")
        normalized.append({
            "parameter": parameter,
            "old": item.get("old"),
            "new": item.get("new"),
            "reason": reason,
        })
    confidence = str(value.get("confidence", "")).lower()
    if confidence not in {"high", "medium", "low"}:
        raise ValueError("confidence must be high, medium or low")
    summary = str(value.get("summary", "")).strip()
    if not summary or len(summary) > 1000:
        raise ValueError("summary is required and limited to 1000 characters")
    return {"adjustments": normalized, "summary": summary, "confidence": confidence}


async def run_strategy_learner(
    evaluation: dict,
    *,
    gateway: ProviderGateway | None = None,
) -> dict:
    """Generate and persist one candidate; never activate it."""
    try:  # container PYTHONPATH=/app
        from storage.postgres_client import get_latest_strategy_rules, save_strategy_rules
    except ModuleNotFoundError:  # repository test imports can already own `storage`
        from chatbot.storage.postgres_client import (
            get_latest_strategy_rules,
            save_strategy_rules,
        )

    current = await get_latest_strategy_rules(status="active")
    current_rules = json.dumps(current["rules"], ensure_ascii=False) if current else "нет"
    details = evaluation.get("details", {})
    prompt = STRATEGY_LEARNER_PROMPT.format(
        winrate=evaluation.get("winrate", 0),
        avg_pnl=evaluation.get("avg_pnl", 0),
        max_drawdown=evaluation.get("max_drawdown", 0),
        liquidation_rate=evaluation.get("liquidation_rate", 0),
        total=evaluation.get("total", 0),
        wins=details.get("wins", 0),
        losses=details.get("losses", 0),
        best_pnl=details.get("best_pnl", 0),
        worst_pnl=details.get("worst_pnl", 0),
        current_rules=current_rules,
    )
    request = NormalizedRequest(
        task_type="strategy_learning",
        source="chatbot",
        principal="system:daily-learner",
        messages=[{"role": "system", "content": prompt}],
        routing_mode=RoutingMode.BALANCED,
        output_schema=STRATEGY_SCHEMA,
        max_output_tokens=1024,
        timeout_seconds=60,
    )
    response = await (gateway or ProviderGateway()).execute(request, _candidate_keys())
    evidence = {
        "evaluation_run_id": evaluation.get("evaluation_run_id"),
        "sample_size": int(evaluation.get("total", 0) or 0),
        "request_id": response.request_id,
        "provider": response.actual_provider,
        "model": response.actual_model,
    }
    if response.error_code is None:
        parsed = response.parsed_json
        if parsed is None and response.final_text:
            parsed = json.loads(response.final_text)
        candidate = _validate_candidate(parsed)
        source = "daily_learner_llm"
    else:
        candidate = _heuristic_candidate(evaluation)
        source = "daily_learner_heuristic"
        evidence["gateway_error"] = response.error_code.value

    version = (current["version"] + 1) if current else 1
    candidate.update({
        "status": "candidate",
        "version": version,
        "validation_required": ["minimum_sample", "replay", "out_of_sample", "risk_limits"],
    })
    await save_strategy_rules(
        candidate,
        version=version,
        source=source,
        status="candidate",
        evidence=evidence,
    )
    return candidate


def _heuristic_candidate(evaluation: dict) -> dict[str, Any]:
    wr = float(evaluation.get("winrate", 0) or 0)
    liq = float(evaluation.get("liquidation_rate", 0) or 0)
    avg_pnl = float(evaluation.get("avg_pnl", 0) or 0)
    max_dd = float(evaluation.get("max_drawdown", 0) or 0)
    adjustments: list[dict[str, Any]] = []
    if liq > 20:
        adjustments.append({"parameter": "max_leverage", "old": 10, "new": 8, "reason": f"Ликвидации {liq:.0f}% выше лимита 20%"})
    if wr < 40:
        adjustments.append({"parameter": "minimum_confidence", "old": 0.60, "new": 0.65, "reason": f"Winrate {wr:.0f}% ниже 40%"})
    if avg_pnl < 0:
        adjustments.append({"parameter": "minimum_confirmations", "old": 1, "new": 2, "reason": f"Средний PnL {avg_pnl:.2f} отрицательный"})
    if max_dd > 50:
        adjustments.append({"parameter": "risk_per_trade_pct", "old": 1.0, "new": 0.75, "reason": f"Max drawdown {max_dd:.0f} превышает 50"})
    return {
        "adjustments": adjustments,
        "summary": f"Winrate {wr:.0f}%, ликвидации {liq:.0f}%, средний PnL {avg_pnl:.2f}",
        "confidence": "low",
    }

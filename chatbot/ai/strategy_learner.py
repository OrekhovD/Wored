"""
Strategy Learner - Premium model analyzes sim evaluation results
and generates corrective trading rules.
"""
from __future__ import annotations

import asyncio
import json
import logging

log = logging.getLogger(__name__)

STRATEGY_LEARNER_PROMPT = """Ты — Strategy Learner криптотрейдера.
Проанализируй результаты серии симуляций и предложи корректирующие правила.

Метрики:
- Winrate: {winrate}%
- Средний PnL: {avg_pnl}
- Max Drawdown: {max_drawdown}
- Доля ликвидаций: {liquidation_rate}%
- Всего позиций: {total}
- Побед/Поражений: {wins}/{losses}
- Лучший/Худший PnL: {best_pnl}/{worst_pnl}

Правила текущей стратегии:
{current_rules}

Верни СТРОГО JSON (без markdown):
{{
  "adjustments": [
    {{"parameter": "rsi_threshold", "old": 70, "new": 65, "reason": "причина"}},
    {{"parameter": "min_leverage", "old": 100, "new": 120, "reason": "причина"}}
  ],
  "summary": "краткий вывод на русском",
  "confidence": "high|medium|low"
}}

Правила корректировки:
- Если winrate < 40% → ужесточить фильтры входа
- Если liquidation_rate > 20% → снизить максимальное плечо
- Если avg_pnl < 0 → пересмотреть условия входа
- Если max_drawdown > 50% → добавить стоп-лосс правила
- Не меняй правила если метрики хорошие (winrate > 60%, liq < 10%)"""


async def run_strategy_learner(evaluation: dict) -> dict | None:
    """
    Run Strategy Learner on evaluation results.
    Returns corrective rules dict.
    """
    from ai.models import MODELS, PREMIUM_MODEL_CHAIN
    from ai.router import get_client

    # Get current rules
    from storage.postgres_client import get_latest_strategy_rules
    current = await get_latest_strategy_rules()
    current_rules_str = json.dumps(current["rules"]) if current else "нет (первые правила)"

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
        current_rules=current_rules_str,
    )

    for tier in PREMIUM_MODEL_CHAIN:
        cfg = MODELS.get(tier)
        if not cfg:
            continue
        client = get_client(tier)
        if client is None:
            continue

        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=cfg.model_id,
                    messages=[{"role": "system", "content": prompt}],
                    max_tokens=1024,
                    temperature=0.3,
                ),
                timeout=cfg.timeout,
            )
            raw = (response.choices[0].message.content or "").strip()
            if raw.startswith("```"):
                lines = raw.splitlines()
                if lines and lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()

            result = json.loads(raw)
            log.info("Strategy Learner (%s): %s", cfg.model_id, result.get("summary", "?"))

            # Save rules to DB
            from storage.postgres_client import save_strategy_rules
            version = (current["version"] + 1) if current else 1
            await save_strategy_rules(result, version=version)

            return result
        except Exception as exc:
            log.warning("Strategy Learner failed on %s: %s", cfg.model_id, exc)
            continue

    log.error("Strategy Learner failed across all premium models")
    # Heuristic fallback: generate rules from metrics without AI
    return await _heuristic_rules(evaluation, current)


async def _heuristic_rules(evaluation: dict, current: dict | None) -> dict:
    """Generate corrective rules from metrics using simple heuristics."""
    wr = evaluation.get("winrate", 0)
    liq = evaluation.get("liquidation_rate", 0)
    avg_pnl = evaluation.get("avg_pnl", 0)
    max_dd = evaluation.get("max_drawdown", 0)
    adjustments = []

    if liq > 20:
        adjustments.append({"parameter": "max_leverage", "old": 200, "new": 150, "reason": f"Доля ликвидаций {liq:.0f}% > 20%"})
    if wr < 40:
        adjustments.append({"parameter": "rsi_entry_filter", "old": 50, "new": 60, "reason": f"Winrate {wr:.0f}% < 40%, ужесточить фильтр входа"})
    if avg_pnl < 0:
        adjustments.append({"parameter": "min_confirmation_signals", "old": 1, "new": 2, "reason": f"Средний PnL {avg_pnl:.2f} < 0, требовать больше подтверждений"})
    if max_dd > 50:
        adjustments.append({"parameter": "stop_loss_pct", "old": 0, "new": 5, "reason": f"Max drawdown {max_dd:.0f}% > 50%, добавить стоп-лосс 5%"})
    if wr > 60 and liq < 10:
        adjustments.append({"parameter": "status", "old": "active", "new": "active", "reason": "Метрики в норме, правила не меняются"})

    summary = f"Winrate {wr:.0f}%, ликвидации {liq:.0f}%, avg PnL {avg_pnl:.2f}"
    confidence = "medium"

    result = {"adjustments": adjustments, "summary": summary, "confidence": confidence}

    # Save to DB
    try:
        from storage.postgres_client import save_strategy_rules
        version = (current["version"] + 1) if current else 1
        await save_strategy_rules(result, version=version, source="heuristic")
    except Exception as exc:
        log.error("Heuristic rules save failed: %s", exc)

    return result

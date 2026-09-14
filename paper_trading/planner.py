"""Paper Trading AI Planner — generates trading plans via existing provider gateway.

Uses the existing chatbot AI router/provider gateway. No new paid models.
No_trade is valid in all risk profiles. No forced entries.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4

log = logging.getLogger(__name__)

# AI plan configuration (from REQ-12 env vars, with defaults)
DEFAULT_AI_CONFIG = {
    "min_interval_seconds": 300,     # PAPER_AI_MIN_INTERVAL_SECONDS
    "plan_ttl_seconds": 3600,        # PAPER_AI_PLAN_TTL_SECONDS
    "max_requests_per_day": 48,      # PAPER_AI_MAX_REQUESTS_PER_DAY
    "max_tokens_per_day": 200000,    # PAPER_AI_MAX_TOKENS_PER_DAY
}


@dataclass
class PlanEntry:
    """Executable entry from AI plan."""
    side: str               # "long" | "short"
    entry_zone_from: Decimal
    entry_zone_to: Decimal
    trigger_type: str       # "zone_reclaim_confirmed" | "breakout_confirmation" | "range_bound"
    confirmation_rule: str
    invalidation_price: Decimal
    stop_loss: Decimal
    take_profit: List[Decimal]
    recommended_leverage: int
    budget_share_pct: Decimal
    margin_mode: str = "isolated"
    reason_code: str = ""


@dataclass
class AIPlan:
    """Validated AI trading plan."""
    plan_id: str
    version: int
    schema_version: int = 2
    strategy: str = "ai_plan"
    instrument: str = "BTC-USDT"
    snapshot_id: str = ""
    snapshot_time: str = ""
    ttl_expires_at: str = ""
    market_regime: str = "unknown"
    thesis: str = ""
    primary_scenario: str = "no_trade"
    alternative_scenario: str = ""
    no_trade_condition: str = ""
    entries: List[PlanEntry] = field(default_factory=list)
    rejected_entries: List[Dict[str, Any]] = field(default_factory=list)
    model_used: str = "unknown"
    provider: str = ""
    latency_ms: int = 0
    tokens_used: int = 0
    validation_status: str = "no_trade"  # "accepted" | "rejected" | "no_trade"
    decision_code: str = ""
    decision_at: str = ""


PLAN_PROMPT_TEMPLATE = """Ты — Crypto Trader Agent (Analyst), эксперт по BTC-USDT perpetual futures на HTX.

Тебе передан Market Context Snapshot в JSON. На основе этих данных сформируй торговый план.

Верни СТРОГО JSON (без markdown, без ```json) следующей структуры:
{{
  "market_regime": "trend_up|trend_down|range|volatile|unknown",
  "thesis": "краткий тезис на русском",
  "primary_scenario": "long_on_reclaim|short_on_breakdown|range_trade|no_trade",
  "alternative_scenario": "краткий текст",
  "no_trade_condition": "условие когда не торговать",
  "entries": [
    {{
      "side": "long|short",
      "entry_zone_from": 0.0,
      "entry_zone_to": 0.0,
      "trigger_type": "zone_reclaim_confirmed|breakout_confirmation|range_bound",
      "confirmation_rule": "close_above_zone_on_1m_and_rsi_gt_50|rsi_gt_50|any",
      "invalidation_price": 0.0,
      "stop_loss": 0.0,
      "take_profit": [0.0, 0.0],
      "recommended_leverage": 100,
      "budget_share_pct": 15.0,
      "margin_mode": "isolated",
      "reason_code": "trend_pullback_entry|breakout_entry|range_entry"
    }}
  ]
}}

Правила:
- Не более 3 заявок. no_trade с entries=[] допустим в ЛЮБОМ режиме риска.
- LONG: SL < invalidation < zone_from <= zone_to < TP1 < TP2.
- SHORT: TP2 < TP1 < zone_from <= zone_to < invalidation < SL.
- Плечо только 10, 25, 50, 100.
- Доля маржи: defensive 5–10%, balanced 10–20%, aggressive 20–30%.
- Чистая прибыль до TP1 / чистый убыток до SL >= 1. Комиссия 0.06%, slippage 2 bps.
- Если требования несовместимы, выбери no_trade.
- primary_scenario должен быть ТОЛЬКО одним из: long_on_reclaim, short_on_breakdown, range_trade, no_trade (без пояснений).
- При no_trade entries=[] обязательно.

Режим риска: {risk_mode}
Бюджет: {budget_usdt} USDT
Направление: {trade_direction}
Горизонт: {trade_horizon}
Целевая чистая прибыль: {target_net_profit_usdt} USDT

Рыночный контекст:
{market_context}
"""


class AIPlanner:
    """Generates AI trading plans via existing provider gateway."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = {**DEFAULT_AI_CONFIG, **(config or {})}
        self._last_request_at: Dict[str, float] = {}  # per account
        self._daily_request_count: Dict[str, int] = {}  # per account/day
        self._daily_token_count: Dict[str, int] = {}  # per account/day

    def can_request(self, account_id: str, day_id: str) -> tuple[bool, str]:
        """Check if AI request is allowed (interval, quota)."""
        now = time.time()

        # Check min interval
        last = self._last_request_at.get(account_id, 0)
        if now - last < self.config["min_interval_seconds"]:
            remaining = int(self.config["min_interval_seconds"] - (now - last))
            return False, f"interval_not_elapsed ({remaining}s remaining)"

        # Check daily request quota
        day_key = f"{account_id}:{day_id}"
        count = self._daily_request_count.get(day_key, 0)
        if count >= self.config["max_requests_per_day"]:
            return False, "quota_exceeded"

        # Check daily token quota
        tokens = self._daily_token_count.get(day_key, 0)
        if tokens >= self.config["max_tokens_per_day"]:
            return False, "token_quota_exceeded"

        return True, "ok"

    def record_request(self, account_id: str, day_id: str, tokens_used: int):
        """Record an AI request for quota tracking."""
        now = time.time()
        self._last_request_at[account_id] = now
        day_key = f"{account_id}:{day_id}"
        self._daily_request_count[day_key] = self._daily_request_count.get(day_key, 0) + 1
        self._daily_token_count[day_key] = self._daily_token_count.get(day_key, 0) + tokens_used

    async def generate_plan(
        self,
        market_context: Dict[str, Any],
        risk_mode: str = "balanced",
        budget_usdt: float = 100.0,
        trade_direction: str = "auto",
        trade_horizon: str = "fast",
        target_net_profit_usdt: float = 1.5,
        account_id: str = "",
        day_id: str = "",
    ) -> AIPlan:
        """Generate an AI trading plan via provider gateway."""
        # Check quota
        allowed, reason = self.can_request(account_id, day_id)
        if not allowed:
            return AIPlan(
                plan_id=str(uuid4()),
                version=0,
                decision_code=reason,
                decision_at=datetime.now(timezone.utc).isoformat(),
            )

        # Build prompt
        prompt = PLAN_PROMPT_TEMPLATE.format(
            market_context=json.dumps(market_context, indent=2, ensure_ascii=False),
            risk_mode=risk_mode,
            budget_usdt=budget_usdt,
            trade_direction=trade_direction,
            trade_horizon=trade_horizon,
            target_net_profit_usdt=target_net_profit_usdt,
        )

        # Call AI via existing gateway
        # This will be integrated with chatbot/ai/router.py in T06
        # For now, this is the interface
        t0 = time.time()
        try:
            raw_response, model_used, tokens = await self._call_ai(prompt)
            latency_ms = int((time.time() - t0) * 1000)

            # Record usage
            self.record_request(account_id, day_id, tokens)

            # Parse JSON response
            plan = self._parse_response(raw_response, model_used, latency_ms, tokens)
            return plan

        except asyncio.TimeoutError:
            return AIPlan(
                plan_id=str(uuid4()),
                version=0,
                decision_code="timeout",
                decision_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            log.warning("AI plan generation failed: %s", exc)
            return AIPlan(
                plan_id=str(uuid4()),
                version=0,
                decision_code="engine_error",
                decision_at=datetime.now(timezone.utc).isoformat(),
            )

    async def _call_ai(self, prompt: str) -> tuple[str, str, int]:
        """Call AI provider — to be connected to existing gateway in T06."""
        # Placeholder — will be connected to chatbot.ai.router in adapter
        raise NotImplementedError("AI gateway integration in T06")

    def _parse_response(
        self, raw: str, model_used: str, latency_ms: int, tokens: int
    ) -> AIPlan:
        """Parse AI response into AIPlan."""
        # Strip markdown fences
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        # Find first valid JSON object
        json_start = raw.find("{")
        if json_start == -1:
            return AIPlan(
                plan_id=str(uuid4()),
                version=0,
                model_used=model_used,
                decision_code="invalid_schema",
                decision_at=datetime.now(timezone.utc).isoformat(),
            )

        try:
            decoder = json.JSONDecoder()
            data = decoder.raw_decode(raw[json_start:])[0]
        except (json.JSONDecodeError, ValueError):
            # Fallback: first { to last }
            json_end = raw.rfind("}")
            if json_end <= json_start:
                return AIPlan(
                    plan_id=str(uuid4()),
                    version=0,
                    model_used=model_used,
                    decision_code="invalid_schema",
                    decision_at=datetime.now(timezone.utc).isoformat(),
                )
            try:
                data = json.loads(raw[json_start:json_end + 1])
            except Exception:
                return AIPlan(
                    plan_id=str(uuid4()),
                    version=0,
                    model_used=model_used,
                    decision_code="invalid_schema",
                    decision_at=datetime.now(timezone.utc).isoformat(),
                )

        # Normalize no_trade
        ps = data.get("primary_scenario", "")
        if not data.get("entries") and isinstance(ps, str) and "no_trade" in ps.lower():
            data["primary_scenario"] = "no_trade"

        # Parse entries
        entries: List[PlanEntry] = []
        for e in data.get("entries", []):
            try:
                entries.append(PlanEntry(
                    side=e["side"],
                    entry_zone_from=Decimal(str(e["entry_zone_from"])),
                    entry_zone_to=Decimal(str(e["entry_zone_to"])),
                    trigger_type=e.get("trigger_type", "zone_reclaim_confirmed"),
                    confirmation_rule=e.get("confirmation_rule", "rsi_gt_50"),
                    invalidation_price=Decimal(str(e["invalidation_price"])),
                    stop_loss=Decimal(str(e["stop_loss"])),
                    take_profit=[Decimal(str(t)) for t in e.get("take_profit", [])],
                    recommended_leverage=int(e.get("recommended_leverage", 10)),
                    budget_share_pct=Decimal(str(e.get("budget_share_pct", "15"))),
                    margin_mode=e.get("margin_mode", "isolated"),
                    reason_code=e.get("reason_code", ""),
                ))
            except (KeyError, ValueError, TypeError):
                continue

        # Determine validation status
        if not entries:
            status = "no_trade"
        else:
            status = "accepted"  # Risk validation in service layer

        now = datetime.now(timezone.utc)
        ttl = now + timedelta(seconds=self.config["plan_ttl_seconds"])

        return AIPlan(
            plan_id=str(uuid4()),
            version=1,
            market_regime=data.get("market_regime", "unknown"),
            thesis=data.get("thesis", ""),
            primary_scenario=data.get("primary_scenario", "no_trade"),
            alternative_scenario=data.get("alternative_scenario", ""),
            no_trade_condition=data.get("no_trade_condition", ""),
            entries=entries,
            model_used=model_used,
            latency_ms=latency_ms,
            tokens_used=tokens,
            validation_status=status,
            ttl_expires_at=ttl.isoformat(),
            snapshot_time=now.isoformat(),
            decision_at=now.isoformat(),
        )
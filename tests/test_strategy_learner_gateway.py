"""Daily Learner must use ProviderGateway and persist candidates only."""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "chatbot"), str(ROOT)]

from ai.contracts import ErrorCode, NormalizedResponse
from ai.strategy_learner import run_strategy_learner


class FakeGateway:
    def __init__(self, response: NormalizedResponse) -> None:
        self.response = response
        self.calls = []

    async def execute(self, request, candidates):
        self.calls.append((request, candidates))
        return self.response


def evaluation() -> dict:
    return {
        "evaluation_run_id": "eval-1",
        "total": 12,
        "winrate": 35,
        "avg_pnl": -2.5,
        "max_drawdown": 18,
        "liquidation_rate": 8,
        "details": {"wins": 4, "losses": 8, "best_pnl": 10, "worst_pnl": -12},
    }


def fake_storage() -> tuple[types.ModuleType, AsyncMock]:
    module = types.ModuleType("storage.postgres_client")
    module.get_latest_strategy_rules = AsyncMock(return_value=None)
    save = AsyncMock(return_value=True)
    module.save_strategy_rules = save
    return module, save


@pytest.mark.asyncio
async def test_learner_routes_through_gateway_and_saves_candidate() -> None:
    gateway = FakeGateway(NormalizedResponse(
        final_text='{"adjustments":[{"parameter":"minimum_confidence","old":0.6,"new":0.65,"reason":"низкий winrate"}],"summary":"ужесточить вход","confidence":"medium"}',
        actual_provider="ollama-cloud",
        actual_model="glm-5.2",
        request_id="request-1",
    ))
    storage_module, save = fake_storage()
    with patch.dict(sys.modules, {"storage.postgres_client": storage_module}):
        result = await run_strategy_learner(evaluation(), gateway=gateway)

    assert result["status"] == "candidate"
    assert result["validation_required"] == [
        "minimum_sample", "replay", "out_of_sample", "risk_limits"
    ]
    assert gateway.calls[0][0].task_type == "strategy_learning"
    assert gateway.calls[0][0].output_schema is not None
    assert gateway.calls[0][1][0].startswith("ollama-cloud/")
    assert save.await_args.kwargs["status"] == "candidate"
    assert save.await_args.kwargs["evidence"]["request_id"] == "request-1"


@pytest.mark.asyncio
async def test_gateway_failure_creates_low_confidence_candidate() -> None:
    gateway = FakeGateway(NormalizedResponse(
        final_text="",
        error_code=ErrorCode.PROVIDER_UNAVAILABLE,
        request_id="request-2",
    ))
    storage_module, save = fake_storage()
    with patch.dict(sys.modules, {"storage.postgres_client": storage_module}):
        result = await run_strategy_learner(evaluation(), gateway=gateway)

    assert result["status"] == "candidate"
    assert result["confidence"] == "low"
    assert save.await_args.kwargs["source"] == "daily_learner_heuristic"
    assert save.await_args.kwargs["evidence"]["gateway_error"] == "provider_unavailable"

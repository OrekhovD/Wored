from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

log = logging.getLogger("webui.prediction_engine")

DEFAULT_ANALYST_QWEN_MODEL = "qwen3.6-35b-a3b"
DEFAULT_PREMIUM_QWEN_MODEL = "qwen3.6-27b"
DEFAULT_WORKER_GEMINI_MODEL = "gemini-3-flash-preview"
PROVIDER_COOLDOWN_SECONDS = {"ollama": 1.0, "glm": 1.8, "gemini": 1.0, "dashscope": 1.4, "minimax": 1.0}
RETRY_BACKOFF_SECONDS = (2.0, 5.0)
GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4/"
DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "https://ollama.com/v1")

PREDICTION_SYSTEM_PROMPT = """
You are a crypto market forecasting agent inside a model-vs-model evaluation lab.

Rules:
1. Stay neutral by default. Do not force bullish or bearish bias.
2. If evidence is mixed, keep projected moves small and confidence modest.
3. Forecast every hour in the requested horizon.
4. Each forecasted change_pct is measured from the base price at prediction time, not from the previous hour.
5. Use only the supplied market context. Do not invent news or external data.
6. Return strict JSON only. No markdown fences. No prose outside JSON.
7. Keep the summary short and every rationale compact. Prefer one brief sentence fragment per hour.

Required JSON shape:
{
  "summary": "short neutral outlook",
  "points": [
    {
      "hour": 1,
      "price": 123.45,
      "change_pct": 0.82,
      "confidence": 61,
      "rationale": "short reason"
    }
  ]
}

The points array must include exactly one item for every hour from 1 to horizon_hours.
""".strip()


@dataclass(frozen=True)
class PredictionModelConfig:
    key: str
    name: str
    model_id: str
    base_url: str
    api_key_env: str
    tier: str
    timeout: float
    max_tokens: int
    temperature: float = 0.2


@dataclass(frozen=True)
class RuntimeModelCandidate:
    cache_key: str
    model_id: str
    base_url: str
    api_key_env: str
    timeout: float


@dataclass(frozen=True)
class PredictionPoint:
    hour: int
    predicted_price: float
    predicted_change_pct: float
    confidence: float | None
    rationale: str


@dataclass
class ModelPredictionResult:
    key: str
    name: str
    model_id: str
    tier: str
    status: str
    summary: str = ""
    error_message: str | None = None
    points: list[PredictionPoint] = field(default_factory=list)


MODEL_ORDER = ["analyst", "premium", "minimax"]


def _parse_model_csv(raw_value: str | None) -> list[str]:
    items: list[str] = []
    for item in (raw_value or "").split(","):
        normalized = item.strip()
        if normalized and normalized not in items:
            items.append(normalized)
    return items

MODEL_CONFIGS: dict[str, PredictionModelConfig] = {
    "worker": PredictionModelConfig(
        key="worker",
        name="Worker / Ollama Auto",
        model_id=os.getenv("OLLAMA_WORKER_MODEL", "deepseek-v4-flash"),
        base_url=OLLAMA_BASE_URL,
        api_key_env="OLLAMA_API_KEY",
        tier="worker",
        timeout=20.0,
        max_tokens=1200,
    ),
    "analyst": PredictionModelConfig(
        key="analyst",
        name="Analyst / Ollama Reasoning",
        model_id=os.getenv("OLLAMA_ANALYST_MODEL", "glm-5.1"),
        base_url=OLLAMA_BASE_URL,
        api_key_env="OLLAMA_API_KEY",
        tier="analyst",
        timeout=40.0,
        max_tokens=1600,
    ),
    "premium": PredictionModelConfig(
        key="premium",
        name="Strategist / Ollama Reasoning",
        model_id=os.getenv("OLLAMA_PREMIUM_MODEL", "glm-5.2"),
        base_url=OLLAMA_BASE_URL,
        api_key_env="OLLAMA_API_KEY",
        tier="premium",
        timeout=45.0,
        max_tokens=1600,
    ),
    "minimax": PredictionModelConfig(
        key="minimax",
        name="Oracle / Ollama Thinking",
        model_id=os.getenv("OLLAMA_ORACLE_MODEL", "kimi-k2-thinking"),
        base_url=OLLAMA_BASE_URL,
        api_key_env="OLLAMA_API_KEY",
        tier="minimax",
        timeout=60.0,
        max_tokens=700,
        temperature=0.15,
    ),
}

_clients: dict[str, AsyncOpenAI | None] = {}


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip().replace("%", "").replace(",", "")
        if not normalized:
            return None
        return float(normalized)
    return None


def _extract_json_payload(raw_text: str) -> dict[str, Any] | list[Any]:
    text = raw_text.strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])

    raise ValueError("Model response does not contain a JSON object or array")


def parse_prediction_payload(raw_text: str, horizon_hours: int, base_price: float) -> tuple[str, list[PredictionPoint]]:
    payload = _extract_json_payload(raw_text)
    if isinstance(payload, list):
        summary = ""
        raw_points = payload
    else:
        summary = str(payload.get("summary") or payload.get("outlook") or "").strip()
        raw_points = payload.get("points") or payload.get("forecasts") or payload.get("hours")

    if not isinstance(raw_points, list):
        raise ValueError("Prediction payload does not contain a points array")

    required_hours = set(range(1, horizon_hours + 1))
    normalized_points: dict[int, PredictionPoint] = {}

    for item in raw_points:
        if not isinstance(item, dict):
            raise ValueError("Prediction point is not an object")

        hour_value = item.get("hour", item.get("forecast_hour", item.get("step")))
        if hour_value is None:
            raise ValueError("Prediction point is missing hour")

        hour = int(hour_value)
        if hour not in required_hours:
            raise ValueError(f"Prediction hour {hour} is outside requested horizon")
        if hour in normalized_points:
            raise ValueError(f"Prediction hour {hour} is duplicated")

        predicted_price = _coerce_float(item.get("price", item.get("predicted_price")))
        predicted_change_pct = _coerce_float(item.get("change_pct", item.get("changePercent", item.get("predicted_change_pct"))))

        if predicted_price is None and predicted_change_pct is None:
            raise ValueError(f"Prediction hour {hour} is missing both price and change_pct")

        if predicted_price is None:
            predicted_price = base_price * (1.0 + (predicted_change_pct or 0.0) / 100.0)
        if predicted_change_pct is None:
            predicted_change_pct = ((predicted_price - base_price) / base_price * 100.0) if base_price else 0.0

        if predicted_price <= 0:
            raise ValueError(f"Prediction hour {hour} has non-positive price")

        confidence = _coerce_float(item.get("confidence"))
        if confidence is not None:
            confidence = max(0.0, min(100.0, confidence))

        rationale = str(item.get("rationale") or item.get("reason") or item.get("note") or "").strip()
        normalized_points[hour] = PredictionPoint(
            hour=hour,
            predicted_price=round(predicted_price, 8),
            predicted_change_pct=round(predicted_change_pct, 4),
            confidence=round(confidence, 2) if confidence is not None else None,
            rationale=rationale[:400],
        )

    if set(normalized_points) != required_hours:
        missing = sorted(required_hours - set(normalized_points))
        raise ValueError(f"Prediction payload is missing hour(s): {missing}")

    ordered_points = [normalized_points[hour] for hour in sorted(normalized_points)]
    if not summary:
        fallback_parts = [point.rationale for point in ordered_points if point.rationale][:2]
        summary = " ".join(fallback_parts)[:500]
    return summary, ordered_points


def get_model_config(key: str) -> PredictionModelConfig:
    return MODEL_CONFIGS[key]


def _provider_group(config: PredictionModelConfig) -> str:
    if "bigmodel.cn" in config.base_url:
        return "glm"
    if "googleapis.com" in config.base_url:
        return "gemini"
    if "dashscope-intl.aliyuncs.com" in config.base_url:
        return "dashscope"
    return config.key


def _is_quota_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "quota" in message or "allocated quota exceeded" in message or "insufficient balance" in message


def _is_access_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "403" in message or "forbidden" in message or "permission" in message or "access denied" in message


def _is_auth_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "401" in message or "unauthorized" in message or "invalid api key" in message


def _is_rate_limit_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "429" in message or "rate limit" in message or "too many requests" in message


def _is_timeout_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "timed out" in message or "timeout" in message


def _is_missing_model_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "404" in message and ("not found" in message or "unsupported" in message)


def _build_runtime_candidates(config: PredictionModelConfig) -> list[RuntimeModelCandidate]:
    if config.key == "worker":
        candidates: list[RuntimeModelCandidate] = []
        ollama_primary = os.getenv("OLLAMA_WORKER_MODEL", "deepseek-v4-flash").strip()
        ollama_fallback = os.getenv("OLLAMA_WORKER_FALLBACK_MODEL", "").strip()
        for model_id in [ollama_primary, ollama_fallback]:
            if model_id:
                candidates.append(
                    RuntimeModelCandidate(
                        cache_key=f"worker:{model_id}",
                        model_id=model_id,
                        base_url=OLLAMA_BASE_URL,
                        api_key_env="OLLAMA_API_KEY",
                        timeout=config.timeout,
                    )
                )
        return candidates

    if config.key == "analyst":
        # Ollama primary — glm-5.1, fallback deepseek-v4-pro
        ollama_primary = os.getenv("OLLAMA_ANALYST_MODEL", "glm-5.1").strip()
        ollama_fallback = os.getenv("OLLAMA_ANALYST_FALLBACK_MODEL", "deepseek-v4-pro").strip()
        candidates = []
        for model_id in [ollama_primary, ollama_fallback]:
            if model_id:
                candidates.append(
                    RuntimeModelCandidate(
                        cache_key=f"analyst:{model_id}",
                        model_id=model_id,
                        base_url=OLLAMA_BASE_URL,
                        api_key_env="OLLAMA_API_KEY",
                        timeout=config.timeout,
                    )
                )
        return candidates

    if config.key == "premium":
        # Ollama primary — glm-5.2, fallback kimi-k2:1t
        ollama_primary = os.getenv("OLLAMA_PREMIUM_MODEL", "glm-5.2").strip()
        ollama_fallback = os.getenv("OLLAMA_PREMIUM_FALLBACK_MODEL", "kimi-k2:1t").strip()
        candidates = []
        for model_id in [ollama_primary, ollama_fallback]:
            if model_id:
                candidates.append(
                    RuntimeModelCandidate(
                        cache_key=f"premium:{model_id}",
                        model_id=model_id,
                        base_url=OLLAMA_BASE_URL,
                        api_key_env="OLLAMA_API_KEY",
                        timeout=config.timeout,
                    )
                )
        return candidates

    # Oracle — Ollama only: kimi-k2-thinking → deepseek-v4-flash
    ollama_primary = os.getenv("OLLAMA_ORACLE_MODEL", "kimi-k2-thinking").strip()
    ollama_fallback = os.getenv("OLLAMA_WORKER_MODEL", "deepseek-v4-flash").strip()
    candidates = []
    for model_id in [ollama_primary, ollama_fallback]:
        if model_id:
            candidates.append(
                RuntimeModelCandidate(
                    cache_key=f"minimax:{model_id}",
                    model_id=model_id,
                    base_url=OLLAMA_BASE_URL,
                    api_key_env="OLLAMA_API_KEY",
                    timeout=config.timeout,
                )
            )
    return candidates


def _candidate_is_available(candidate: RuntimeModelCandidate, strict_nvapi: bool = False) -> bool:
    api_key = os.getenv(candidate.api_key_env, "").strip()
    if not api_key:
        return False
    if strict_nvapi and candidate.api_key_env != "OLLAMA_API_KEY" and not api_key.startswith("nvapi-"):
        return False
    return True


def list_prediction_models() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in MODEL_ORDER:
        config = MODEL_CONFIGS[key]
        candidates = _build_runtime_candidates(config)
        available = False
        reason = ""

        if key == "minimax":
            # Oracle — Ollama only now, no NVIDIA NIM required
            available = any(_candidate_is_available(candidate) for candidate in candidates)
            if not available:
                reason = "OLLAMA_API_KEY is not set for the oracle chain"
        else:
            available = any(_candidate_is_available(candidate) for candidate in candidates)
            if not available:
                if key == "worker":
                    reason = "OLLAMA_API_KEY is not set for the worker chain"
                elif key == "analyst":
                    reason = "OLLAMA_API_KEY is not set for the analyst chain"
                elif key == "premium":
                    reason = "OLLAMA_API_KEY is not set for the strategist chain"
                else:
                    reason = f"{config.api_key_env} is not set"

        items.append(
            {
                "key": key,
                "name": config.name,
                "model_id": " -> ".join(candidate.model_id for candidate in candidates),
                "tier": config.tier,
                "available": available,
                "reason": reason,
            }
        )
    return items


def _build_client(candidate: RuntimeModelCandidate, strict_nvapi: bool = False) -> AsyncOpenAI | None:
    """Kept for list_prediction_models compatibility — actual calls use _ollama_chat."""
    if candidate.cache_key in _clients:
        return _clients[candidate.cache_key]

    api_key = os.getenv(candidate.api_key_env, "").strip()
    if not api_key:
        _clients[candidate.cache_key] = None
        return None

    _clients[candidate.cache_key] = AsyncOpenAI(
        api_key=api_key,
        base_url=candidate.base_url,
        timeout=candidate.timeout,
        max_retries=0,
    )
    return _clients[candidate.cache_key]


async def _ollama_chat(
    candidate: RuntimeModelCandidate,
    config: PredictionModelConfig,
    context_payload: dict[str, Any],
) -> str:
    """Call Ollama Cloud native /api/chat endpoint and return assistant content."""
    import httpx

    api_key = os.getenv(candidate.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"{candidate.api_key_env} is not set")

    base = candidate.base_url.rstrip("/").removesuffix("/v1")
    url = f"{base}/api/chat"

    payload = {
        "model": candidate.model_id,
        "messages": _build_prediction_messages(context_payload),
        "stream": False,
        "options": {
            "temperature": config.temperature,
            "num_predict": config.max_tokens,
        },
    }

    async with httpx.AsyncClient(timeout=candidate.timeout) as hc:
        resp = await hc.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    content = (data.get("message", {}).get("content", "") or "").strip()
    if not content:
        raise ValueError("Model returned empty content")
    return content


def _build_prediction_messages(context_payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": PREDICTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Build an hourly crypto forecast using this exact context.\n"
                "Return strict JSON matching the required schema.\n\n"
                f"{json.dumps(context_payload, ensure_ascii=False, indent=2)}"
            ),
        },
    ]


def _attempt_schedule(config: PredictionModelConfig) -> tuple[float, ...]:
    if config.key == "minimax":
        return (0.0,)
    return (0.0, *RETRY_BACKOFF_SECONDS)


async def generate_model_prediction(config: PredictionModelConfig, context_payload: dict[str, Any]) -> ModelPredictionResult:
    runtime_candidates = _build_runtime_candidates(config)
    strict_nvapi = False  # Ollama-only now, no NVIDIA NIM strict check
    chain_switch_enabled = config.key in {"worker", "analyst", "premium", "minimax"}

    if not any(
        _candidate_is_available(candidate, strict_nvapi=(strict_nvapi and candidate.api_key_env != "OLLAMA_API_KEY"))
        for candidate in runtime_candidates
    ):
        model_status = next(item for item in list_prediction_models() if item["key"] == config.key)
        return ModelPredictionResult(
            key=config.key,
            name=config.name,
            model_id=runtime_candidates[0].model_id if runtime_candidates else config.model_id,
            tier=config.tier,
            status="failed",
            error_message=model_status["reason"] or "Model is not configured",
        )

    last_error: Exception | None = None
    last_model_id = runtime_candidates[0].model_id if runtime_candidates else config.model_id

    for candidate in runtime_candidates:
        if not _candidate_is_available(candidate, strict_nvapi=strict_nvapi):
            continue

        attempt_schedule = _attempt_schedule(config)
        for attempt, backoff in enumerate(attempt_schedule, start=1):
            if backoff:
                await asyncio.sleep(backoff)

            try:
                last_model_id = candidate.model_id
                content = await _ollama_chat(candidate, config, context_payload)

                summary, points = parse_prediction_payload(
                    raw_text=content,
                    horizon_hours=int(context_payload["horizon_hours"]),
                    base_price=float(context_payload["base_price"]),
                )
                return ModelPredictionResult(
                    key=config.key,
                    name=config.name,
                    model_id=candidate.model_id,
                    tier=config.tier,
                    status="completed",
                    summary=summary,
                    points=points,
                )
            except Exception as exc:
                last_error = exc
                if chain_switch_enabled and (
                    _is_quota_error(exc)
                    or _is_access_error(exc)
                    or _is_auth_error(exc)
                    or _is_missing_model_error(exc)
                ):
                    log.warning(
                        "Prediction %s switching from %s to next candidate after terminal error: %s",
                        config.key,
                        candidate.model_id,
                        exc,
                    )
                    break
                if (_is_rate_limit_error(exc) or _is_timeout_error(exc)) and attempt < len(attempt_schedule):
                    log.warning(
                        "Prediction model %s attempt %s hit retryable error on %s: %s",
                        config.key,
                        attempt,
                        candidate.model_id,
                        exc,
                    )
                    continue
                if chain_switch_enabled and (_is_rate_limit_error(exc) or _is_timeout_error(exc)):
                    log.warning(
                        "Prediction %s switching from %s after retryable exhaustion: %s",
                        config.key,
                        candidate.model_id,
                        exc,
                    )
                    break
                log.warning("Prediction model %s failed on %s: %s", config.key, candidate.model_id, exc)
                break

    return ModelPredictionResult(
        key=config.key,
        name=config.name,
        model_id=last_model_id,
        tier=config.tier,
        status="failed",
        error_message=str(last_error) if last_error else "Prediction request failed",
    )


async def generate_prediction_bundle(
    context_payload: dict[str, Any],
    model_keys: list[str] | tuple[str, ...] | None = None,
) -> list[ModelPredictionResult]:
    results: list[ModelPredictionResult] = []
    previous_provider_group: str | None = None
    active_keys = list(model_keys or MODEL_ORDER)

    for key in active_keys:
        config = MODEL_CONFIGS[key]
        provider_group = _provider_group(config)
        if previous_provider_group is not None:
            cooldown = 0.8
            if previous_provider_group == provider_group:
                cooldown = PROVIDER_COOLDOWN_SECONDS.get(provider_group, cooldown)
            await asyncio.sleep(cooldown)
        results.append(await generate_model_prediction(config, context_payload))
        previous_provider_group = provider_group

    return results

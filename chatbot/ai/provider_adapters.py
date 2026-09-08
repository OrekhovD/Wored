"""Provider adapters — the ONLY place where direct SDK/HTTP inference calls happen.

R04 spec: Direct SDK/HTTP inference calls are allowed ONLY in this file.
All other code must go through ProviderGateway.execute().

Two adapter types:
  - OllamaCloudAdapter: native /api/chat endpoint
  - OpenAICompatibleAdapter: OpenAI chat/completions protocol
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

from ai.contracts import (
    AttemptState,
    CostClass,
    FinishReason,
    NormalizedRequest,
    NormalizedResponse,
    UsageSource,
)

log = logging.getLogger(__name__)


class AdapterError(Exception):
    """Base for adapter-level errors that should trigger next candidate."""
    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class TransportError(AdapterError):
    """Connection reset, timeout, DNS failure."""
    pass


class InvalidResponseError(AdapterError):
    """Empty/whitespace final, NaN, truncated JSON, malformed response."""
    pass


def _is_retryable_status(code: int) -> bool:
    """HTTP status codes that merit a retry per spec: 408, 429, 5xx."""
    return code in (408, 429) or (500 <= code <= 599)


def _is_fatal_status(code: int) -> bool:
    """400/401/403/404/410 → skip candidate immediately, no retry."""
    return code in (400, 401, 403, 404, 410)


def _validate_final_text(text: str | None, finish_reason: str | None,
                          output_schema: dict | None = None,
                          allowed_tools: list[str] | None = None,
                          tool_calls: list[dict] | None = None) -> FinishReason:
    """Validate response text per R04 spec.

    Returns FinishReason.STOP on valid content, or the appropriate error finish reason.
    Raises InvalidResponseError for content that should invalidate this candidate.
    """
    # Empty/whitespace final without valid tool calls → invalid
    if not text or not text.strip():
        has_valid_tools = (
            tool_calls
            and allowed_tools
            and any(tc.get("function", {}).get("name") in allowed_tools for tc in tool_calls)
        )
        if not has_valid_tools:
            raise InvalidResponseError("Empty final text without valid tool calls")

    # NaN in text → invalid
    if text and "nan" in text.lower():
        # Check if it's an actual NaN value in JSON context
        try:
            parsed = json.loads(text)
            text_check = json.dumps(parsed)
            if _contains_nan(text_check):
                raise InvalidResponseError("Response contains NaN")
        except (json.JSONDecodeError, TypeError):
            if math.isnan(0.0) and "nan" in text.lower():
                # Loose check — if it looks like a standalone NaN value
                import re
                if re.search(r'\bNaN\b', text):
                    raise InvalidResponseError("Response contains NaN value")

    # Truncation/length finish → invalid for tasks needing complete output
    if finish_reason == "length":
        raise InvalidResponseError("Response truncated (finish_reason=length)")

    # Malformed JSON when schema was requested
    if output_schema and text:
        try:
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise InvalidResponseError("Expected JSON object, got non-object")
        except json.JSONDecodeError:
            raise InvalidResponseError("Malformed JSON in response")

    return FinishReason.STOP


def _contains_nan(obj: Any) -> bool:
    """Recursively check for NaN values in parsed JSON."""
    if isinstance(obj, float) and math.isnan(obj):
        return True
    if isinstance(obj, dict):
        return any(_contains_nan(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_nan(v) for v in obj)
    return False


@dataclass
class RawProviderResponse:
    """Raw response from provider before normalization."""
    final_text: str | None = None
    parsed_json: dict | None = None
    finish_reason_raw: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    tool_calls: list[dict] | None = None
    tool_tokens: int | None = None
    latency_ms: int = 0


class OllamaCloudAdapter:
    """Adapter for Ollama Cloud native /api/chat endpoint.

    Extracts:
    - final from message.content
    - input from prompt_eval_count
    - output from eval_count
    - finish from done_reason
    """

    def __init__(self, base_url: str | None = None, api_key_env: str = "OLLAMA_CLOUD_API_KEY"):
        self.base_url = (base_url or os.getenv("OLLAMA_CLOUD_BASE_URL", "https://ollama.com")).rstrip("/")
        self.api_key_env = api_key_env

    async def call(self, model_id: str, messages: list[dict],
                   max_tokens: int = 4096,
                   timeout_seconds: float = 120.0,
                   **kwargs) -> RawProviderResponse:
        """Call Ollama Cloud /api/chat endpoint."""
        api_key = os.getenv(self.api_key_env, "").strip()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model_id,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    headers=headers,
                )
            except httpx.TimeoutException as exc:
                raise TransportError(f"Ollama timeout: {exc}", retryable=True) from exc
            except httpx.ConnectError as exc:
                raise TransportError(f"Ollama connection error: {exc}", retryable=True) from exc
            except httpx.HTTPError as exc:
                raise TransportError(f"Ollama HTTP error: {exc}", retryable=True) from exc

        latency_ms = int((time.monotonic() - start) * 1000)

        if resp.status_code >= 400:
            if _is_fatal_status(resp.status_code):
                raise AdapterError(
                    f"Ollama {resp.status_code}: {resp.text[:200]}",
                    retryable=False,
                    status_code=resp.status_code,
                )
            if _is_retryable_status(resp.status_code):
                raise TransportError(
                    f"Ollama {resp.status_code}: {resp.text[:200]}",
                    retryable=True,
                    status_code=resp.status_code,
                )
            raise AdapterError(f"Ollama {resp.status_code}", retryable=False, status_code=resp.status_code)

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise InvalidResponseError(f"Ollama non-JSON response: {exc}") from exc

        # Extract from Ollama native format
        message = data.get("message", {})
        final_text = message.get("content", "") if isinstance(message, dict) else ""

        done_reason = data.get("done_reason", "stop")
        # Ollama maps: "stop" → stop, "length" → length
        finish_reason_raw = done_reason if done_reason in ("stop", "length") else "stop"

        input_tokens = data.get("prompt_eval_count")
        output_tokens = data.get("eval_count")

        return RawProviderResponse(
            final_text=final_text or None,
            finish_reason_raw=finish_reason_raw,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )


class OpenAICompatibleAdapter:
    """Adapter for OpenAI-compatible /v1/chat/completions endpoint.

    Extracts:
    - choices[0].message.content
    - finish_reason
    - usage and available details
    reasoning/thinking do NOT replace final.
    """

    def __init__(self, base_url: str, api_key_env: str, model_id_override: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.model_id_override = model_id_override

    async def call(self, model_id: str, messages: list[dict],
                   max_tokens: int = 4096,
                   timeout_seconds: float = 120.0,
                   output_schema: dict | None = None,
                   allowed_tools: list[str] | None = None,
                   tool_definitions: list[dict] | None = None,
                   **kwargs) -> RawProviderResponse:
        """Call OpenAI-compatible /v1/chat/completions."""
        api_key = os.getenv(self.api_key_env, "").strip()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        effective_model = self.model_id_override or model_id

        payload: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "max_tokens": max_tokens,
        }

        # Only include tools if explicitly allowed by the request
        if allowed_tools and tool_definitions:
            payload["tools"] = tool_definitions
            payload["tool_choice"] = "auto"

        # Request JSON output if schema provided
        if output_schema:
            payload["response_format"] = {"type": "json_object"}

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            try:
                resp = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
            except httpx.TimeoutException as exc:
                raise TransportError(f"OpenAI timeout: {exc}", retryable=True) from exc
            except httpx.ConnectError as exc:
                raise TransportError(f"OpenAI connection error: {exc}", retryable=True) from exc
            except httpx.HTTPError as exc:
                raise TransportError(f"OpenAI HTTP error: {exc}", retryable=True) from exc

        latency_ms = int((time.monotonic() - start) * 1000)

        if resp.status_code >= 400:
            if _is_fatal_status(resp.status_code):
                raise AdapterError(
                    f"OpenAI {resp.status_code}: {resp.text[:200]}",
                    retryable=False,
                    status_code=resp.status_code,
                )
            if _is_retryable_status(resp.status_code):
                # Check Retry-After header
                retry_after = resp.headers.get("Retry-After")
                raise TransportError(
                    f"OpenAI {resp.status_code}: {resp.text[:200]}",
                    retryable=True,
                    status_code=resp.status_code,
                )
            raise AdapterError(f"OpenAI {resp.status_code}", retryable=False, status_code=resp.status_code)

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise InvalidResponseError(f"OpenAI non-JSON response: {exc}") from exc

        choices = data.get("choices", [])
        if not choices:
            raise InvalidResponseError("OpenAI response has no choices")

        choice = choices[0]
        message = choice.get("message", {})

        # final_text is message.content, NOT reasoning/thinking
        final_text = message.get("content", "")
        if final_text is None:
            final_text = ""

        finish_reason_raw = choice.get("finish_reason", "stop")

        # Token usage
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens")
        output_tokens = usage.get("completion_tokens")

        # Detailed token breakdown (OpenAI-compatible)
        details = usage.get("prompt_tokens_details", {})
        cached_tokens = details.get("cached_tokens") if isinstance(details, dict) else None
        completion_details = usage.get("completion_tokens_details", {})
        reasoning_tokens = completion_details.get("reasoning_tokens") if isinstance(completion_details, dict) else None

        # Tool calls
        tool_calls_raw = message.get("tool_calls")
        tool_calls = None
        if tool_calls_raw:
            tool_calls = []
            for tc in tool_calls_raw:
                func = tc.get("function", {})
                tool_calls.append({
                    "id": tc.get("id", ""),
                    "type": tc.get("type", "function"),
                    "function": {
                        "name": func.get("name", ""),
                        "arguments": func.get("arguments", ""),
                    },
                })

        return RawProviderResponse(
            final_text=final_text or None,
            finish_reason_raw=finish_reason_raw,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            reasoning_tokens=reasoning_tokens,
            tool_calls=tool_calls,
            latency_ms=latency_ms,
        )


def normalize_response(
    raw: RawProviderResponse,
    provider: str,
    model_id: str,
    requested_model: str,
    request: NormalizedRequest,
    attempt_id: str,
    request_id: str,
) -> NormalizedResponse:
    """Convert raw provider response to NormalizedResponse, with validation.

    Raises InvalidResponseError for responses that should invalidate this candidate.
    """
    # Map raw finish reason
    finish_map = {
        "stop": FinishReason.STOP,
        "length": FinishReason.LENGTH,
        "tool_calls": FinishReason.TOOL_CALLS,
        "content_filter": FinishReason.CONTENT_FILTER,
    }
    finish_reason = finish_map.get(raw.finish_reason_raw or "stop", FinishReason.STOP)

    # Validate final_text — raises InvalidResponseError on bad content
    try:
        validated_finish = _validate_final_text(
            raw.final_text,
            raw.finish_reason_raw,
            output_schema=request.output_schema,
            allowed_tools=request.allowed_tools,
            tool_calls=raw.tool_calls,
        )
        # If validation passes but finish was length, that's still truncation
        if finish_reason == FinishReason.LENGTH:
            validated_finish = FinishReason.LENGTH
    except InvalidResponseError:
        # Re-raise with context — the gateway will try next candidate
        raise

    # Determine usage source
    if raw.input_tokens is not None and raw.output_tokens is not None:
        usage_source = UsageSource.ACTUAL
    elif raw.input_tokens is not None or raw.output_tokens is not None:
        usage_source = UsageSource.ESTIMATED
    else:
        usage_source = UsageSource.UNKNOWN

    # Parse JSON if schema was requested
    parsed_json = None
    if request.output_schema and raw.final_text:
        try:
            parsed_json = json.loads(raw.final_text)
        except json.JSONDecodeError:
            pass  # Will have been caught by validation above

    return NormalizedResponse(
        final_text=raw.final_text or "",
        parsed_json=parsed_json,
        actual_provider=provider,
        actual_model=model_id,
        requested_model=requested_model,
        finish_reason=validated_finish,
        input_tokens=raw.input_tokens,
        output_tokens=raw.output_tokens,
        cached_tokens=raw.cached_tokens,
        reasoning_tokens=raw.reasoning_tokens,
        tool_tokens=raw.tool_tokens,
        usage_source=usage_source,
        latency_ms=raw.latency_ms,
        tool_calls=raw.tool_calls,
        attempt_id=attempt_id,
        request_id=request_id,
    )
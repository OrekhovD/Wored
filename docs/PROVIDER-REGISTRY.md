# Provider Registry — WORED

## Registry Format

```json
{
  "registry_version": 1,
  "verified_at": "2026-09-08T00:00:00Z",
  "models": [
    {
      "provider": "ollama-cloud",
      "model_id": "glm-5.2",
      "endpoint_type": "native",
      "enabled": true,
      "cost_class": "free",
      "capabilities": {"tools": false, "thinking": true, "json_schema": true, "vision": false},
      "context_tokens": 131072,
      "max_output_tokens": 8192,
      "pricing": {"currency": "USD", "input_per_million": 0, "output_per_million": 0},
      "validation_gate_id": null
    }
  ]
}
```

## Active Models

| Provider | Model | Endpoint | Cost Class | Thinking | JSON Schema |
|---------|-------|----------|------------|----------|-------------|
| ollama-cloud | glm-5.2 | native | free | yes | yes |
| ollama-cloud | glm-5.1 | native | free | yes | yes |
| ollama-cloud | deepseek-v4-pro | openai | free | yes | yes |
| ollama-cloud | deepseek-v4-flash | openai | free | yes | no |
| ollama-cloud | minimax-m3 | openai | free | no | yes |
| ollama-cloud | kimi-k2.6 | native | included | yes | yes |
| ollama-cloud | kimi-k2:1t | native | included | yes | yes |

### Routing Mode Defaults

- `free_only` → only cost_class=free models
- `balanced` → free first, then included (if LLM_PAID_ENABLED=true)
- `premium` → all models (requires LLM_PAID_ENABLED=true + valid gate)

### Key Notes

- Reasoning models (glm-5.x, kimi-k2.x, deepseek-v4) return empty `content` field via OpenAI SDK `/v1/chat/completions`; must use native `/api/chat` endpoint
- `minimax-m3` is the only model that returns structured JSON in `content` on both endpoints
- `included` is NOT the same as `free` — included models have usage limits
- Unknown capability = `false` (conservative default)
- Local Ollama JSON Schema support does NOT carry over to Cloud without verification

## Validation Gate

A model passes the gate when:
- ≥100 completed forecast cases on identical immutable snapshots
- Chronological holdout last 30%
- Transport/schema success ≥95%
- Median error improvement over no-change baseline ≥5%
- p95 latency ≤180 seconds
- Zero policy violations

Gate is valid for 7 days and tied to model/registry/dataset/policy hash.
Insufficient data (<30 cases) → `insufficient_data`, no rating built.
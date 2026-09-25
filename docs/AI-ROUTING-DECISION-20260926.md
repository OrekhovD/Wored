# ADR: AI Routing Decision (2026-09-26)

- **Status**: Accepted
- **Date**: 2026-09-26
- **Deciders**: project owner; engineering (recorded against actual code)
- **Supersedes**: earlier "free-first gateway routing" assumptions spread across
  `README.md`, `docs/04-configuration.md`, `docs/ENV-REGISTRY.md` and
  `docs/PROVIDER-REGISTRY.md`.

## Context

The project originally aspired to a **free-first LLM gateway**: route inference to
the cheapest available model, with metered/paid models only behind an explicit
budget gate. That subsystem (`provider_gateway`, `provider_adapters`,
`usage_ledger`, `budget_policy`, self-learn `reflector` / `strategy_learner`, the
`provider_registry.json` registry, and providers OmniRoute / NVIDIA NIM /
TokenRouter / DashScope-Qwen / minimax-oracle) was built and tested.

In practice the free tier proved unreliable for the product's real goal. NVIDIA NIM
returned a permanent `410 Gone` (24/24 failures, documented in
`free_routing_archive/README.md`), free-model JSON/schema behaviour was
inconsistent, and reasoning models returned empty `content` via the OpenAI
compatible endpoint. Maintaining a budget gateway to serve flaky free models added
complexity without serving the actual user need.

## Decision

The **active goal is a local-first trading assistant** whose AI inference resolves:

1. **Primary** — Ollama Cloud Pro (`:cloud` suffix), OpenAI-compatible
   `https://ollama.com/v1`. Model set (owner decision 2026-09-25):
   - premium / analyst → `glm-5.3:cloud`
   - worker → `deepseek-v4.1-flash:cloud`
   - oracle (WebUI only) → `glm-5.3-flash:cloud`, fallbacks `gemma4:31b:cloud` / `deepseek-v4.1-flash:cloud`
2. **Failover** — workstation-local **Bonsai-27B**
   (`bonsai-27b:lmstudio-q1`, `provider="local_ollama"`, `http://127.0.0.1:8088/v1`),
   free and unlimited, sitting behind every cloud tier.

The two active entry points define chains with **different env names and different
candidate orders** (accepted as-is, not unified):

- Chatbot — `chatbot/ai/models.py`: `OLLAMA_CLOUD_API_KEY` / `OLLAMA_CLOUD_BASE_URL`,
  `OLLAMA_CHATBOT_{WORKER,ANALYST,PREMIUM}_MODEL`, tier chains
  `*_ollama → *_bonsai`.
- WebUI forecast — `webui/prediction_engine.py`: `OLLAMA_API_KEY` / `OLLAMA_BASE_URL`,
  `OLLAMA_{WORKER,ANALYST,PREMIUM,ORACLE}_MODEL` (+ `*_FALLBACK_MODEL`),
  `LOCAL_LLM_ROLES` to let a local candidate lead a role.

The entire free-first gateway subsystem is **frozen** to top-level
`free_routing_archive/`: it is outside the WORED runtime, not collected by pytest
(`norecursedirs = free_routing_archive`), and its keys (`LLM_ROUTING_MODE`,
`LLM_PAID_ENABLED`, `LLM_REGISTRY_PATH`, `DASHSCOPE_API_KEY`, `GLM_API_KEY`,
`GOOGLE_API_KEY`, `MINIMAX_API_KEY`, `NVIDIA_NIM_ENABLED`) are **inert / no-op**.

## Consequences

- **Honest mismatch with the original goal.** WORED is no longer "cheapest free
  model first". It is "Pro cloud quality with a free local safety net". Anyone
  reading old docs must treat free-gateway claims as archived, not current.
- `record_usage()` exists in `chatbot/storage/postgres_client.py` but is **not**
  called from the active AI path, and `check_quota` lives only in the archived
  `quota.py`. Token accounting for the active path is therefore **not** wired —
  this is a product consequence of freezing the gateway, not a bug being fixed here.
- Docs now separate **confirmed** behaviour (code-backed chains) from
  **unverified** claims (HTTP 200 is not authenticated acceptance — see
  `docs/KNOWN_LIMITS.md` #15b).

## Review triggers

Revisit this ADR if any of the following becomes true:

1. A reliable free/metered tier is required again for cost reasons **and** passes
   the validation gate (`docs/PROVIDER-REGISTRY.md`: ≥100 completed cases, ≥95%
   transport/schema success, median error improvement ≥5%, p95 ≤180s).
2. The local Bonsai failover can no longer run on the target hardware, removing the
   free safety net and re-justifying a multi-provider gateway.
3. The chatbot ↔ WebUI env-name divergence starts causing real operational errors
   (then unify the two configs deliberately).
4. Token accounting becomes a hard product requirement — then wire `record_usage()`
   into the active path rather than reviving the archived ledger.

## References

- `chatbot/ai/models.py`, `webui/prediction_engine.py`
- `AGENTS.md` (active chains note), `free_routing_archive/README.md` (revival + NVIDIA 410 history)
- `README.md`, `docs/04-configuration.md`, `docs/ENV-REGISTRY.md`, `docs/PROVIDER-REGISTRY.md`, `docs/KNOWN_LIMITS.md`

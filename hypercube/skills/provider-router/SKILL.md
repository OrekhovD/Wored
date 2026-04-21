---
name: provider-router
description: Design and maintain multi-provider AI routing for gateway-style systems. Use when Codex needs to normalize provider capabilities, define model selection policy, build fallback chains, explain route decisions, or add a new provider without coupling core logic to one SDK.
---

# Provider Router

Implement provider-agnostic routing for AI requests. Keep provider metadata, policy evaluation, fallback order, and route decision traces separate from bot handlers, token accounting, and storage concerns.

## Workflow

1. Inventory provider capabilities.
   Capture transport type, model list, context limits, pricing tier, known reliability notes, tool support, and quota constraints in a registry.
2. Normalize routing inputs.
   Convert user mode, task type, quota state, latency tolerance, and provider health into a stable internal decision shape.
3. Build candidate chains.
   Produce ordered candidates for `free-only`, `balanced`, and `premium` modes. Prefer free-tier models first unless policy explicitly allows paid execution.
4. Apply failure rules.
   Move to the next candidate on timeout, transport failure, quota denial, malformed output, policy violation, or invalid model capability match.
5. Emit route evidence.
   Persist reason codes for provider inclusion, exclusion, fallback, and final selection so the decision is auditable.

## Required Outputs

- Routing matrix by mode and task type
- Provider capability table
- Fallback policy with ordered escalation rules
- Route decision examples with reason codes
- Provider onboarding notes showing adapter-plus-config integration only

## Rules

- Never bind routing logic to one provider SDK or one response schema.
- Prefer config-driven routing tables over hardcoded branches.
- Keep routing separate from accounting, quota persistence, and Telegram handlers.
- Fail closed when no allowed model exists.
- Log route decision reason codes for selection, exclusion, and fallback.
- Treat provider health and quota state as first-class routing inputs.

## Route Decision Pattern

When documenting or implementing routing, produce decisions in this shape:

- Request summary
- Active mode and policy
- Eligible providers
- Excluded providers with reason codes
- Ordered execution chain
- Final provider and model
- Fallback path, if used
- Persistence fields required for audit

## Done When

- A new provider can be introduced with adapter and config changes only.
- Fallback order is explicit, deterministic, and test-covered.
- Free-tier-first behavior is preserved unless policy overrides it.
- Every final route can be explained from persisted decision data alone.

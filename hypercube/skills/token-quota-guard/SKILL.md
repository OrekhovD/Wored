---
name: token-quota-guard
description: Implement unified token accounting and quota enforcement for multi-provider AI systems. Use when Codex needs to define usage schemas, persist request metrics, estimate cost from partial provider usage data, enforce provider or model quotas, or gate premium models behind validation rules.
---

# Token Quota Guard

Implement durable accounting and quota controls outside the Telegram layer. Treat usage, latency, cost estimates, and quota state as auditable data that can drive routing, admin reporting, and premium unlock policy.

## Workflow

1. Define the usage record.
   Persist request ID, provider, model, input tokens, output tokens, cached or reasoning tokens when available, latency, success flag, error class, and cost estimate.
2. Normalize partial provider usage.
   When a provider omits fields, estimate conservatively and mark uncertain fields explicitly.
3. Apply policy scopes.
   Enforce quota at provider, model, user, and time-window levels where configured.
4. Separate stop levels.
   Distinguish soft warning, routing downgrade, and hard stop conditions.
5. Expose audit surfaces.
   Support admin reports by day, week, month, provider, and model.

## Required Outputs

- Usage schema
- Accounting service contract
- Quota policy examples
- Admin commands or reports for usage and limits
- Provider token-semantics notes with uncertainty markers where needed

## Rules

- Never mix accounting logic into Telegram handlers.
- Always persist request ID, provider, model, latency, success, and token usage.
- Distinguish hard quota stop from soft warning and routing downgrade.
- Mark uncertain token data explicitly instead of pretending the provider returned it.
- Keep premium unlock behind a validation gate plus config flag.
- Make quota decisions reproducible from stored records and active policy.

## Reporting Pattern

When producing accounting outputs, include:

- Time window
- Total requests
- Success and failure counts
- Token totals by class
- Estimated cost totals
- Quota consumed versus quota allowed
- Premium-lock status and reason

## Done When

- Usage can be audited by day, week, month, provider, and model.
- Quota exhaustion and premium lock states are explicit and testable.
- Routing can consume accounting outputs without duplicating logic.
- Missing provider usage fields no longer break reporting or policy enforcement.

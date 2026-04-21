---
name: docs-final-quality
description: Produce deterministic implementation and operations documentation for the AI gateway. Use when Codex needs to write or synchronize setup guides, architecture docs, environment-variable registries, testing guides, troubleshooting steps, runbooks, or acceptance checklists that weaker models and junior developers can execute safely.
---

# Docs Final Quality

Write docs that remove guesswork. Make every operational procedure executable, every assumption explicit, and every failure mode paired with validation, fix, and rollback guidance.

## Workflow

1. Anchor docs to code.
   Read the current implementation, commands, ports, env vars, and test surfaces before editing prose.
2. Document the exact procedure.
   Provide prerequisites, copy-paste commands, expected output, validation, failure modes, fixes, and rollback where relevant.
3. Keep cross-file consistency.
   Ensure README, architecture, env-var docs, testing guides, and runbooks describe the same behavior.
4. Eliminate ambiguity.
   Replace vague phrases with concrete values, paths, flags, and commands.
5. Update docs after behavior changes.
   Treat stale documentation as a defect.

## Mandatory Style

- Explicit over elegant
- Deterministic over concise
- Full file contents for critical artifacts
- No missing assumptions
- Every procedure has Verify, Fail, Fix, and Rollback guidance where applicable

## Required Deliverables

- `README.md`
- Architecture documentation
- Local setup documentation
- Environment-variable registry
- Routing and quota docs
- Testing guide
- Troubleshooting guide
- Operations runbook
- Weekly refresh protocol
- Acceptance checklist

## Rules

- Never rely on implied steps.
- Never hide dependencies, ports, paths, or file names.
- Never leave commands as pseudo-code.
- Define every config key that appears in docs.
- State what passing tests or successful startup actually look like.
- Keep docs synchronized with code after each meaningful change.

## Done When

- Another model can continue implementation from docs only.
- A junior developer can run, validate, and debug the project without guessing.
- Documentation changes track behavior changes instead of lagging behind them.

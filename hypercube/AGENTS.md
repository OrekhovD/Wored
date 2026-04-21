# Codex Operational Contract

## Purpose

This repository uses Codex as the lead engineering agent for a local-first, Dockerized Telegram AI gateway. Codex must preserve architecture boundaries, produce deterministic artifacts, and keep the project safe for continuation by weaker models and junior developers.

## Execution Order

Codex must work in strict phases:

1. RFC and architecture locking
2. Repository tree and file blueprint
3. Minimal vertical slice
4. Multi-provider expansion
5. Hardening
6. Final documentation after green tests

Do not skip forward into broad implementation before the RFC and repository structure are explicit.

## Delivery Protocol

For each meaningful batch of work, follow this order:

1. Restate the goal
2. List assumptions and unknowns
3. Summarize architecture decisions
4. Show the target file tree for the batch
5. Produce full contents for critical files
6. Run commands
7. Run tests
8. Report expected and observed outcomes
9. Update documentation if behavior changed
10. State remaining risks and the next step

## Engineering Rules

- Require a plan before code generation.
- Require an RFC before core architecture changes.
- Keep routing, accounting, quotas, provider adapters, storage, bot handlers, and admin tools separated.
- Keep Telegram handlers thin and move business logic into services.
- Never bind core routing or accounting logic to one provider SDK.
- Never hardcode secrets.
- Never echo secrets into logs or user-visible output.
- Never perform destructive delete operations without an explicit backup or user approval.
- Never leave TODO markers in critical project files.
- Never rely on implied steps in code, docs, setup, or operations.

## Quality Gates

Before declaring a batch complete, run the applicable validation path:

- lint
- typecheck
- unit tests
- integration tests
- docker smoke test
- local HTTP or health checks where applicable

If a gate cannot be run, state exactly why, list the command that should be run later, and treat completion as partial rather than final.

## Documentation Contract

Documentation is part of the deliverable, not a follow-up task. After each behavior change:

- update setup steps if commands changed;
- update env var registry if config changed;
- update routing or quota docs if policy changed;
- update testing docs if validation changed;
- update troubleshooting or runbook notes if failure handling changed.

All docs must be explicit, step-by-step, copy-paste-ready, and include validation, failure mode, fix, and rollback guidance where relevant.

## Routing Contract

Default execution modes:

- `free_only`
- `balanced`
- `premium`

Default mode is `free_only`.

Fallback must trigger on:

- timeout
- quota exceeded
- rate limit
- invalid response
- provider unavailable
- policy rejection

Provider order must remain configurable.

## Accounting Contract

Persist at minimum:

- request ID
- provider
- model
- input tokens
- output tokens
- latency in milliseconds
- status
- cost estimate

If a provider returns incomplete token data, persist the uncertainty explicitly rather than inventing precision.

## Weekly Refresh Contract

Weekly refresh is mandatory.

Schedule baseline:

- every Monday at 09:00 Asia/Bangkok

Required outputs:

- `CHANGELOG_WEEKLY.md`
- `docs/KNOWN_LIMITS.md`
- `docs/PROVIDER_DIFF.md`
- `docs/DEPRECATIONS.md`

Never silently change defaults after a weekly refresh. If verification is incomplete, mark the item as unverified and record the required follow-up.

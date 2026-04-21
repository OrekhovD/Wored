---
name: local-docker-runtime
description: Guarantee reproducible local deployment for the AI gateway with Docker Compose. Use when Codex needs to define containers, startup order, healthchecks, persistent volumes, environment files, helper scripts, or one-command local recovery for personal-PC deployment.
---

# Local Docker Runtime

Design a deterministic local runtime that starts cleanly on a personal machine and can later migrate to a VPS with minimal change. Keep container purpose, ports, env vars, persistence, and healthchecks explicit.

## Workflow

1. Define service boundaries.
   Separate bot, app API, storage, and optional supporting services into clear containers.
2. Specify deterministic startup.
   Use healthchecks and dependency order so the bot does not start against an unready backend.
3. Persist the right data.
   Mount SQLite data, logs, and generated reports into named volumes or explicit host paths.
4. Provide operator helpers.
   Include `.env.example`, `Makefile`, `scripts/doctor.*`, and `scripts/smoke_test.*` when those artifacts exist in the project.
5. Document recovery.
   Show exact commands for first boot, restart, teardown, and state-preserving recovery.

## Required Outputs

- `docker-compose.yml`
- `.env.example`
- `Makefile`
- `scripts/doctor.*`
- `scripts/smoke_test.*`
- Local setup, validation, and recovery documentation

## Rules

- Prefer one-command startup where possible.
- Do not rely on undocumented external dependencies.
- Document every container's purpose, ports, env vars, volumes, and healthcheck.
- Prefer simple base images and deterministic startup order.
- Keep local persistence enabled by default.
- Make teardown and rebuild commands explicit and reversible.

## Runtime Checklist

For each service, capture:

- Image or build target
- Command and entrypoint
- Environment variables
- Bound ports
- Volume mounts
- Healthcheck command and timing
- Dependency conditions
- Expected steady-state logs

## Done When

- A clean machine can start the stack from docs only.
- Healthchecks prove the bot and backend are ready, not merely started.
- Persistent data survives routine restarts.
- Recovery and smoke-test commands confirm the runtime is healthy.

---
name: telegram-control-plane
description: Implement Telegram as the control interface for a local-first AI gateway. Use when Codex needs to define bot commands, keep handlers thin, separate admin and user capabilities, normalize inbound messages, or return concise user replies with richer admin diagnostics.
---

# Telegram Control Plane

Build Telegram handlers as transport adapters, not business-logic containers. Route commands into services for execution, accounting, quota checks, provider status, and admin diagnostics.

## Workflow

1. Normalize inbound updates.
   Extract chat ID, user ID, command, arguments, reply context, and correlation metadata into a stable internal shape.
2. Authorize early.
   Enforce admin allowlists before dangerous or diagnostic commands execute.
3. Dispatch to services.
   Keep handlers thin and pass work to routing, accounting, quota, and health services.
4. Shape outbound responses.
   Return concise user-facing messages and richer operator detail only where policy allows it.
5. Handle failure safely.
   Truncate long outputs, hide secrets, and summarize provider or policy errors without leaking credentials.

## Required Commands

- `/start`
- `/help`
- `/ask`
- `/mode`
- `/providers`
- `/usage`
- `/quota`
- `/health`
- `/reload`
- `/admin_stats`

## Rules

- Keep handlers thin.
- Push business logic into services.
- Return concise user responses and richer admin diagnostics only for authorized admins.
- Protect admin commands with explicit allowlists.
- Sanitize logs and outbound error messages.
- Support long-response handling with chunking or file-based fallback where needed.

## Command Design Pattern

For each command, specify:

- Purpose
- Caller type: user or admin
- Required inputs
- Service dependency
- Success response shape
- Failure response shape
- Audit fields written

## Done When

- Telegram remains a UI and control surface, not a policy engine.
- User and admin command boundaries are explicit and enforceable.
- Execution status, usage, quota, and health flows are reachable without leaking internal secrets.
- Long outputs and provider failures degrade predictably.

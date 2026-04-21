---
name: weekly-refresh
description: Maintain a weekly refresh workflow for provider, model, Telegram, and runtime knowledge. Use when Codex needs to review provider docs and changelogs, compare model availability or pricing, update the provider registry, or publish explicit weekly change records and migration notes.
---

# Weekly Refresh

Run a predictable weekly review cycle for providers and runtime dependencies. Record what changed, what was verified, what remains uncertain, and what migration or testing follow-up is required.

## Workflow

1. Review upstream sources.
   Inspect provider docs, model lists, limits, pricing, token semantics, Telegram Bot API updates, and Docker or runtime security changes.
2. Compare against current registry.
   Detect additions, removals, deprecations, changed defaults, and broken assumptions.
3. Update machine-readable records.
   Refresh the provider registry and any tracked compatibility data.
4. Publish operator-facing deltas.
   Write the weekly changelog, known limits, deprecations, and provider-difference notes.
5. Flag uncertainty and breakage.
   Mark any unverified item explicitly and add migration notes or test checklists for breaking changes.

## Weekly Outputs

- `CHANGELOG_WEEKLY.md`
- `docs/KNOWN_LIMITS.md`
- `docs/DEPRECATIONS.md`
- `docs/PROVIDER_DIFF.md`

## Rules

- Never silently change defaults.
- Mark unverified information clearly.
- Attach migration notes to every breaking change.
- Add a test checklist for each new provider or model.
- Keep pricing, limits, and token semantics date-stamped.

## Refresh Record Pattern

For each reviewed provider or platform surface, capture:

- Source reviewed
- Review date
- Verified changes
- Unverified or uncertain items
- Impact assessment
- Required config, code, doc, or test follow-up

## Done When

- Weekly review has a visible audit trail.
- Breaking changes are documented before defaults shift.
- Registry, changelog, limits, and deprecations stay synchronized.

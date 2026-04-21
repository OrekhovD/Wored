---
name: test-orchestrator
description: Design and enforce a layered test strategy for the AI gateway. Use when Codex needs to define unit, integration, contract, smoke, or end-to-end tests; validate routing and fallback behavior; verify quota stops; or prove Docker startup and bot readiness without manual guessing.
---

# Test Orchestrator

Define the full validation path before calling implementation complete. Use tests to prove route selection, fallback execution, quota exhaustion, malformed-response handling, and runtime readiness.

## Workflow

1. Map features to layers.
   Place routing, quota, accounting, and normalization logic under unit tests; bot flows and storage wiring under integration tests; provider adapters under contract tests.
2. Create deterministic fixtures.
   Mock provider responses, quota states, latency, and failure classes so edge cases are reproducible.
3. Cover critical degradations.
   Test fallback, quota stop, timeout, retry, malformed payloads, and startup readiness.
4. Define operator checks.
   Add smoke and e2e commands that prove the Docker stack and bot are usable.
5. Record pass criteria.
   For every test command, state what success looks like and what failures imply.

## Required Outputs

- Test matrix
- Command list
- Pass and fail criteria
- Fixture strategy
- Regression checklist

## Rules

- No feature is complete without a test path.
- Tests must prove route selection, quota stop, and fallback behavior.
- Every bug fix should add or update a test.
- Prefer deterministic fixtures over live-provider dependence.
- Include Docker smoke coverage when runtime behavior changes.
- Report residual risks when a test surface remains unautomated.

## Minimum Matrix

- Unit tests for routing, quotas, accounting, and normalization
- Integration tests for Telegram command flow
- Provider adapter contract tests
- Fallback behavior tests
- Quota exhaustion tests
- Malformed provider response tests
- Timeout and retry tests
- Docker Compose smoke tests
- Localhost end-to-end happy path

## Done When

- The project can be validated from commands and fixtures alone.
- Failures identify whether the problem is routing, quota policy, provider integration, or runtime startup.
- New features and bug fixes extend the matrix instead of bypassing it.

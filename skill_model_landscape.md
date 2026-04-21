---
name: model-landscape
description: This skill maps and maintains an up-to-date landscape of LLM models and tools (Qwen, GLM-5, MiniMax, and other free options), including versions, capabilities, pricing, and agent/tool support, and should be used whenever the agent needs to choose or compare models for a project.
---

# Skill: Model & Tool Landscape Mapping

## Purpose
This Skill helps the agent systematically discover, describe, and keep up to date a registry of available LLM models and tools, with focus on Qwen 3.5/3.6+, GLM-5, MiniMax M2.7, and other free or open models.

## Responsibilities
- Collect fresh information about:
  - model versions, licenses, context windows, reasoning/agent/tool-use modes;
  - available endpoints and APIs, rate limits and pricing (when applicable);
  - support for MCP, function calling, code interpreters, and built-in agents.
- Build and maintain a “model & tools registry” with explicit versioning and check dates.
- Update the registry whenever new releases appear, especially after 2026-04-17.

## Inputs
- The human’s description of target providers and constraints.
- Existing project configs (if any).
- Responses from web/docs/API tools.

## Outputs
- An updated registry (table/Markdown) with fields like:
  - `name`, `provider`, `version`, `release_date`, `license`, `context_window`,
    `agentic_features`, `mcp_support`, `last_checked_at`, `notes`.
- Recommendations on which model to use for each task type.

## Workflow
1. Read the human’s list of providers (Qwen, GLM-series, MiniMax, others).  
2. For each provider:
   - Locate official documentation and release notes.
   - Cross-check at least one independent source (repo, blog, benchmark).
   - Extract key parameters and record them in the registry.
3. Update `last_checked_at` for every inspected model.
4. Mark uncertain or conflicting information for human review.

## Validation
- Randomly select one model from the registry and verify:
  - that recorded parameters match the official documentation;
  - that there are no major new releases or pricing/limit changes.
- If discrepancies are found, update the registry and notify the human.

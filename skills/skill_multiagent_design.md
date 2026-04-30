---
name: multi-agent-design
description: This skill designs multi-agent system architectures, defines agent roles, assigns backing models (Qwen, GLM-5, MiniMax, etc.), and specifies interaction protocols, and should be used whenever the agent needs to plan or refactor a multi-agent setup.
---

# Skill: Multi-Agent System Design

## Purpose
This Skill helps the agent design the architecture of a multi-agent system, assigning clear roles to agents and mapping those roles to specific models and tools.

## Responsibilities
- Define agent roles (planner, researcher, coder, evaluator, devops, docs, etc.).
- Assign a suitable backing model to each role based on strengths and constraints.
- Specify communication protocols and escalation rules between agents.

## Inputs
- Project goals and constraints (budget, latency, privacy requirements).
- The current model & tools registry (from model-landscape Skill).

## Outputs
- A structured description or diagram of the multi-agent architecture.
- A roles table with fields:
  - `agent_name`, `role`, `backing_model`, `skills`, `tools`, `input`, `output`.

## Workflow
1. Formalize project goals and typical use cases.  
2. Decompose work into roles/agents:
   - reasoning-heavy tasks,
   - code-heavy tasks,
   - IO-heavy tasks (web, DB, filesystem, crypto APIs, game backends).  
3. For each role, select a backing model using the model registry.  
4. Define interaction protocols:
   - message schema (`goal`, `context`, `artifacts`, `constraints`, `status`);
   - conditions when an agent must stop and escalate to the human.
5. Present the design to the human for confirmation or adjustment.

## Validation
- Run several end-to-end scenarios:
  - ensure that necessary context flows correctly between agents;
  - check for possible infinite loops or unnecessary agent hops;
  - verify that routing of tasks to models is reasonable.

---
name: eval-and-validation
description: This skill defines evaluation strategies, test sets, and validation procedures for models, agents, and pipelines, and should be used whenever the agent needs to measure or compare quality or robustness of a configuration.
---

# Skill: Evaluation & Validation

## Purpose
This Skill helps the agent design and run quality checks for models, agents, and the overall system.

## Responsibilities
- Define quality metrics (accuracy, latency, cost, robustness).
- Build test sets (realistic prompts, edge cases).
- Compare models and configurations on project-specific tasks.

## Inputs
- Current agent and model configuration.
- Business and quality requirements.

## Outputs
- A set of test scenarios and validation checklists.
- Reports summarizing results and recommendations.

## Workflow
1. Identify which aspects matter most (reasoning, code quality, freshness of information, etc.).  
2. Build a test set:
   - real user-like requests,
   - synthetic edge cases.  
3. Run scenarios across different models and agent setups.  
4. Record results and propose configuration changes.

## Validation
- Check that test cases reflect realistic workloads.  
- Re-run tests after each significant configuration change.

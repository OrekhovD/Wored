---
name: cli-and-devops
description: This skill integrates agents with CLI, Git, CI/CD, testing, and IDE workflows, and should be used whenever the agent needs to generate commands, repo structure, or deployment pipelines for the project.
---

# Skill: CLI & DevOps Integration

## Purpose
This Skill helps the agent connect the multi-agent system with CLI tools, Git workflows, CI/CD pipelines, testing frameworks, and IDE setups.

## Responsibilities
- Generate commands for installing and running services.
- Propose repository structure.
- Integrate agents with CI/CD (e.g., GitHub Actions, GitLab CI).

## Inputs
- Target stack (OS, package managers, CI system).
- Deployment requirements (local, cloud, hybrid).

## Outputs
- A set of CLI commands (`install`, `run`, `test`, `deploy`).
- A proposed repository layout (directories, configs, docs).

## Workflow
1. Clarify the target stack with the human.  
2. Generate a minimal command set for:
   - environment setup,
   - local service startup,
   - test execution.  
3. Propose a CI/CD config template consistent with the stack.

## Validation
- Prefer dry-run modes where possible.  
- Ensure that pipelines fail fast on errors and surface clear logs.

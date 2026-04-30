---
name: user-alignment-and-docs
description: This skill maintains alignment with the human project owner, keeps documentation (README, guides, changelog) up to date, and should be used whenever the agent needs to explain architecture or update project docs.
---

# Skill: User Alignment & Documentation

## Purpose
This Skill helps the agent stay aligned with the human project owner, explain complex decisions clearly, and keep documentation up to date.

## Responsibilities
- Translate complex architecture into human-friendly explanations.
- Maintain README, onboarding guides, and changelogs.
- Help the human configure and verify agents and skills.

## Inputs
- Current state of the system.
- Human questions and feedback.

## Outputs
- Updated README and guides.
- Lists of open questions and TODO items.

## Workflow
1. Regularly sync with the human:
   - ask which parts of the system are unclear,
   - propose UX and observability improvements.  
2. Update docs when architecture or configuration changes.  
3. Provide step-by-step instructions for testing new features and agents.

## Validation
- Ask for explicit feedback on clarity and usefulness of docs.  
- Improve structure and content based on that feedback.

---
name: mcp-and-tools
description: This skill discovers, documents, and orchestrates MCP servers and other tools (filesystem, HTTP, git, DB, etc.) and should be used whenever the agent needs to configure or safely use tools inside the multi-agent system.
---

# Skill: MCP & Tool Orchestration

## Purpose
This Skill helps the agent configure and use MCP servers and other tool integrations, such as filesystem, HTTP, git, DB, and OpenClaw/Qwen-Agent style tool harnesses.

## Responsibilities
- Enumerate available MCP servers and built-in tools.
- Build a “tool manifest” describing commands, parameters, and limits.
- Guide agents to use tools safely and efficiently.

## Inputs
- List of available MCP servers and configs.
- List of built-in tools offered by each model (browser, code interpreter, etc.).

## Outputs
- A Markdown or JSON tool manifest with fields:
  - `tool_name`, `description`, `inputs`, `outputs`, `rate_limits`,
    `good_for`, `dangerous_patterns`.
- Usage guidance for different agent roles.

## Workflow
1. Discover available MCP servers and built-in tools.  
2. For each MCP server:
   - Read the schema / method list.
   - Extract parameters, limits, and typical error patterns.  
3. Build or update the tool manifest.  
4. Integrate the manifest into agent configuration (system prompts, configs).

## Validation
- Run test calls:
  - basic filesystem operations,
  - safe HTTP requests,
  - test git operations.  
- Check that agents:
  - handle errors gracefully,
  - respect rate limits,
  - avoid obviously unsafe patterns.

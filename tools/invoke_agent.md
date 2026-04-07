# invoke_agent

## Purpose

Delegates a concrete task to a configured specialist agent.

## Availability

Available when the agent registry and LLM factory are configured and at least one specialist agent is registered.

## Configuration

Relevant config: `[orchestration]`, `[orchestration.main_agent]`, and agent definition frontmatter.

Important fields include `default_timeout_seconds`, `delegated_tool_call_policy`, `tool_ownership_mode`, and per-agent `tools_allow`, `tools_deny`, or `mcp_servers`.

## Interface

Inputs:

- `agent_name`: exact specialist name.
- `task`: concrete delegated task.
- `context`: optional supporting context.

The result includes delegated output, continuation signal, attachments, token usage where available, and tool-call policy metadata.

## Safety Notes

The specialist receives only tools allowed by agent policy. Delegated tool-use requirements are controlled by `delegated_tool_call_policy`.

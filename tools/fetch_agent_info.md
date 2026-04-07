# fetch_agent_info

## Purpose

Fetches details for a configured specialist agent.

## Availability

Available when the agent registry and LLM factory are configured and at least one specialist agent is registered.

## Configuration

Relevant config: `[orchestration]` and the configured agent definition files. Agent tool visibility is affected by `tool_ownership_mode`, main-agent policy, and per-agent `tools_allow`, `tools_deny`, or `mcp_servers`.

## Interface

Inputs:

- `agent_name`: exact specialist name from the available specialists list.

The result includes agent name, description, and system prompt when found.

## Safety Notes

This exposes specialist instructions to the orchestrating model. It does not invoke the specialist.

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from minibot.adapters.config.schema import Settings
from minibot.app.mcp_tool_name import extract_mcp_server, is_mcp_tool_name
from minibot.app.tool_policy_utils import matches_any, normalize_patterns, validate_allow_deny
from minibot.core.agents import AgentSpec
from minibot.llm.tools.base import ToolBinding

MODEL_OVERRIDE_KEYS = ("model_provider", "model", "reasoning_effort")

RESERVED_DELEGATION_TOOL_NAMES = {
    "fetch_agent_info",
    "spawn_task",
    "list_tasks",
    "get_task",
    "cancel_task",
}


def filter_tools_for_agent(tools: Sequence[ToolBinding], spec: AgentSpec) -> list[ToolBinding]:
    validate_allow_deny(spec.tools_allow, spec.tools_deny)

    allow_patterns = normalize_patterns(spec.tools_allow)
    deny_patterns = normalize_patterns(spec.tools_deny)
    allow_mode = bool(allow_patterns)
    deny_mode = bool(deny_patterns)
    mcp_servers = {item.strip() for item in spec.mcp_servers if item.strip()}
    filtered: list[ToolBinding] = []
    for binding in tools:
        tool_name = binding.tool.name
        is_mcp = is_mcp_tool_name(tool_name)
        if is_mcp:
            server = extract_mcp_server(tool_name)
            if server is None or server not in mcp_servers:
                continue
            if deny_mode and matches_any(tool_name, deny_patterns):
                continue
            filtered.append(binding)
            continue

        if allow_mode:
            if matches_any(tool_name, allow_patterns):
                filtered.append(binding)
            continue
        if deny_mode:
            if not matches_any(tool_name, deny_patterns):
                filtered.append(binding)
            continue
        # Neither allow nor deny: no non-MCP tools are exposed.
    return filtered


def strip_reserved_delegation_tools(tools: Sequence[ToolBinding]) -> list[ToolBinding]:
    return [binding for binding in tools if binding.tool.name not in RESERVED_DELEGATION_TOOL_NAMES]


def normalize_model_overrides(payload: Mapping[str, Any] | None) -> dict[str, str]:
    """Keep only the known override keys carrying a non-empty string."""
    if not payload:
        return {}
    overrides: dict[str, str] = {}
    for key in MODEL_OVERRIDE_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            overrides[key] = value.strip()
    return overrides


def resolve_delegation_target(
    settings: Settings,
    spec: AgentSpec | None,
    overrides: Mapping[str, Any] | None,
) -> tuple[str, str]:
    """The provider and model a delegated run actually talks to.

    Mirrors ``LLMClientFactory.create_for_agent``, which starts from ``[llm]`` and replaces only
    what the spec sets. An agent whose frontmatter omits ``model_provider`` inherits the main one,
    and a task with no ``agent_name`` runs the default worker on ``[llm]`` outright, so resolving
    limits against a bare ``spec.model_provider`` resolves nothing at all.
    """
    normalized = normalize_model_overrides(overrides)
    provider = normalized.get("model_provider") or (spec.model_provider if spec else None) or settings.llm.provider
    model = normalized.get("model") or (spec.model if spec else None) or settings.llm.model
    return provider, model


def is_retargeted(spec: AgentSpec | None, overrides: Mapping[str, Any] | None) -> bool:
    """Whether the overrides point this run at a different model than the spec's own."""
    normalized = normalize_model_overrides(overrides)
    if not normalized:
        return False
    return any(
        normalized.get(key) is not None and normalized[key] != (getattr(spec, key) if spec else None)
        for key in ("model_provider", "model")
    )


def apply_agent_overrides(spec: AgentSpec, overrides: Mapping[str, Any] | None) -> AgentSpec:
    """Retarget one invocation of an agent at another provider, model or reasoning effort.

    ``context_limit`` and ``max_new_tokens`` were derived from the spec's own model, so they are
    meaningless for an ad-hoc target and are dropped here. The daemon re-derives both against the
    real target and ships them in the task payload; it has to, because the worker is a cold
    subprocess whose limits cache would need a full catalog download to answer.
    """
    normalized = normalize_model_overrides(overrides)
    if not normalized:
        return spec
    if is_retargeted(spec, normalized):
        spec = replace(spec, context_limit=None, max_new_tokens=None)
    return replace(spec, **normalized)

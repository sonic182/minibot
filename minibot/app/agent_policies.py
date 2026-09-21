from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from minibot.app.mcp_tool_name import extract_mcp_server, is_mcp_tool_name
from minibot.app.token_limits_autoconfig import cached_model_limits
from minibot.app.tool_policy_utils import matches_any, normalize_patterns, validate_allow_deny
from minibot.core.agents import AgentSpec
from minibot.llm.tools.base import ToolBinding

MODEL_OVERRIDE_KEYS = ("model_provider", "model", "reasoning_effort")

RESERVED_DELEGATION_TOOL_NAMES = {
    "invoke_agent",
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


def apply_agent_overrides(
    spec: AgentSpec,
    overrides: Mapping[str, Any] | None,
    *,
    context_ratio: float = 0.0,
) -> AgentSpec:
    """Retarget one invocation of an agent at another provider, model or reasoning effort.

    ``context_limit`` and ``max_new_tokens`` were derived at boot from the spec's own model, so
    they are meaningless for an ad-hoc target: they get re-derived from the cached limits of the
    model actually being called. A cache miss falls back to dropping both, which costs this run its
    mid-run compaction and tuned cap but never sends another model's window.
    """
    normalized = normalize_model_overrides(overrides)
    if not normalized:
        return spec
    retargeted = any(
        normalized.get(key, getattr(spec, key)) != getattr(spec, key) for key in ("model_provider", "model")
    )
    if retargeted:
        limits = cached_model_limits(
            normalized.get("model_provider") or spec.model_provider,
            normalized.get("model") or spec.model,
        )
        normalized_spec = replace(
            spec,
            context_limit=limits["context"] if limits else None,
            max_new_tokens=_retargeted_max_new_tokens(spec, limits, context_ratio),
        )
    else:
        normalized_spec = spec
    return replace(normalized_spec, **normalized)


def _retargeted_max_new_tokens(spec: AgentSpec, limits: dict[str, Any] | None, context_ratio: float) -> int | None:
    if not limits:
        return None
    budget = max(1, int(limits["context"] * context_ratio)) if context_ratio > 0 else None
    # `if value` mirrors token auto-config: it drops both an unset cap and the None output limit
    # the chatgpt_codex branch returns.
    ceilings = [value for value in (limits.get("output"), budget, spec.max_new_tokens) if value]
    return max(1, min(ceilings)) if ceilings else None

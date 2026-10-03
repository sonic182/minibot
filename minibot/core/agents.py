from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str
    system_prompt: str
    source_path: Path
    model_provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    omit_temperature: bool = False
    max_new_tokens: int | None = None
    # The model's total context window, resolved at boot by token_limits_autoconfig. Drives
    # mid-run compaction; None just means no catalog entry, so no compaction.
    context_limit: int | None = None
    reasoning_effort: str | None = None
    max_tool_iterations: int | None = None
    timeout_seconds: int | None = None
    tools_allow: list[str] = field(default_factory=list)
    tools_deny: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    openrouter_provider_overrides: dict[str, Any] = field(default_factory=dict)
    openrouter_reasoning_enabled: bool | None = None


@runtime_checkable
class AgentCatalog(Protocol):
    def all(self) -> list[AgentSpec]: ...

    def get(self, name: str) -> AgentSpec | None: ...

    def names(self) -> list[str]: ...

    def is_empty(self) -> bool: ...


def normalize_model_overrides(payload: Mapping[str, Any] | None) -> dict[str, str]:
    if not payload:
        return {}
    keys = ("model_provider", "model", "reasoning_effort")
    return {key: value.strip() for key in keys if isinstance((value := payload.get(key)), str) and value.strip()}


@dataclass(frozen=True)
class DelegationDecision:
    should_delegate: bool
    agent_name: str | None = None
    reason: str = ""

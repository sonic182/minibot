from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentSpec:
    name: str
    description: str
    system_prompt: str
    source_path: Path
    model_provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_new_tokens: int | None = None
    reasoning_effort: str | None = None
    max_tool_iterations: int | None = None
    tools_allow: list[str] = field(default_factory=list)
    tools_deny: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    openrouter_provider_overrides: dict[str, Any] = field(default_factory=dict)
    openrouter_reasoning_enabled: bool | None = None


@dataclass(frozen=True)
class DelegationDecision:
    should_delegate: bool
    agent_name: str | None = None
    reason: str = ""

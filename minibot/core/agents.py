from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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
    allow_write: bool = True
    allow_edit: bool = True
    allow_bash: bool = True
    tool_allow: list[str] = field(default_factory=list)
    tool_deny: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DelegationDecision:
    should_delegate: bool
    agent_name: str | None = None
    reason: str = ""


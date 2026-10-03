"""Authorization for model-authored agent definitions.

An owner-authored definition is trusted: it is a file the owner wrote, and the owner already decides
what the daemon may do. A model-authored one is not, so it is bounded by a ceiling the owner sets in
``[orchestration.agent_management]``. Nothing here is a sandbox — it is a grant check on what a
managed agent may be told to do, applied wherever a managed definition can become executable.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from minibot.app.agent_policies import RESERVED_DELEGATION_TOOL_NAMES
from minibot.app.mcp_tool_name import is_mcp_tool_name
from minibot.app.tool_policy_utils import matches_any
from minibot.config.schema import Settings
from minibot.core.agents import AgentSpec

# The agent-management surface itself, named here so the policy can refuse to grant it before the
# tools exist and so the tool modules have one place to read the names from.
MANAGEMENT_TOOL_NAMES = ("reload_agents", "create_agent", "update_agent", "delete_agent")

# The bundled skill that walks the model through authoring a managed agent. It is hidden unless
# writes are on, because without them the tools it tells the model to call do not exist.
MANAGED_AGENT_SKILL = "create-agent"

_FORBIDDEN_TOOL_NAMES = frozenset(MANAGEMENT_TOOL_NAMES) | frozenset(RESERVED_DELEGATION_TOOL_NAMES)
_PATTERN_CHARS = ("*", "?", "[")


@dataclasses.dataclass(frozen=True)
class ManagedAgentPolicy:
    """The owner-configured ceiling a managed agent may not exceed.

    ``tool_patterns`` are fnmatch patterns the owner writes, so the ceiling itself may use a
    wildcard. A managed definition may not: each of its ``tools_allow`` entries has to be an exact
    name, because only an exact name can be checked against a pattern without enumerating the tool
    list. MCP access is claimed through ``mcp_servers`` and never through ``tools_allow``.
    """

    tool_patterns: tuple[str, ...]
    mcp_servers: frozenset[str]
    providers: frozenset[str]

    @classmethod
    def from_settings(cls, settings: Settings) -> ManagedAgentPolicy:
        config = settings.orchestration.agent_management
        return cls(
            tool_patterns=tuple(config.tools_allow),
            mcp_servers=frozenset(config.mcp_servers),
            # Targeting the provider the daemon already runs on is not an escalation, so it stays
            # available even with an empty ceiling. Anything else has to be listed.
            providers=frozenset({*config.providers, settings.llm.provider}),
        )

    def authorize(self, spec: AgentSpec) -> None:
        """Raise ``ValueError`` when ``spec`` exceeds the ceiling."""
        source = str(spec.source_path)
        if spec.tools_deny:
            raise ValueError(
                f"{source}: a managed agent must use tools_allow, not tools_deny; tools_deny grants every "
                "tool except the listed patterns, so no ceiling can bound it"
            )
        for name in spec.tools_allow:
            self._authorize_tool(source, name)
        for server in spec.mcp_servers:
            if server not in self.mcp_servers:
                raise ValueError(
                    f"{source}: MCP server '{server}' is not in [orchestration.agent_management].mcp_servers"
                )
        self.authorize_provider(spec.model_provider, source=source)

    def _authorize_tool(self, source: str, name: str) -> None:
        if any(char in name for char in _PATTERN_CHARS):
            raise ValueError(
                f"{source}: managed tools_allow entry '{name}' is a pattern; list exact tool names so each "
                "one can be checked against the ceiling"
            )
        if is_mcp_tool_name(name):
            raise ValueError(
                f"{source}: '{name}' is an MCP tool; a managed agent claims MCP servers through mcp_servers, "
                "not tools_allow"
            )
        if name in _FORBIDDEN_TOOL_NAMES:
            raise ValueError(f"{source}: tool '{name}' is reserved and cannot be granted to a managed agent")
        if not matches_any(name, self.tool_patterns):
            raise ValueError(f"{source}: tool '{name}' is not allowed by [orchestration.agent_management].tools_allow")

    def authorize_provider(self, provider: str | None, *, source: str) -> None:
        if provider is None or provider in self.providers:
            return
        raise ValueError(
            f"{source}: provider '{provider}' is not allowed by [orchestration.agent_management].providers"
        )


def native_skills_hidden_by_management(settings: Settings) -> list[str]:
    """Bundled skills whose prerequisite feature is off in this configuration."""
    if settings.orchestration.agent_management.write:
        return []
    return [MANAGED_AGENT_SKILL]


def is_managed_definition(spec: AgentSpec, managed_directory: Path) -> bool:
    """Whether a spec came from the managed directory.

    Provenance is the directory the loader read, never a frontmatter field: a model-authored
    definition must not be able to claim it is owner-authored by writing ``owner: true``.
    """
    try:
        return spec.source_path.resolve().parent == managed_directory.resolve()
    except OSError:
        return False

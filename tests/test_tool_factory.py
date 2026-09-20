from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from llm_async.models import Tool

from minibot.adapters.config.schema import CalculatorToolConfig, Settings, ToolsConfig
from minibot.app.agent_policies import RESERVED_DELEGATION_TOOL_NAMES
from minibot.app.agent_registry import AgentRegistry
from minibot.app.event_bus import EventBus
from minibot.app.extensions import load_extensions
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.core.agents import AgentSpec
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.factory import build_enabled_tools


class _MemoryStub:
    async def append_history(self, session_id: str, role: str, content: str) -> None:
        del session_id, role, content

    async def get_history(self, session_id: str, limit: int | None = None) -> list[object]:
        del session_id, limit
        return []

    async def count_history(self, session_id: str) -> int:
        del session_id
        return 0

    async def trim_history(self, session_id: str, keep_latest: int) -> int:
        del session_id, keep_latest
        return 0


def test_build_enabled_tools_keeps_only_core_tools_without_extensions() -> None:
    settings = Settings(tools=ToolsConfig(calculator=CalculatorToolConfig(enabled=True)))

    names = {binding.tool.name for binding in build_enabled_tools(settings, memory=_MemoryStub())}

    assert {"chat_history_info", "chat_history_trim", "calculate_expression"}.issubset(names)
    assert "current_datetime" not in names


def test_build_enabled_tools_merges_bundled_extension_tools() -> None:
    settings = Settings.from_dict({"tools": {"time": {"enabled": True}, "wait": {"enabled": True}}})
    registry = load_extensions(settings, EventBus(), entrypoint="console")

    names = {
        binding.tool.name
        for binding in build_enabled_tools(settings, memory=_MemoryStub(), extension_tools=registry.tools)
    }

    assert {"current_datetime", "wait"}.issubset(names)


def test_build_enabled_tools_rejects_extension_tool_name_collisions() -> None:
    settings = Settings()

    async def handler(payload: dict[str, Any], context: ToolContext) -> dict[str, bool]:
        del payload, context
        return {"ok": True}

    duplicate = ToolBinding(
        tool=Tool(name="calculate_expression", description="duplicate", parameters={}),
        handler=handler,
    )
    with pytest.raises(ValueError, match="duplicate tool name"):
        build_enabled_tools(settings, memory=_MemoryStub(), extension_tools=[duplicate])


def test_specialists_are_scoped_against_extension_tools_too() -> None:
    """A delegate built before the extension tools scopes specialists against core tools alone."""
    settings = Settings.from_dict({"tools": {"time": {"enabled": True}, "wait": {"enabled": True}}})
    registry = load_extensions(settings, EventBus(), entrypoint="console")
    specialist = AgentSpec(
        name="general_agent",
        description="generalist",
        system_prompt="do the work",
        source_path=Path("agents/general.md"),
        tools_deny=["mcp*"],
    )

    tools = build_enabled_tools(
        settings,
        memory=_MemoryStub(),
        extension_tools=registry.tools,
        agent_registry=AgentRegistry([specialist]),
        llm_factory=LLMClientFactory(settings),
    )

    delegate = next(binding for binding in tools if binding.tool.name == "invoke_agent").handler.__self__
    scoped = {binding.tool.name for binding in delegate._scoped_tools(specialist)}

    assert {"current_datetime", "wait", "calculate_expression"}.issubset(scoped)
    # Recursive delegation and async hand-off stay out of a specialist's reach.
    assert scoped.isdisjoint(RESERVED_DELEGATION_TOOL_NAMES)

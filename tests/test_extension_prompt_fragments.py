from __future__ import annotations

import logging

from llm_async.models import Tool

from minibot.adapters.config.schema import Settings
from minibot.adapters.mcp.client import MCPServerMetadata
from minibot.app.event_bus import EventBus
from minibot.app.extensions import ExtensionContext, ExtensionRegistry
from minibot.app.handlers.services.prompt_service import PromptService
from minibot.extensions.integrations.mcp import _instructions_fragment, _server_instructions
from minibot.llm.services import LLMExecutionProfile
from minibot.llm.tools.base import ToolBinding, ToolContext, ToolPayload


def _context(name: str) -> ExtensionContext:
    return ExtensionContext(
        name=name,
        config={},
        settings=Settings(),
        event_bus=EventBus(),
        logger=logging.getLogger("test.fragments"),
    )


async def _noop_handler(payload: ToolPayload, context: ToolContext) -> None:
    del payload, context


def _binding(name: str) -> ToolBinding:
    return ToolBinding(
        tool=Tool(name=name, description=name, parameters={"type": "object", "properties": {}}),
        handler=_noop_handler,
    )


def test_registry_collects_fragments_across_extensions() -> None:
    first = _context("one")
    first.add_prompt_fragment("## One\n\nuse it well")
    second = _context("two")
    second.add_prompt_fragment("## Two\n\nand this one")

    registry = ExtensionRegistry([first, second], logging.getLogger("test.fragments"))

    assert registry.prompt_fragments == ["## One\n\nuse it well", "## Two\n\nand this one"]


def test_registry_excludes_fragments_for_hidden_tools() -> None:
    context = _context("one")
    context.add_prompt_fragment("always visible")
    context.add_prompt_fragment("visible tool", tool_names=["visible"])
    context.add_prompt_fragment("hidden tool", tool_names=["hidden"])

    registry = ExtensionRegistry([context], logging.getLogger("test.fragments"))

    assert registry.prompt_fragments_for([_binding("visible")]) == ["always visible", "visible tool"]


def test_blank_fragments_are_dropped() -> None:
    context = _context("quiet")
    context.add_prompt_fragment("")
    context.add_prompt_fragment("   \n  ")

    # An extension with nothing to say must not push an empty section into every turn's prompt.
    assert context.prompt_fragments == []


def test_mcp_fragment_has_server_instructions() -> None:
    assert _instructions_fragment("gmem", "Use recall before changing code.") == (
        "## MCP server: gmem\n\nUse recall before changing code."
    )


def test_no_mcp_section_when_no_server_ships_instructions() -> None:
    assert _instructions_fragment("silent", "") == ""


class _CachedMetadataClient:
    server_metadata = MCPServerMetadata(name="gmem", instructions="Use recall before changing code.")

    def get_server_metadata_blocking(self) -> MCPServerMetadata:
        raise AssertionError("instruction lookup must not retry metadata discovery")


def test_server_instructions_uses_cached_metadata() -> None:
    assert _server_instructions(_CachedMetadataClient()) == "Use recall before changing code."


class _StubLLMClient:
    def __init__(self, prompts_dir: str) -> None:
        self._prompts_dir = prompts_dir

    def features(self) -> LLMExecutionProfile:
        return LLMExecutionProfile(system_prompt="base prompt", prompts_dir=self._prompts_dir)


def test_prompt_service_puts_the_fragments_in_the_system_prompt(tmp_path) -> None:
    service = PromptService(
        llm_client=_StubLLMClient(str(tmp_path)),
        tools=[],
        environment_prompt_fragment="",
        logger=logging.getLogger("test.fragments"),
        extension_prompt_fragments=["## MCP servers\n\n### gmem\n\nUse recall first."],
    )

    prompt = service.compose_system_prompt(channel=None)

    assert prompt.startswith("base prompt")
    assert "## MCP servers\n\n### gmem\n\nUse recall first." in prompt

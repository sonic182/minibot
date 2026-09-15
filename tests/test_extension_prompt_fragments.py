from __future__ import annotations

import logging

from minibot.adapters.config.schema import Settings
from minibot.app.event_bus import EventBus
from minibot.app.extensions import ExtensionContext, ExtensionRegistry
from minibot.app.handlers.services.prompt_service import PromptService
from minibot.extensions.integrations.mcp import _instructions_fragment
from minibot.llm.services import LLMExecutionProfile


def _context(name: str) -> ExtensionContext:
    return ExtensionContext(
        name=name,
        config={},
        settings=Settings(),
        event_bus=EventBus(),
        logger=logging.getLogger("test.fragments"),
    )


def test_registry_collects_fragments_across_extensions() -> None:
    first = _context("one")
    first.add_prompt_fragment("## One\n\nuse it well")
    second = _context("two")
    second.add_prompt_fragment("## Two\n\nand this one")

    registry = ExtensionRegistry([first, second], logging.getLogger("test.fragments"))

    assert registry.prompt_fragments == ["## One\n\nuse it well", "## Two\n\nand this one"]


def test_blank_fragments_are_dropped() -> None:
    context = _context("quiet")
    context.add_prompt_fragment("")
    context.add_prompt_fragment("   \n  ")

    # An extension with nothing to say must not push an empty section into every turn's prompt.
    assert context.prompt_fragments == []


def test_mcp_fragment_has_one_subsection_per_server_with_instructions() -> None:
    servers = [
        {"name": "gmem", "instructions": "Use recall before changing code."},
        {"name": "silent", "instructions": ""},
        {"name": "broken", "tools": [], "error": "boom"},
        {"name": "other", "instructions": "Prefer the search tool."},
    ]

    fragment = _instructions_fragment(servers)

    assert fragment == (
        "## MCP servers\n\n### gmem\n\nUse recall before changing code.\n\n### other\n\nPrefer the search tool."
    )


def test_no_mcp_section_when_no_server_ships_instructions() -> None:
    assert _instructions_fragment([{"name": "silent", "instructions": ""}]) == ""
    assert _instructions_fragment([]) == ""


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

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from minibot.app.agent_registry import AgentRegistry
from minibot.app.event_bus import EventBus
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.app.skill_definitions_loader import NATIVE_SKILLS_DIR, parse_skill_file
from minibot.app.skill_registry import SkillRegistry
from minibot.app.tool_approval import Approver, apply_tool_approval, request_tool_approval
from minibot.app.tool_constructors import build_calculator_tool, build_skill_loader_bindings
from minibot.config.schema import Settings
from minibot.core.files import FileStorage
from minibot.core.memory import KeyValueMemory, MemoryBackend
from minibot.core.tasks import TaskManager, TaskProducer
from minibot.llm.services.tool_executor import canonical_tool_name
from minibot.llm.tools.agent_info import AgentInfoTool
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.chat_memory import ChatMemoryTool
from minibot.llm.tools.docs_reader import DocsReaderTool
from minibot.llm.tools.output_spill import apply_tool_output_spill
from minibot.llm.tools.settings_info import SettingsInfoTool
from minibot.llm.tools.tool_events import apply_tool_call_events

if TYPE_CHECKING:  # pragma: no cover
    from minibot.app.scheduler_service import ScheduledPromptService


def build_enabled_tools(
    settings: Settings,
    memory: MemoryBackend,
    kv_memory: KeyValueMemory | None = None,
    prompt_scheduler: ScheduledPromptService | None = None,
    event_bus: EventBus | None = None,
    agent_registry: AgentRegistry | None = None,
    llm_factory: LLMClientFactory | None = None,
    skill_registry: SkillRegistry | None = None,
    task_manager: TaskManager | None = None,
    task_producer: TaskProducer | None = None,
    extension_tools: Sequence[ToolBinding] | None = None,
    managed_storage: FileStorage | None = None,
    config_path: Path | None = None,
) -> list[ToolBinding]:
    """Build core tools, then merge contributions from loaded extensions.

    ``kv_memory``, ``prompt_scheduler``, ``task_manager``, and ``task_producer`` remain
    accepted temporarily for callers outside the daemon; their tools are bundled extensions now.
    """
    tools = ChatMemoryTool(memory, max_history_messages=settings.memory.max_history_messages).bindings()
    tools.extend(
        SettingsInfoTool(
            settings,
            config_path=config_path,
            agent_names=agent_registry.names if agent_registry is not None else None,
            skill_names=skill_registry.names if skill_registry is not None else None,
        ).bindings()
    )
    skills_config = settings.tools.skills
    if skills_config.enabled and skills_config.native and "minibot-docs" not in skills_config.disabled_native_skills:
        tools.extend(DocsReaderTool().bindings())
    if settings.tools.calculator.enabled:
        calculator = settings.tools.calculator
        tools.extend(
            build_calculator_tool(
                default_scale=calculator.default_scale,
                max_expression_length=calculator.max_expression_length,
                max_exponent_abs=calculator.max_exponent_abs,
            ).bindings()
        )
    if settings.tools.skills.enabled and skill_registry is not None:
        tools.extend(build_skill_loader_bindings(skill_registry, managed_storage, settings.tools.bash.enabled))
        if settings.tools.skills.install:
            from minibot.llm.tools.skill_installer import SkillInstallerTool

            tools.extend(
                SkillInstallerTool(
                    skill_registry,
                    parse_skill=parse_skill_file,
                    native_skills_dir=NATIVE_SKILLS_DIR,
                ).bindings()
            )
    if extension_tools:
        tools.extend(extension_tools)
    if agent_registry is not None and llm_factory is not None and not agent_registry.is_empty():
        tools.extend(AgentInfoTool(registry=agent_registry, providers=llm_factory.available_providers()).bindings())
    _ensure_unique_tool_names(tools)
    return apply_tool_call_events(
        apply_tool_approval(
            apply_tool_output_spill(
                tools,
                storage=managed_storage,
                config=settings.tools.tool_output_spill,
            ),
            patterns=settings.tools.approval.require_approval,
            approve=_event_bus_approver(event_bus, settings.tools.approval.timeout_seconds),
        ),
        event_bus=event_bus,
    )


def _event_bus_approver(event_bus: EventBus | None, timeout_seconds: float) -> Approver:
    async def approve(tool_name: str, arguments: dict[str, Any], context: ToolContext) -> bool:
        if event_bus is None:
            return False
        return await request_tool_approval(
            event_bus,
            tool_name=tool_name,
            arguments=arguments,
            channel=context.channel,
            chat_id=context.chat_id,
            timeout_seconds=timeout_seconds,
            supports_tool_approval=context.channel_capabilities.supports_tool_approval,
        )

    return approve


def _ensure_unique_tool_names(tools: list[ToolBinding]) -> None:
    seen: dict[str, str] = {}
    for binding in tools:
        tool_name = binding.tool.name
        canonical_name = canonical_tool_name(tool_name)
        previous_name = seen.get(canonical_name)
        if previous_name is not None:
            raise ValueError(f"duplicate tool name detected: {tool_name} conflicts with {previous_name}")
        seen[canonical_name] = tool_name

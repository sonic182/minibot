from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from minibot.adapters.config.schema import Settings
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.app.agent_registry import AgentRegistry
from minibot.app.environment_context import build_environment_prompt_fragment
from minibot.app.event_bus import EventBus
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.app.skill_registry import SkillRegistry
from minibot.core.memory import KeyValueMemory, MemoryBackend
from minibot.core.tasks import TaskProducer
from minibot.llm.services.tool_executor import canonical_tool_name
from minibot.llm.tools.agent_delegate import AgentDelegateTool
from minibot.llm.tools.base import ToolBinding
from minibot.llm.tools.calculator import CalculatorTool
from minibot.llm.tools.chat_memory import ChatMemoryTool
from minibot.llm.tools.output_spill import apply_tool_output_spill
from minibot.llm.tools.tool_events import apply_tool_call_events

if TYPE_CHECKING:  # pragma: no cover
    from minibot.adapters.tasks.manager import TaskManager
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
) -> list[ToolBinding]:
    """Build core tools, then merge contributions from loaded extensions.

    ``kv_memory``, ``prompt_scheduler``, ``task_manager``, and ``task_producer`` remain
    accepted temporarily for callers outside the daemon; their tools are bundled extensions now.
    """
    managed_storage = _build_managed_storage(settings) if settings.tools.file_storage.enabled else None
    tools = ChatMemoryTool(memory, max_history_messages=settings.memory.max_history_messages).bindings()
    if settings.tools.calculator.enabled:
        calculator = settings.tools.calculator
        tools.extend(
            CalculatorTool(
                default_scale=calculator.default_scale,
                max_expression_length=calculator.max_expression_length,
                max_exponent_abs=calculator.max_exponent_abs,
            ).bindings()
        )
    if settings.tools.skills.enabled and skill_registry is not None:
        from minibot.llm.tools.skill_loader import SkillLoaderTool

        tools.extend(SkillLoaderTool(skill_registry).bindings())
    if agent_registry is not None and llm_factory is not None and not agent_registry.is_empty():
        tools.extend(
            AgentDelegateTool(
                registry=agent_registry,
                llm_factory=llm_factory,
                tools=tools,
                default_timeout_seconds=settings.orchestration.default_timeout_seconds,
                delegated_tool_call_policy=settings.orchestration.delegated_tool_call_policy,
                environment_prompt_fragment=build_environment_prompt_fragment(settings),
                managed_storage=managed_storage,
                spill_config=settings.tools.tool_output_spill,
            ).bindings()
        )
    if extension_tools:
        tools.extend(extension_tools)
    _ensure_unique_tool_names(tools)
    return apply_tool_call_events(
        apply_tool_output_spill(
            tools,
            storage=managed_storage,
            config=settings.tools.tool_output_spill,
        ),
        event_bus=event_bus,
    )


def _build_managed_storage(settings: Settings) -> LocalFileStorage:
    return LocalFileStorage(
        root_dir=settings.tools.file_storage.root_dir,
        max_write_bytes=settings.tools.file_storage.max_write_bytes,
        allow_outside_root=settings.tools.file_storage.allow_outside_root,
    )


def _ensure_unique_tool_names(tools: list[ToolBinding]) -> None:
    seen: dict[str, str] = {}
    for binding in tools:
        tool_name = binding.tool.name
        canonical_name = canonical_tool_name(tool_name)
        previous_name = seen.get(canonical_name)
        if previous_name is not None:
            raise ValueError(f"duplicate tool name detected: {tool_name} conflicts with {previous_name}")
        seen[canonical_name] = tool_name

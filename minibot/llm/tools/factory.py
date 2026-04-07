from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from minibot.adapters.config.schema import Settings
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.adapters.mcp.client import MCPClient
from minibot.app.agent_registry import AgentRegistry
from minibot.app.environment_context import build_environment_prompt_fragment
from minibot.app.skill_registry import SkillRegistry
from minibot.app.event_bus import EventBus
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.core.memory import KeyValueMemory, MemoryBackend
from minibot.llm.tools.agent_delegate import AgentDelegateTool
from minibot.llm.tools.apply_patch import ApplyPatchTool
from minibot.llm.tools.audio_transcription import AudioTranscriptionTool
from minibot.llm.tools.bash import BashTool
from minibot.llm.tools.base import ToolBinding
from minibot.llm.tools.calculator import CalculatorTool
from minibot.llm.tools.chat_memory import ChatMemoryTool
from minibot.llm.tools.code_read import CodeReadTool
from minibot.llm.tools.file_storage import FileStorageTool
from minibot.llm.tools.grep import GrepTool
from minibot.llm.tools.http_client import HTTPClientTool
from minibot.llm.tools.mcp_bridge import MCPToolBridge
from minibot.llm.tools.python_exec import HostPythonExecTool
from minibot.llm.tools.scheduler import SchedulePromptTool
from minibot.llm.tools.time import CurrentTimeTool
from minibot.llm.tools.user_memory import build_kv_tools
from minibot.llm.services.tool_executor import canonical_tool_name

if TYPE_CHECKING:  # pragma: no cover
    from minibot.app.scheduler_service import ScheduledPromptService


FeatureBuilder = Callable[["ToolAssemblyContext", list[ToolBinding]], list[ToolBinding]]
ConfigEnabled = Callable[[Settings], bool]


@dataclass(frozen=True)
class ToolAssemblyContext:
    settings: Settings
    memory: MemoryBackend
    kv_memory: KeyValueMemory | None
    prompt_scheduler: ScheduledPromptService | None
    event_bus: EventBus | None
    agent_registry: AgentRegistry | None
    skill_registry: SkillRegistry | None
    llm_factory: LLMClientFactory | None
    managed_storage: LocalFileStorage | None
    environment_prompt_fragment: str


@dataclass(frozen=True)
class ToolFeature:
    key: str
    labels: tuple[str, ...]
    enabled_in_config: ConfigEnabled
    builder: FeatureBuilder


def configured_tool_labels(settings: Settings) -> list[str]:
    labels: list[str] = []
    for feature in _OPTIONAL_FEATURES:
        if feature.enabled_in_config(settings):
            labels.extend(feature.labels)
    return labels


def build_enabled_tools(
    settings: Settings,
    memory: MemoryBackend,
    kv_memory: KeyValueMemory | None,
    prompt_scheduler: ScheduledPromptService | None = None,
    event_bus: EventBus | None = None,
    agent_registry: AgentRegistry | None = None,
    llm_factory: LLMClientFactory | None = None,
    skill_registry: SkillRegistry | None = None,
) -> list[ToolBinding]:
    context = ToolAssemblyContext(
        settings=settings,
        memory=memory,
        kv_memory=kv_memory,
        prompt_scheduler=prompt_scheduler,
        event_bus=event_bus,
        agent_registry=agent_registry,
        skill_registry=skill_registry,
        llm_factory=llm_factory,
        managed_storage=_build_managed_storage(settings) if settings.tools.file_storage.enabled else None,
        environment_prompt_fragment=build_environment_prompt_fragment(settings),
    )
    tools = ChatMemoryTool(memory, max_history_messages=settings.memory.max_history_messages).bindings()
    for feature in _OPTIONAL_FEATURES:
        if not feature.enabled_in_config(settings):
            continue
        tools.extend(feature.builder(context, tools))
    _ensure_unique_tool_names(tools)
    return tools


def _build_kv_feature(context: ToolAssemblyContext, _: list[ToolBinding]) -> list[ToolBinding]:
    if context.kv_memory is None:
        return []
    return build_kv_tools(context.kv_memory)


def _build_http_feature(context: ToolAssemblyContext, _: list[ToolBinding]) -> list[ToolBinding]:
    return HTTPClientTool(context.settings.tools.http_client, storage=context.managed_storage).bindings()


def _build_time_feature(context: ToolAssemblyContext, _: list[ToolBinding]) -> list[ToolBinding]:
    return CurrentTimeTool(context.settings.tools.time.default_format).bindings()


def _build_calculator_feature(context: ToolAssemblyContext, _: list[ToolBinding]) -> list[ToolBinding]:
    settings = context.settings.tools.calculator
    return CalculatorTool(
        default_scale=settings.default_scale,
        max_expression_length=settings.max_expression_length,
        max_exponent_abs=settings.max_exponent_abs,
    ).bindings()


def _build_python_feature(context: ToolAssemblyContext, _: list[ToolBinding]) -> list[ToolBinding]:
    return HostPythonExecTool(context.settings.tools.python_exec, storage=context.managed_storage).bindings()


def _build_bash_feature(context: ToolAssemblyContext, _: list[ToolBinding]) -> list[ToolBinding]:
    return BashTool(context.settings.tools.bash).bindings()


def _build_apply_patch_feature(context: ToolAssemblyContext, _: list[ToolBinding]) -> list[ToolBinding]:
    return ApplyPatchTool(context.settings.tools.apply_patch).bindings()


def _build_file_storage_feature(context: ToolAssemblyContext, _: list[ToolBinding]) -> list[ToolBinding]:
    storage = _require_managed_storage(
        context.managed_storage,
        error_message="tools.file_storage.enabled requires tools.file_storage configuration",
    )
    return [
        *FileStorageTool(storage=storage, event_bus=context.event_bus).bindings(),
        *CodeReadTool(storage=storage).bindings(),
    ]


def _build_audio_feature(context: ToolAssemblyContext, _: list[ToolBinding]) -> list[ToolBinding]:
    storage = _require_managed_storage(
        context.managed_storage,
        error_message="tools.audio_transcription.enabled requires tools.file_storage.enabled",
    )
    return AudioTranscriptionTool(config=context.settings.tools.audio_transcription, storage=storage).bindings()


def _build_grep_feature(context: ToolAssemblyContext, _: list[ToolBinding]) -> list[ToolBinding]:
    storage = _require_managed_storage(
        context.managed_storage,
        error_message="tools.grep.enabled requires tools.file_storage.enabled",
    )
    return GrepTool(storage=storage, config=context.settings.tools.grep).bindings()


def _build_scheduler_feature(context: ToolAssemblyContext, _: list[ToolBinding]) -> list[ToolBinding]:
    if context.prompt_scheduler is None:
        return []
    return SchedulePromptTool(
        context.prompt_scheduler,
        min_recurrence_interval_seconds=context.settings.scheduler.prompts.min_recurrence_interval_seconds,
    ).bindings()


def _build_mcp_feature(context: ToolAssemblyContext, _: list[ToolBinding]) -> list[ToolBinding]:
    logger = logging.getLogger("minibot.tools.factory")
    bindings: list[ToolBinding] = []
    for server in context.settings.tools.mcp.servers:
        server_args = _override_playwright_output_dir(
            server_name=server.name,
            args=server.args,
            browser_output_dir=context.settings.tools.browser.output_dir,
        )
        client = MCPClient(
            server_name=server.name,
            transport=server.transport,
            timeout_seconds=context.settings.tools.mcp.timeout_seconds,
            command=server.command,
            args=server_args,
            env=server.env or None,
            cwd=server.cwd,
            url=server.url,
            headers=server.headers,
        )
        bridge = MCPToolBridge(
            server_name=server.name,
            client=client,
            name_prefix=context.settings.tools.mcp.name_prefix,
            enabled_tools=server.enabled_tools,
            disabled_tools=server.disabled_tools,
        )
        try:
            bindings.extend(bridge.build_bindings())
        except Exception as exc:  # noqa: BLE001
            logger.exception("failed to load mcp tools", exc_info=exc, extra={"server": server.name})
    return bindings


def _build_skill_feature(context: ToolAssemblyContext, _: list[ToolBinding]) -> list[ToolBinding]:
    if context.skill_registry is None or context.skill_registry.is_empty():
        return []
    from minibot.llm.tools.skill_loader import SkillLoaderTool

    return SkillLoaderTool(context.skill_registry).bindings()


def _build_agent_delegate_feature(context: ToolAssemblyContext, tools: list[ToolBinding]) -> list[ToolBinding]:
    if context.agent_registry is None or context.llm_factory is None or context.agent_registry.is_empty():
        return []
    return AgentDelegateTool(
        registry=context.agent_registry,
        llm_factory=context.llm_factory,
        tools=tools,
        default_timeout_seconds=context.settings.orchestration.default_timeout_seconds,
        delegated_tool_call_policy=context.settings.orchestration.delegated_tool_call_policy,
        environment_prompt_fragment=context.environment_prompt_fragment,
    ).bindings()


def _tool_enabled(settings: Settings, field: str) -> bool:
    tool_cfg = getattr(settings.tools, field, None)
    return bool(tool_cfg is not None and getattr(tool_cfg, "enabled", False))


def _scheduler_enabled(settings: Settings) -> bool:
    prompts_cfg = getattr(getattr(settings, "scheduler", None), "prompts", None)
    return bool(prompts_cfg is not None and getattr(prompts_cfg, "enabled", False))


_OPTIONAL_FEATURES: tuple[ToolFeature, ...] = (
    ToolFeature(
        key="kv_memory",
        labels=("memory",),
        enabled_in_config=lambda settings: _tool_enabled(settings, "kv_memory"),
        builder=_build_kv_feature,
    ),
    ToolFeature(
        key="http_client",
        labels=("http_request",),
        enabled_in_config=lambda settings: _tool_enabled(settings, "http_client"),
        builder=_build_http_feature,
    ),
    ToolFeature(
        key="time",
        labels=("current_datetime",),
        enabled_in_config=lambda settings: _tool_enabled(settings, "time"),
        builder=_build_time_feature,
    ),
    ToolFeature(
        key="calculator",
        labels=("calculate_expression",),
        enabled_in_config=lambda settings: _tool_enabled(settings, "calculator"),
        builder=_build_calculator_feature,
    ),
    ToolFeature(
        key="python_exec",
        labels=("python_execute",),
        enabled_in_config=lambda settings: _tool_enabled(settings, "python_exec"),
        builder=_build_python_feature,
    ),
    ToolFeature(
        key="bash",
        labels=("bash",),
        enabled_in_config=lambda settings: _tool_enabled(settings, "bash"),
        builder=_build_bash_feature,
    ),
    ToolFeature(
        key="apply_patch",
        labels=("apply_patch",),
        enabled_in_config=lambda settings: _tool_enabled(settings, "apply_patch"),
        builder=_build_apply_patch_feature,
    ),
    ToolFeature(
        key="file_storage",
        labels=("filesystem", "code_read"),
        enabled_in_config=lambda settings: _tool_enabled(settings, "file_storage"),
        builder=_build_file_storage_feature,
    ),
    ToolFeature(
        key="audio_transcription",
        labels=("transcribe_audio",),
        enabled_in_config=lambda settings: _tool_enabled(settings, "audio_transcription"),
        builder=_build_audio_feature,
    ),
    ToolFeature(
        key="grep",
        labels=("grep",),
        enabled_in_config=lambda settings: _tool_enabled(settings, "grep"),
        builder=_build_grep_feature,
    ),
    ToolFeature(
        key="scheduler",
        labels=("schedule",),
        enabled_in_config=_scheduler_enabled,
        builder=_build_scheduler_feature,
    ),
    ToolFeature(
        key="mcp",
        labels=("mcp",),
        enabled_in_config=lambda settings: _tool_enabled(settings, "mcp"),
        builder=_build_mcp_feature,
    ),
    ToolFeature(
        key="skills",
        labels=("activate_skill",),
        enabled_in_config=lambda settings: _tool_enabled(settings, "skills"),
        builder=_build_skill_feature,
    ),
    ToolFeature(
        key="agent_delegate",
        labels=(),
        enabled_in_config=lambda _settings: True,
        builder=_build_agent_delegate_feature,
    ),
)


def _build_managed_storage(settings: Settings) -> LocalFileStorage:
    return LocalFileStorage(
        root_dir=settings.tools.file_storage.root_dir,
        max_write_bytes=settings.tools.file_storage.max_write_bytes,
        allow_outside_root=settings.tools.file_storage.allow_outside_root,
    )


def _require_managed_storage(storage: LocalFileStorage | None, *, error_message: str) -> LocalFileStorage:
    if storage is None:
        raise ValueError(error_message)
    return storage


def _override_playwright_output_dir(*, server_name: str, args: list[str], browser_output_dir: str) -> list[str]:
    if server_name != "playwright-cli":
        return list(args)
    normalized_dir = browser_output_dir.strip()
    if not normalized_dir:
        return list(args)

    updated: list[str] = []
    replaced = False
    skip_next = False
    for index, arg in enumerate(args):
        if skip_next:
            skip_next = False
            continue
        if arg == "--output-dir":
            updated.append(f"--output-dir={normalized_dir}")
            replaced = True
            if index + 1 < len(args):
                skip_next = True
            continue
        if arg.startswith("--output-dir="):
            updated.append(f"--output-dir={normalized_dir}")
            replaced = True
            continue
        updated.append(arg)
    if not replaced:
        updated.append(f"--output-dir={normalized_dir}")
    return updated


def _ensure_unique_tool_names(tools: list[ToolBinding]) -> None:
    seen: dict[str, str] = {}
    for binding in tools:
        tool_name = binding.tool.name
        canonical_name = canonical_tool_name(tool_name)
        previous_name = seen.get(canonical_name)
        if previous_name is not None:
            raise ValueError(f"duplicate tool name detected: {tool_name} conflicts with {previous_name}")
        seen[canonical_name] = tool_name

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.adapters.mcp.client import MCPClient
from minibot.adapters.config.schema import Settings
from minibot.core.memory import KeyValueMemory, MemoryBackend
from minibot.app.environment_context import build_environment_prompt_fragment
from minibot.app.event_bus import EventBus
from minibot.app.agent_registry import AgentRegistry
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.llm.tools.base import ToolBinding
from minibot.llm.tools.agent_delegate import AgentDelegateTool
from minibot.llm.tools.apply_patch import ApplyPatchTool
from minibot.llm.tools.audio_transcription import AudioTranscriptionTool
from minibot.llm.tools.bash import BashTool
from minibot.llm.tools.calculator import CalculatorTool
from minibot.llm.tools.chat_memory import ChatMemoryTool
from minibot.llm.tools.code_read import CodeReadTool
from minibot.llm.tools.file_storage import FileStorageTool
from minibot.llm.tools.grep import GrepTool
from minibot.llm.tools.http_client import HTTPClientTool
from minibot.llm.tools.mcp_bridge import MCPToolBridge
from minibot.llm.tools.user_memory import build_kv_tools
from minibot.llm.tools.python_exec import HostPythonExecTool
from minibot.llm.tools.scheduler import SchedulePromptTool
from minibot.llm.tools.time import CurrentTimeTool

if TYPE_CHECKING:  # pragma: no cover
    from minibot.app.scheduler_service import ScheduledPromptService


def configured_tool_labels(settings: Settings) -> list[str]:
    labels: list[str] = []
    if _tool_enabled(settings, "kv_memory"):
        labels.append("memory")
    if _tool_enabled(settings, "http_client"):
        labels.append("http_request")
    if _tool_enabled(settings, "time"):
        labels.append("current_datetime")
    if _tool_enabled(settings, "calculator"):
        labels.append("calculate_expression")
    if _tool_enabled(settings, "python_exec"):
        labels.append("python_execute")
    if _tool_enabled(settings, "bash"):
        labels.append("bash")
    if _tool_enabled(settings, "apply_patch"):
        labels.append("apply_patch")
    if _tool_enabled(settings, "file_storage"):
        labels.append("filesystem")
        labels.append("code_read")
    if _tool_enabled(settings, "grep"):
        labels.append("grep")
    if _tool_enabled(settings, "audio_transcription"):
        labels.append("transcribe_audio")
    prompts_cfg = getattr(getattr(settings, "scheduler", None), "prompts", None)
    if prompts_cfg is not None and getattr(prompts_cfg, "enabled", False):
        labels.append("schedule")
    if _tool_enabled(settings, "mcp"):
        labels.append("mcp")
    return labels


def build_enabled_tools(
    settings: Settings,
    memory: MemoryBackend,
    kv_memory: KeyValueMemory | None,
    prompt_scheduler: ScheduledPromptService | None = None,
    event_bus: EventBus | None = None,
    agent_registry: AgentRegistry | None = None,
    llm_factory: LLMClientFactory | None = None,
) -> list[ToolBinding]:
    tools: list[ToolBinding] = []
    environment_prompt_fragment = build_environment_prompt_fragment(settings)
    managed_storage = _build_managed_storage(settings) if settings.tools.file_storage.enabled else None

    tools.extend(ChatMemoryTool(memory, max_history_messages=settings.memory.max_history_messages).bindings())
    if settings.tools.kv_memory.enabled and kv_memory is not None:
        tools.extend(build_kv_tools(kv_memory))
    if settings.tools.http_client.enabled:
        tools.extend(HTTPClientTool(settings.tools.http_client).bindings())
    if settings.tools.time.enabled:
        tools.extend(CurrentTimeTool(settings.tools.time.default_format).bindings())
    if settings.tools.calculator.enabled:
        tools.extend(
            CalculatorTool(
                default_scale=settings.tools.calculator.default_scale,
                max_expression_length=settings.tools.calculator.max_expression_length,
                max_exponent_abs=settings.tools.calculator.max_exponent_abs,
            ).bindings()
        )
    if settings.tools.python_exec.enabled:
        tools.extend(HostPythonExecTool(settings.tools.python_exec, storage=managed_storage).bindings())
    if settings.tools.bash.enabled:
        tools.extend(BashTool(settings.tools.bash).bindings())
    if settings.tools.apply_patch.enabled:
        tools.extend(ApplyPatchTool(settings.tools.apply_patch).bindings())
    if settings.tools.file_storage.enabled:
        file_storage = _require_managed_storage(
            managed_storage,
            error_message="tools.file_storage.enabled requires tools.file_storage configuration",
        )
        tools.extend(FileStorageTool(storage=file_storage, event_bus=event_bus).bindings())
        tools.extend(CodeReadTool(storage=file_storage).bindings())
    if settings.tools.audio_transcription.enabled:
        transcription_storage = _require_managed_storage(
            managed_storage,
            error_message="tools.audio_transcription.enabled requires tools.file_storage.enabled",
        )
        tools.extend(
            AudioTranscriptionTool(
                config=settings.tools.audio_transcription,
                storage=transcription_storage,
            ).bindings()
        )
    if settings.tools.grep.enabled:
        grep_storage = _require_managed_storage(
            managed_storage,
            error_message="tools.grep.enabled requires tools.file_storage.enabled",
        )
        tools.extend(GrepTool(storage=grep_storage, config=settings.tools.grep).bindings())
    if settings.scheduler.prompts.enabled and prompt_scheduler is not None:
        tools.extend(
            SchedulePromptTool(
                prompt_scheduler,
                min_recurrence_interval_seconds=settings.scheduler.prompts.min_recurrence_interval_seconds,
            ).bindings()
        )
    if settings.tools.mcp.enabled:
        _extend_mcp_tools(tools=tools, settings=settings)
    if agent_registry is not None and llm_factory is not None and not agent_registry.is_empty():
        delegate_tool = AgentDelegateTool(
            registry=agent_registry,
            llm_factory=llm_factory,
            tools=tools,
            default_timeout_seconds=settings.orchestration.default_timeout_seconds,
            delegated_tool_call_policy=settings.orchestration.delegated_tool_call_policy,
            environment_prompt_fragment=environment_prompt_fragment,
        )
        tools.extend(delegate_tool.bindings())
    return tools


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


def _tool_enabled(settings: Settings, field: str) -> bool:
    tool_cfg = getattr(settings.tools, field, None)
    return bool(tool_cfg is not None and getattr(tool_cfg, "enabled", False))


def _extend_mcp_tools(*, tools: list[ToolBinding], settings: Settings) -> None:
    logger = logging.getLogger("minibot.tools.factory")
    for server in settings.tools.mcp.servers:
        server_args = _override_playwright_output_dir(
            server_name=server.name,
            args=server.args,
            browser_output_dir=settings.tools.browser.output_dir,
        )
        client = MCPClient(
            server_name=server.name,
            transport=server.transport,
            timeout_seconds=settings.tools.mcp.timeout_seconds,
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
            name_prefix=settings.tools.mcp.name_prefix,
            enabled_tools=server.enabled_tools,
            disabled_tools=server.disabled_tools,
        )
        try:
            tools.extend(bridge.build_bindings())
        except Exception as exc:  # noqa: BLE001
            logger.exception("failed to load mcp tools", exc_info=exc, extra={"server": server.name})


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

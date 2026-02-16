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
from minibot.llm.tools.calculator import CalculatorTool
from minibot.llm.tools.chat_memory import ChatMemoryTool
from minibot.llm.tools.file_storage import FileStorageTool
from minibot.llm.tools.http_client import HTTPClientTool
from minibot.llm.tools.mcp_bridge import MCPToolBridge
from minibot.llm.tools.user_memory import build_kv_tools
from minibot.llm.tools.python_exec import HostPythonExecTool
from minibot.llm.tools.scheduler import SchedulePromptTool
from minibot.llm.tools.time import CurrentTimeTool

if TYPE_CHECKING:  # pragma: no cover
    from minibot.app.scheduler_service import ScheduledPromptService


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
    managed_storage: LocalFileStorage | None = None
    chat_memory_tool = ChatMemoryTool(memory, max_history_messages=settings.memory.max_history_messages)
    tools.extend(chat_memory_tool.bindings())
    if settings.tools.kv_memory.enabled and kv_memory is not None:
        tools.extend(build_kv_tools(kv_memory))
    if settings.tools.http_client.enabled:
        http_tool = HTTPClientTool(settings.tools.http_client)
        tools.extend(http_tool.bindings())
    if settings.tools.time.enabled:
        current_time_tool = CurrentTimeTool(settings.tools.time.default_format)
        tools.extend(current_time_tool.bindings())
    if settings.tools.calculator.enabled:
        calculator_tool = CalculatorTool(
            default_scale=settings.tools.calculator.default_scale,
            max_expression_length=settings.tools.calculator.max_expression_length,
            max_exponent_abs=settings.tools.calculator.max_exponent_abs,
        )
        tools.extend(calculator_tool.bindings())
    if settings.tools.file_storage.enabled:
        managed_storage = LocalFileStorage(
            root_dir=settings.tools.file_storage.root_dir,
            max_write_bytes=settings.tools.file_storage.max_write_bytes,
        )
    if settings.tools.python_exec.enabled:
        python_exec_tool = HostPythonExecTool(settings.tools.python_exec, storage=managed_storage)
        tools.extend(python_exec_tool.bindings())
    if settings.tools.file_storage.enabled:
        file_storage = managed_storage or LocalFileStorage(
            root_dir=settings.tools.file_storage.root_dir,
            max_write_bytes=settings.tools.file_storage.max_write_bytes,
        )
        file_storage_tool = FileStorageTool(
            storage=file_storage,
            event_bus=event_bus,
        )
        tools.extend(file_storage_tool.bindings())
    if settings.scheduler.prompts.enabled and prompt_scheduler is not None:
        schedule_tool = SchedulePromptTool(
            prompt_scheduler,
            min_recurrence_interval_seconds=settings.scheduler.prompts.min_recurrence_interval_seconds,
        )
        tools.extend(schedule_tool.bindings())
    if settings.tools.mcp.enabled:
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
    if agent_registry is not None and llm_factory is not None and not agent_registry.is_empty():
        delegate_tool = AgentDelegateTool(
            registry=agent_registry,
            llm_factory=llm_factory,
            tools=tools,
            default_timeout_seconds=settings.orchestration.default_timeout_seconds,
            delegated_tool_call_policy=settings.orchestration.delegated_tool_call_policy,
            environment_prompt_fragment=environment_prompt_fragment,
            post_answer_gate_enabled=settings.runtime.post_answer_gate.enabled,
            post_answer_gate_scope=settings.runtime.post_answer_gate.scope,
            post_answer_gate_max_retries=settings.runtime.post_answer_gate.max_retries,
        )
        tools.extend(delegate_tool.bindings())
    return tools


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

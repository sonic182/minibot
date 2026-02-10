from __future__ import annotations

from typing import TYPE_CHECKING

from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.adapters.config.schema import Settings
from minibot.core.memory import KeyValueMemory, MemoryBackend
from minibot.app.event_bus import EventBus
from minibot.llm.tools.base import ToolBinding
from minibot.llm.tools.calculator import CalculatorTool
from minibot.llm.tools.chat_memory import ChatMemoryTool
from minibot.llm.tools.file_storage import FileStorageTool
from minibot.llm.tools.http_client import HTTPClientTool
from minibot.llm.tools.user_memory import build_kv_tools
from minibot.llm.tools.playwright import PlaywrightTool
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
) -> list[ToolBinding]:
    tools: list[ToolBinding] = []
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
    if settings.tools.python_exec.enabled:
        python_exec_tool = HostPythonExecTool(settings.tools.python_exec)
        tools.extend(python_exec_tool.bindings())
    if settings.tools.playwright.enabled:
        playwright_tool = PlaywrightTool(settings.tools.playwright)
        tools.extend(playwright_tool.bindings())
    if settings.tools.file_storage.enabled:
        file_storage = LocalFileStorage(
            root_dir=settings.tools.file_storage.root_dir,
            max_write_bytes=settings.tools.file_storage.max_write_bytes,
        )
        file_storage_tool = FileStorageTool(storage=file_storage, event_bus=event_bus)
        tools.extend(file_storage_tool.bindings())
    if settings.scheduler.prompts.enabled and prompt_scheduler is not None:
        schedule_tool = SchedulePromptTool(
            prompt_scheduler,
            min_recurrence_interval_seconds=settings.scheduler.prompts.min_recurrence_interval_seconds,
        )
        tools.extend(schedule_tool.bindings())
    return tools

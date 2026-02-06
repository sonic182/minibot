from __future__ import annotations

from __future__ import annotations

from typing import TYPE_CHECKING

from minibot.adapters.config.schema import Settings
from minibot.core.memory import KeyValueMemory, MemoryBackend
from minibot.llm.tools.base import ToolBinding
from minibot.llm.tools.chat_memory import ChatMemoryTool
from minibot.llm.tools.http_client import HTTPClientTool
from minibot.llm.tools.kv import build_kv_tools
from minibot.llm.tools.scheduler import SchedulePromptTool
from minibot.llm.tools.time import CurrentTimeTool

if TYPE_CHECKING:  # pragma: no cover
    from minibot.app.scheduler_service import ScheduledPromptService


def build_enabled_tools(
    settings: Settings,
    memory: MemoryBackend,
    kv_memory: KeyValueMemory | None,
    prompt_scheduler: ScheduledPromptService | None = None,
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
    if settings.scheduler.prompts.enabled and prompt_scheduler is not None:
        schedule_tool = SchedulePromptTool(
            prompt_scheduler,
            min_recurrence_interval_seconds=settings.scheduler.prompts.min_recurrence_interval_seconds,
        )
        tools.extend(schedule_tool.bindings())
    return tools

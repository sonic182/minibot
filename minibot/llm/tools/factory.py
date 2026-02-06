from __future__ import annotations

from minibot.adapters.config.schema import Settings
from minibot.core.memory import KeyValueMemory
from minibot.llm.tools.base import ToolBinding
from minibot.llm.tools.http_client import HTTPClientTool
from minibot.llm.tools.kv import build_kv_tools


def build_enabled_tools(settings: Settings, kv_memory: KeyValueMemory | None) -> list[ToolBinding]:
    tools: list[ToolBinding] = []
    if settings.kv_memory.enabled and kv_memory is not None:
        tools.extend(build_kv_tools(kv_memory))
    if settings.tools.http_client.enabled:
        http_tool = HTTPClientTool(settings.tools.http_client)
        tools.extend(http_tool.bindings())
    return tools

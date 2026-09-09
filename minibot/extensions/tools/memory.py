from __future__ import annotations

from minibot.adapters.memory.kv_sqlalchemy import SQLAlchemyKeyValueMemory
from minibot.app.extensions import ExtensionContext
from minibot.llm.tools.user_memory import build_kv_tools


class _MemoryService:
    def __init__(self, memory: SQLAlchemyKeyValueMemory) -> None:
        self._memory = memory

    async def start(self) -> None:
        await self._memory.initialize()

    async def stop(self) -> None:
        return None


def register(mb: ExtensionContext) -> None:
    if mb.entrypoint == "worker" or not mb.settings.tools.kv_memory.enabled:
        return
    memory = SQLAlchemyKeyValueMemory(mb.settings.tools.kv_memory)
    mb.add_tool(build_kv_tools(memory))
    mb.add_service(_MemoryService(memory))

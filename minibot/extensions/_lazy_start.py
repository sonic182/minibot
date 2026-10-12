from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from minibot.app.extensions import ExtensionService
from minibot.core.tools import ToolContext, ToolHandler, ToolPayload
from minibot.llm.tools.base import ToolBinding


def start_on_first_call(bindings: Sequence[ToolBinding], service: ExtensionService) -> list[ToolBinding]:
    lock = asyncio.Lock()
    started = False

    async def ensure_started() -> None:
        nonlocal started
        async with lock:
            if not started:
                await service.start()
                started = True

    def wrap(handler: ToolHandler) -> ToolHandler:
        async def handle(payload: ToolPayload, context: ToolContext) -> Any:
            await ensure_started()
            return await handler(payload, context)

        return handle

    return [ToolBinding(tool=binding.tool, handler=wrap(binding.handler)) for binding in bindings]

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

from minibot.llm.tools.base import ToolContext

TResult = TypeVar("TResult")
ActionHandler = Callable[[dict[str, Any], ToolContext], Awaitable[TResult]]


async def dispatch_action(  # noqa: UP047
    *,
    action: str,
    payload: dict[str, Any],
    context: ToolContext,
    handlers: Mapping[str, ActionHandler[TResult]],
    error_message: str,
) -> TResult:
    handler = handlers.get(action)
    if handler is None:
        raise ValueError(error_message)
    return await handler(payload, context)

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

from minibot.core.events import ToolCallEvent
from minibot.llm.services.tool_executor import canonical_tool_name
from minibot.llm.tools.base import ToolBinding, ToolContext, ToolPayload

if TYPE_CHECKING:  # pragma: no cover
    from minibot.app.event_bus import EventBus

_logger = logging.getLogger("minibot.tool_events")


def apply_tool_call_events(
    bindings: Sequence[ToolBinding],
    *,
    event_bus: EventBus | None,
) -> list[ToolBinding]:
    """Wrap tool handlers so every invocation emits a ``ToolCallEvent``.

    Wrapping the bindings keeps the three tool-execution call sites (provider client,
    generation loop, agent runtime) untouched, and covers delegated agents for free
    since they share the same binding list.
    """
    if event_bus is None:
        return list(bindings)
    return [_wrap(binding, event_bus=event_bus) for binding in bindings]


def _wrap(binding: ToolBinding, *, event_bus: EventBus) -> ToolBinding:
    tool_name = canonical_tool_name(binding.tool.name)

    async def handler(payload: ToolPayload, context: ToolContext) -> Any:
        argument_keys = sorted(str(key) for key in payload) if isinstance(payload, dict) else []
        await _publish(event_bus, tool_name, "started", context, argument_keys)
        try:
            result = await binding.handler(payload, context)
        except Exception as exc:
            await _publish(event_bus, tool_name, "failed", context, argument_keys, error=str(exc))
            raise
        await _publish(event_bus, tool_name, "completed", context, argument_keys)
        return result

    return ToolBinding(tool=binding.tool, handler=handler)


async def _publish(
    event_bus: EventBus,
    tool_name: str,
    phase: Literal["started", "completed", "failed"],
    context: ToolContext,
    argument_keys: list[str],
    error: str | None = None,
) -> None:
    """Telemetry must never break a tool: a stopped bus raises, and shutdown races are normal."""
    try:
        await event_bus.publish(
            ToolCallEvent(
                phase=phase,
                tool_name=tool_name,
                turn_id=context.turn_id,
                owner_id=context.owner_id,
                channel=context.channel,
                chat_id=context.chat_id,
                argument_keys=argument_keys,
                error=error,
            )
        )
    except Exception:  # noqa: BLE001
        _logger.debug(
            "tool call event publish failed",
            extra={"tool": tool_name, "phase": phase},
            exc_info=True,
        )

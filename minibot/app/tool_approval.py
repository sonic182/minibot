from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any
from uuid import uuid4

from minibot.app.event_bus import EventBus
from minibot.app.tool_policy_utils import matches_any, normalize_patterns
from minibot.core.events import ToolApprovalRequestedEvent, ToolApprovalResolvedEvent
from minibot.llm.services.tool_executor import canonical_tool_name, is_sensitive_argument_key
from minibot.llm.tools.base import ToolBinding, ToolContext, ToolPayload
from minibot.shared.errors import ToolInputError

Approver = Callable[[str, dict[str, Any], ToolContext], Awaitable[bool]]

_DETAIL_MAX_CHARS = 3000
_LAZY_MCP_CALL_SUFFIX = "__call_tool"
_logger = logging.getLogger("minibot.tool_approval")


def apply_tool_approval(
    bindings: Sequence[ToolBinding],
    *,
    patterns: Sequence[str],
    approve: Approver,
) -> list[ToolBinding]:
    """Gate the handlers whose tool name matches ``patterns`` behind ``approve``.

    A lazy-mode MCP ``call_tool`` binding is always wrapped, since the remote tool it runs is only
    known from the payload.
    """
    normalized = normalize_patterns(patterns)
    if not normalized:
        return list(bindings)
    return [
        _wrap(binding, normalized, approve) if _may_need_approval(binding.tool.name, normalized) else binding
        for binding in bindings
    ]


def effective_tool_name(tool_name: str, payload: ToolPayload) -> str:
    name = canonical_tool_name(tool_name)
    remote = payload.get("tool_name") if isinstance(payload, dict) else None
    if name.endswith(_LAZY_MCP_CALL_SUFFIX) and isinstance(remote, str) and remote:
        return f"{name.removesuffix(_LAZY_MCP_CALL_SUFFIX)}__{remote}"
    return name


def _may_need_approval(tool_name: str, patterns: Sequence[str]) -> bool:
    name = canonical_tool_name(tool_name)
    return name.endswith(_LAZY_MCP_CALL_SUFFIX) or matches_any(name, patterns)


def _wrap(binding: ToolBinding, patterns: Sequence[str], approve: Approver) -> ToolBinding:
    async def handler(payload: ToolPayload, context: ToolContext) -> Any:
        name = effective_tool_name(binding.tool.name, payload)
        if matches_any(name, patterns):
            is_lazy_call = name != canonical_tool_name(binding.tool.name)
            arguments = payload.get("arguments") if is_lazy_call else payload
            if not await approve(name, dict(arguments) if isinstance(arguments, dict) else {}, context):
                raise ToolInputError(
                    f"The user did not approve {name}. Do not retry it unless the user asks again.",
                    error_code="tool_approval:denied",
                )
        return await binding.handler(payload, context)

    return ToolBinding(tool=binding.tool, handler=handler)


async def request_tool_approval(
    event_bus: EventBus,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    channel: str | None,
    chat_id: int | None,
    timeout_seconds: float,
) -> bool:
    """Ask the user on Telegram and wait for the answer; anything but an explicit approval denies."""
    if channel != "telegram" or chat_id is None:
        _logger.warning("tool approval unavailable on this channel", extra={"tool": tool_name, "channel": channel})
        return False
    approval_id = uuid4().hex
    # Subscribe before publishing so a fast answer cannot slip past.
    subscription = event_bus.subscribe(types=(ToolApprovalResolvedEvent,))
    try:
        await event_bus.publish(
            ToolApprovalRequestedEvent(
                approval_id=approval_id,
                tool_name=tool_name,
                channel=channel,
                chat_id=chat_id,
                detail=format_approval_detail(arguments),
            )
        )
        async with asyncio.timeout(timeout_seconds):
            async for event in subscription:
                if isinstance(event, ToolApprovalResolvedEvent) and event.approval_id == approval_id:
                    _logger.info(
                        "tool approval resolved",
                        extra={"tool": tool_name, "approved": event.approved, "user_id": event.user_id},
                    )
                    return event.approved
    except TimeoutError:
        _logger.warning("tool approval timed out", extra={"tool": tool_name})
    finally:
        await subscription.close()
    # No user_id marks the expiry, so the channel can retire the buttons of the unanswered prompt.
    with contextlib.suppress(RuntimeError):
        await event_bus.publish(ToolApprovalResolvedEvent(approval_id=approval_id, approved=False))
    return False


def format_approval_detail(arguments: dict[str, Any]) -> str:
    text = json.dumps(_redact(arguments), indent=2, ensure_ascii=False, default=str)
    if len(text) > _DETAIL_MAX_CHARS:
        return f"{text[:_DETAIL_MAX_CHARS]}\n…(truncated)"
    return text


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "***" if is_sensitive_argument_key(str(key)) else _redact(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value

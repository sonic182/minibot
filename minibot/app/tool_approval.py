from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import unicodedata
from collections.abc import Awaitable, Iterator, Sequence
from typing import Any, Protocol
from uuid import uuid4

from minibot.app.event_bus import EventBus
from minibot.app.tool_policy_utils import matches_any, normalize_patterns
from minibot.core.events import ToolApprovalRequestedEvent, ToolApprovalResolvedEvent
from minibot.core.tools import ToolContext, ToolPayload
from minibot.llm.services.tool_executor import canonical_tool_name, is_sensitive_argument_key
from minibot.llm.tools.base import ToolBinding
from minibot.shared.errors import TOOL_APPROVAL_DENIED, ToolInputError


class Approver(Protocol):
    """Asks the user to approve ``tool_name``; ``detail`` replaces the text built from ``arguments``."""

    def __call__(
        self, tool_name: str, arguments: dict[str, Any], context: ToolContext, /, *, detail: str | None = None
    ) -> Awaitable[bool]: ...


_DETAIL_MAX_CHARS = 3000
_TRUNCATED_SUFFIX = "\n…(truncated)"
_VALUE_MAX_CHARS = 1000
_NO_ARGUMENTS = "(no arguments)"
_FLAGGED_LINE_MAX_CHARS = 300
NAME_MAX_CHARS = 200
_ESCAPED_CATEGORIES = {"Cc", "Cf", "Zl", "Zp"}
_ESCAPED_CHARS = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
_LAZY_MCP_CALL_SUFFIX = "__call_tool"
_logger = logging.getLogger("minibot.tool_approval")


class ApprovalWaitObserver(Protocol):
    """Told when a call starts and stops a wait bounded by its own timeout (an owner approval, a safety check)."""

    def begin(self) -> None: ...

    def end(self) -> None: ...


# A tool that runs other tools under its own deadline (codemode) installs an observer here so the owner's
# thinking time, and other waits with their own timeout, are not charged to that deadline. The context is
# copied into tasks the caller spawns.
approval_wait_observer: contextvars.ContextVar[ApprovalWaitObserver | None] = contextvars.ContextVar(
    "approval_wait_observer", default=None
)


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
        _wrap(binding, normalized, approve) if may_match_tool_patterns(binding.tool.name, normalized) else binding
        for binding in bindings
    ]


def effective_tool_name(tool_name: str, payload: ToolPayload) -> str:
    name = canonical_tool_name(tool_name)
    remote = payload.get("tool_name") if isinstance(payload, dict) else None
    if name.endswith(_LAZY_MCP_CALL_SUFFIX) and isinstance(remote, str) and remote.strip():
        return f"{name.removesuffix(_LAZY_MCP_CALL_SUFFIX)}__{remote.strip()}"
    return name


def may_match_tool_patterns(tool_name: str, patterns: Sequence[str]) -> bool:
    """Whether a call of ``tool_name`` can match ``patterns``: always for a lazy-mode MCP ``call_tool``."""
    name = canonical_tool_name(tool_name)
    return name.endswith(_LAZY_MCP_CALL_SUFFIX) or matches_any(name, patterns)


def _wrap(binding: ToolBinding, patterns: Sequence[str], approve: Approver) -> ToolBinding:
    async def handler(payload: ToolPayload, context: ToolContext) -> Any:
        name = effective_tool_name(binding.tool.name, payload)
        binding_name = canonical_tool_name(binding.tool.name)
        if matches_any(name, patterns) or matches_any(binding_name, patterns):
            is_lazy_call = name != binding_name
            arguments = payload.get("arguments") if is_lazy_call else payload
            if not await _approve_observed(
                approve, name, dict(arguments) if isinstance(arguments, dict) else {}, context
            ):
                raise ToolInputError(
                    f"The user did not approve {name}. Do not retry it unless the user asks again.",
                    error_code=TOOL_APPROVAL_DENIED,
                )
        return await binding.handler(payload, context)

    return ToolBinding(tool=binding.tool, handler=handler)


async def _approve_observed(
    approve: Approver, name: str, arguments: dict[str, Any], context: ToolContext, *, detail: str | None = None
) -> bool:
    """Run ``approve`` under the wait observer; ``detail`` is passed only when set, so approvers written for
    the three-argument call keep working."""
    ask = approve(name, arguments, context) if detail is None else approve(name, arguments, context, detail=detail)
    with outside_script_deadline():
        return await ask


@contextlib.contextmanager
def outside_script_deadline() -> Iterator[None]:
    """Report the enclosed wait to ``approval_wait_observer``, so an enclosing script deadline pauses for it."""
    observer = approval_wait_observer.get()
    if observer is None:
        yield
        return
    observer.begin()
    try:
        yield
    finally:
        observer.end()


def observed_approver(approve: Approver) -> Approver:
    """``approve`` reporting its wait to ``approval_wait_observer``, as the approval gate itself does."""

    async def observed(
        tool_name: str, arguments: dict[str, Any], context: ToolContext, /, *, detail: str | None = None
    ) -> bool:
        return await _approve_observed(approve, tool_name, arguments, context, detail=detail)

    return observed


async def request_tool_approval(
    event_bus: EventBus,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    channel: str | None,
    chat_id: int | None,
    timeout_seconds: float,
    supports_tool_approval: bool = False,
    detail: str | None = None,
    requester_user_id: int | None = None,
) -> bool:
    """Ask the user in the chat that started the call and wait; anything but an explicit approval denies."""
    if not supports_tool_approval or chat_id is None:
        _logger.warning("tool approval unavailable on this channel", extra={"tool": tool_name, "channel": channel})
        return False
    approval_id = uuid4().hex
    resolved = False
    subscription = event_bus.subscribe(types=(ToolApprovalResolvedEvent,))
    try:
        await event_bus.publish(
            ToolApprovalRequestedEvent(
                approval_id=approval_id,
                tool_name=escape_approval_text(tool_name)[:NAME_MAX_CHARS],
                channel=channel,
                chat_id=chat_id,
                detail=_cap_detail(detail) if detail is not None else format_approval_detail(arguments),
                requester_user_id=requester_user_id,
            )
        )
        async with asyncio.timeout(timeout_seconds):
            async for event in subscription:
                if isinstance(event, ToolApprovalResolvedEvent) and event.approval_id == approval_id:
                    resolved = True
                    _logger.info(
                        "tool approval resolved",
                        extra={"tool": tool_name, "approved": event.approved, "user_id": event.user_id},
                    )
                    return event.approved
    except TimeoutError:
        _logger.warning("tool approval timed out", extra={"tool": tool_name})
    finally:
        await subscription.close()
        if not resolved:
            with contextlib.suppress(Exception):
                await event_bus.publish(ToolApprovalResolvedEvent(approval_id=approval_id, approved=False))
    return False


def format_approval_detail(arguments: dict[str, Any], *, max_chars: int = _DETAIL_MAX_CHARS) -> str:
    """One block per argument, shortest first, each value capped on its own, the whole within ``max_chars``.

    Empty values (``None``, blank strings, empty containers) are dropped. The model controls argument
    order and size, so a long body must never push a recipient out of view. Unicode format characters
    (bidi overrides, zero-width) are escaped so text cannot be shown reordered or hidden.
    """
    pruned = _prune(redact_arguments(arguments))
    blocks = sorted((_render_entry(str(key), value, "") for key, value in pruned.items()), key=len)
    return _cap_detail("\n".join(f"- {block}" for block in blocks) or _NO_ARGUMENTS, max_chars)


def format_flagged_detail(
    headline: str, reason: str, instruction: str, arguments: dict[str, Any] | None, *, excerpt: str | None = None
) -> str:
    """An approval prompt for a call or result a check flagged: what and why, then the call's arguments.

    ``reason`` comes from a model that may have read attacker-written text, and ``excerpt`` (the start of a
    flagged result) is that text, so each is escaped onto one line and capped, like ``headline``, and the
    arguments get the rest of the prompt's budget: neither can fake an argument line or push the real
    arguments out of view.
    """
    lines = [_cap_line(headline), f"Reason: {_cap_line(reason)}"]
    if excerpt is not None:
        lines.append(f"Excerpt: {_cap_line(excerpt.strip()[: _FLAGGED_LINE_MAX_CHARS + 1])}")
    lines.append(_cap_line(instruction))
    header = "\n".join(lines)
    if arguments is None:
        return header
    budget = _DETAIL_MAX_CHARS - len(header) - len(_TRUNCATED_SUFFIX) - 2
    return f"{header}\n\n{format_approval_detail(arguments, max_chars=budget)}"


def _cap_line(text: str) -> str:
    line = escape_approval_text(text.strip())
    if len(line) <= _FLAGGED_LINE_MAX_CHARS:
        return line
    return f"{line[:_FLAGGED_LINE_MAX_CHARS]}…"


def _prune(value: Any) -> Any:
    if isinstance(value, dict):
        pruned = {key: _prune(item) for key, item in value.items()}
        return {key: item for key, item in pruned.items() if not _is_empty(item)}
    if isinstance(value, list):
        return [item for item in map(_prune, value) if not _is_empty(item)]
    return value


def _is_empty(value: Any) -> bool:
    return (
        value is None
        or (isinstance(value, str) and not value.strip())
        or (isinstance(value, (dict, list)) and not value)
    )


def _render_entry(key: str, value: Any, indent: str) -> str:
    label = f"{indent}{escape_approval_text(key)}:"
    child_indent = f"{indent}  "
    if isinstance(value, dict):
        return "\n".join([label, *(_render_entry(str(name), child, child_indent) for name, child in value.items())])
    if isinstance(value, list):
        return "\n".join([label, *(_render_item(item, child_indent) for item in value)])
    return f"{label} {_render_value(value)}"


def _render_item(item: Any, indent: str) -> str:
    if isinstance(item, dict):
        body = "\n".join(_render_entry(str(name), child, f"{indent}  ") for name, child in item.items())
        return f"{indent}- {body[len(indent) + 2 :]}"
    return f"{indent}- {_render_value(item)}"


def _cap_detail(text: str, max_chars: int = _DETAIL_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}{_TRUNCATED_SUFFIX}"


def _render_value(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) > _VALUE_MAX_CHARS:
        text = f"{text[:_VALUE_MAX_CHARS]}…(+{len(text) - _VALUE_MAX_CHARS} chars)"
    return escape_approval_text(text)


def escape_approval_text(text: str) -> str:
    """Keep each argument on one visible line: line breaks inside a value would let it fake extra
    argument lines (a spoofed ``to:`` under the real one)."""
    return "".join(
        _ESCAPED_CHARS.get(char)
        or (f"\\u{ord(char):04x}" if unicodedata.category(char) in _ESCAPED_CATEGORIES else char)
        for char in text
    )


def redact_arguments(value: Any) -> Any:
    """``value`` with every sensitive key's value (``authorization``, ``token``, ...) replaced by ``***``."""
    if isinstance(value, dict):
        return {
            str(key): "***" if is_sensitive_argument_key(str(key)) else redact_arguments(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_arguments(item) for item in value]
    return value

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from llm_async.models import Tool
from pydantic import ValidationError

from minibot.adapters.config.schema import ToolApprovalConfig
from minibot.app.event_bus import EventBus
from minibot.app.tool_approval import (
    _cap_detail,
    apply_tool_approval,
    format_approval_detail,
    request_tool_approval,
)
from minibot.core.channels import ChannelCapabilities
from minibot.core.events import ToolApprovalRequestedEvent, ToolApprovalResolvedEvent
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.shared.errors import ToolInputError

_CONTEXT = ToolContext(
    channel="telegram",
    chat_id=1,
    channel_capabilities=ChannelCapabilities(supports_tool_approval=True),
)


def _binding(name: str, handler: AsyncMock) -> ToolBinding:
    return ToolBinding(tool=Tool(name=name, description="", parameters={}), handler=handler)


async def _call(
    binding_name: str,
    payload: dict[str, Any],
    *,
    approved: bool,
    patterns: list[str] | None = None,
) -> tuple[AsyncMock, AsyncMock]:
    handler = AsyncMock(return_value="ran")
    approve = AsyncMock(return_value=approved)
    [wrapped] = apply_tool_approval(
        [_binding(binding_name, handler)], patterns=patterns or ["mcp_mail__smtp_*"], approve=approve
    )
    await wrapped.handler(payload, _CONTEXT)
    return handler, approve


@pytest.mark.asyncio
async def test_unmatched_tool_runs_without_asking() -> None:
    handler, approve = await _call("mcp_mail__imap_get_message", {"id": 1}, approved=False)

    handler.assert_awaited_once()
    approve.assert_not_awaited()


@pytest.mark.asyncio
async def test_approved_tool_runs() -> None:
    handler, approve = await _call("mcp_mail__smtp_send_message", {"to": "a@b.c"}, approved=True)

    approve.assert_awaited_once_with("mcp_mail__smtp_send_message", {"to": "a@b.c"}, _CONTEXT)
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_denied_tool_raises_typed_error_and_does_not_run() -> None:
    with pytest.raises(ToolInputError) as exc_info:
        await _call("mcp_mail__smtp_send_message", {"to": "a@b.c"}, approved=False)

    assert exc_info.value.error_code == "tool_approval:denied"


@pytest.mark.asyncio
@pytest.mark.parametrize("remote_name", ["smtp_forward_message", " smtp_forward_message\n"])
async def test_lazy_mcp_call_is_gated_by_the_remote_tool_name(remote_name: str) -> None:
    payload = {"tool_name": remote_name, "arguments": {"to": "x@evil.com"}}

    handler, approve = await _call("mcp_mail__call_tool", payload, approved=True)

    approve.assert_awaited_once_with("mcp_mail__smtp_forward_message", {"to": "x@evil.com"}, _CONTEXT)
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_pattern_on_the_lazy_binding_name_gates_every_remote_call() -> None:
    payload = {"tool_name": "imap_get_message", "arguments": {}}

    _, approve = await _call("mcp_mail__call_tool", payload, approved=True, patterns=["mcp_mail__call_tool"])

    approve.assert_awaited_once()


def test_detail_keeps_every_argument_visible_and_escapes_format_characters() -> None:
    detail = format_approval_detail({"body": "x" * 5000, "to": "attacker‮@evil.com", "api_key": "k"})

    lines = detail.splitlines()
    assert lines[0] == "- api_key: ***"
    assert lines[1] == "- to: attacker\\u202e@evil.com"
    assert lines[2].endswith("…(+4000 chars)")


def test_detail_drops_empty_values_and_indents_nested_ones() -> None:
    detail = format_approval_detail(
        {
            "cc": None,
            "bcc": "  ",
            "references": [],
            "to": ["a@b.c"],
            "attachments": [{"content_base64": None, "file_path": "/x", "filename": "a.webp"}],
        }
    )

    assert detail == "- to:\n  - a@b.c\n- attachments:\n  - file_path: /x\n    filename: a.webp"


def test_detail_values_cannot_fake_extra_argument_lines() -> None:
    detail = format_approval_detail(
        {"to": "attacker@evil.com", "body": "Report attached.\n\nto: boss@company.com\u2028cc: x@y.z"}
    )

    assert detail.splitlines() == [
        "- to: attacker@evil.com",
        "- body: Report attached.\\n\\nto: boss@company.com\\u2028cc: x@y.z",
    ]


@pytest.mark.asyncio
async def test_tool_name_in_the_request_is_escaped_and_capped() -> None:
    bus = EventBus()
    requested = bus.subscribe(types=(ToolApprovalRequestedEvent,))

    await request_tool_approval(
        bus,
        tool_name="mcp_mail__smtp_send\n\nRead-only lookup" + "x" * 500,
        arguments={},
        channel="telegram",
        chat_id=1,
        timeout_seconds=0.01,
        supports_tool_approval=True,
    )

    event = await asyncio.wait_for(anext(aiter(requested)), timeout=1)
    assert isinstance(event, ToolApprovalRequestedEvent)
    assert "\n" not in event.tool_name
    assert len(event.tool_name) == 200


def test_unknown_approval_config_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolApprovalConfig(require_approvals=["mcp_mail__*"])  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_cancelled_request_announces_expiry() -> None:
    bus = EventBus()
    resolved = bus.subscribe(types=(ToolApprovalResolvedEvent,))
    request = asyncio.create_task(_request(bus, timeout=10))
    await asyncio.sleep(0.01)

    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request

    expiry = await asyncio.wait_for(anext(aiter(resolved)), timeout=1)
    assert isinstance(expiry, ToolApprovalResolvedEvent)
    assert expiry.user_id is None


async def _answer_first_request(bus: EventBus, *, approved: bool) -> None:
    subscription = bus.subscribe(types=(ToolApprovalRequestedEvent,))
    async for event in subscription:
        assert isinstance(event, ToolApprovalRequestedEvent)
        assert "password: ***" in event.detail
        await bus.publish(ToolApprovalResolvedEvent(approval_id=event.approval_id, approved=approved, user_id=2))
        break
    await subscription.close()


async def _request(
    bus: EventBus,
    *,
    channel: str = "telegram",
    timeout: float = 1,
    supports_tool_approval: bool = True,
) -> bool:
    return await request_tool_approval(
        bus,
        tool_name="mcp_mail__smtp_send_message",
        arguments={"to": "a@b.c", "password": "hunter2"},
        channel=channel,
        chat_id=1,
        timeout_seconds=timeout,
        supports_tool_approval=supports_tool_approval,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_request_returns_the_user_answer(approved: bool) -> None:
    bus = EventBus()
    answerer = asyncio.create_task(_answer_first_request(bus, approved=approved))
    await asyncio.sleep(0)

    assert await _request(bus) is approved
    await answerer


@pytest.mark.asyncio
async def test_request_times_out_as_denied_and_announces_expiry() -> None:
    bus = EventBus()
    resolved = bus.subscribe(types=(ToolApprovalResolvedEvent,))

    assert await _request(bus, timeout=0.01) is False

    expiry = await asyncio.wait_for(anext(aiter(resolved)), timeout=1)
    assert isinstance(expiry, ToolApprovalResolvedEvent)
    assert expiry.approved is False
    assert expiry.user_id is None


@pytest.mark.asyncio
async def test_request_outside_telegram_is_denied_without_asking() -> None:
    bus = EventBus()
    requested = bus.subscribe(types=(ToolApprovalRequestedEvent,))

    assert await _request(bus, channel="console", supports_tool_approval=False) is False
    assert requested._queue.empty()


@pytest.mark.asyncio
async def test_detail_supplied_by_a_worker_is_capped() -> None:
    bus = EventBus()
    requested = bus.subscribe(types=(ToolApprovalRequestedEvent,))

    await request_tool_approval(
        bus,
        tool_name="t",
        arguments={},
        channel="telegram",
        chat_id=1,
        timeout_seconds=0.01,
        supports_tool_approval=True,
        detail="x" * 10_000,
    )

    event = await asyncio.wait_for(anext(aiter(requested)), timeout=1)
    assert isinstance(event, ToolApprovalRequestedEvent)
    assert event.detail == "x" * 3000 + "\n…(truncated)"


@pytest.mark.parametrize("extra", [0, 50])
def test_capping_an_already_truncated_detail_keeps_one_marker(extra: int) -> None:
    detail = format_approval_detail({key: key * 900 for key in "abcd"})
    worker_detail = detail[: -len("\n…(truncated)")] + "q" * extra + "\n…(truncated)"

    capped = _cap_detail(worker_detail)

    assert capped.count("…(truncated)") == 1
    assert capped.endswith("\n…(truncated)")
    assert len(capped) <= 3000 + len("\n…(truncated)")

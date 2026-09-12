from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from minibot.core.channels import ChannelFileResponse, ChannelMessage, ChannelResponse


class BaseEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    event_type: str


class MessageEvent(BaseEvent):
    event_type: str = "message"
    message: ChannelMessage


class OutboundEvent(BaseEvent):
    event_type: str = "outbound"
    response: ChannelResponse


class OutboundFileEvent(BaseEvent):
    event_type: str = "outbound_file"
    response: ChannelFileResponse


class OutboundFormatRepairEvent(BaseEvent):
    event_type: str = "outbound_format_repair"
    response: ChannelResponse
    parse_error: str
    attempt: int = 1
    chat_id: int
    channel: str
    user_id: int | None = None


class SystemEvent(BaseEvent):
    event_type: str = "system"
    payload: dict | None = None


class TurnStartedEvent(BaseEvent):
    """Emitted when the dispatcher begins processing an inbound message."""

    event_type: str = "turn_started"
    turn_id: str
    channel: str
    chat_id: int | None = None
    user_id: int | None = None


class TurnCompletedEvent(BaseEvent):
    """Emitted once a turn is fully done.

    Fires after the response has been dispatched to the channel, or deliberately
    suppressed when ``should_reply`` is false — never before.
    """

    event_type: str = "turn_completed"
    turn_id: str
    channel: str
    chat_id: int | None = None
    should_reply: bool = True
    llm_provider: str | None = None
    llm_model: str | None = None
    token_trace: dict[str, Any] = Field(default_factory=dict)
    compaction_performed: bool | None = None


class TurnFailedEvent(BaseEvent):
    """Emitted when a turn raised before producing a response."""

    event_type: str = "turn_failed"
    turn_id: str
    channel: str
    chat_id: int | None = None
    error: str


class ReasoningEvent(BaseEvent):
    """Emitted as soon as one provider step returns reasoning, before the turn finishes.

    Reasoning is also attached to the final response metadata; this event exists so a channel can
    show thinking while the turn is still running instead of only after it completes.
    """

    event_type: str = "reasoning"
    text: str
    step: int
    turn_id: str | None = None
    owner_id: str | None = None
    channel: str | None = None
    chat_id: int | None = None


class ToolCallEvent(BaseEvent):
    """Emitted around every tool handler invocation.

    ``detail`` is already redacted and clipped by ``minibot/shared/tool_call_display.py`` before
    this event is published — the raw argument payload (which can hold credentials, e.g.
    ``http_request`` headers, ``bash`` env, ``python_execute`` code) never leaves the tool-execution
    layer. Safe to log, persist, or forward to any subscriber, including extensions.
    """

    event_type: str = "tool_call"
    phase: Literal["started", "completed", "failed"]
    tool_name: str
    turn_id: str | None = None
    owner_id: str | None = None
    channel: str | None = None
    chat_id: int | None = None
    detail: str = ""
    error: str | None = None

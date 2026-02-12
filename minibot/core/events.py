from __future__ import annotations

from uuid import uuid4
from typing import Optional

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
    payload: Optional[dict] = None

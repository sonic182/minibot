from __future__ import annotations

from uuid import uuid4
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .channels import ChannelMessage, ChannelResponse


class BaseEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    event_type: str


class MessageEvent(BaseEvent):
    event_type: Literal["message"] = "message"
    message: ChannelMessage


class OutboundEvent(BaseEvent):
    event_type: Literal["outbound"] = "outbound"
    response: ChannelResponse


class SystemEvent(BaseEvent):
    event_type: Literal["system"] = "system"
    payload: Optional[dict] = None

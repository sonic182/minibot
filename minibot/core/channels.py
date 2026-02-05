from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ChannelMessage(BaseModel):
    channel: str
    user_id: Optional[int]
    chat_id: Optional[int]
    message_id: Optional[int]
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChannelResponse(BaseModel):
    channel: str
    chat_id: int
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

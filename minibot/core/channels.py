from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ChannelMessage(BaseModel):
    channel: str
    user_id: Optional[int]
    chat_id: Optional[int]
    message_id: Optional[int]
    text: str
    attachments: list[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChannelResponse(BaseModel):
    channel: str
    chat_id: int
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChannelMediaResponse(BaseModel):
    channel: str
    chat_id: int
    media_type: str
    file_path: str
    caption: str | None = None
    filename: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

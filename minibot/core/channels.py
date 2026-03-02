from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ChannelMessage(BaseModel):
    channel: str
    user_id: Optional[int]
    chat_id: Optional[int]
    message_id: Optional[int]
    text: str
    attachments: list[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class IncomingFileRef(BaseModel):
    path: str
    filename: str
    mime: str
    size_bytes: int
    source: str
    message_id: int | None = None
    caption: str | None = None


class RenderableResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["text", "html", "markdown"] = "text"
    text: str = Field(validation_alias=AliasChoices("content", "text"), serialization_alias="content")
    meta: Dict[str, Any] = Field(default_factory=dict)


class ChannelResponse(BaseModel):
    channel: str
    chat_id: int
    text: str
    render: RenderableResponse | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChannelFileResponse(BaseModel):
    channel: str
    chat_id: int
    file_path: str
    caption: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

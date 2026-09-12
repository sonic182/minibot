from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ChannelMessage(BaseModel):
    """One inbound message from a channel adapter.

    - ``channel`` — the adapter name, e.g. ``"telegram"`` or ``"console"``.
    - ``chat_id`` — identifies a chat session and the delivery target for the reply. This is the
      conversation scope: ``session_identifier`` keys history on it, so each chat keeps its own
      history.
    - ``user_id`` — the channel's own sender identifier, used for channel authorization and audit
      context only. It is not a MiniBot account and never a data namespace.

    MiniBot assists a single owner, configured once as ``[runtime].owner_id``. Ownership is never
    derived from ``user_id``: every chat session belongs to that same owner.
    """

    channel: str
    user_id: int | None
    chat_id: int | None
    message_id: int | None
    text: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncomingFileRef(BaseModel):
    path: str
    filename: str
    mime: str
    size_bytes: int
    source: str
    message_id: int | None = None
    caption: str | None = None
    duration_seconds: int | None = None


class RenderableResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: Literal["text", "html", "markdown"] = "text"
    text: str = Field(validation_alias=AliasChoices("content", "text"), serialization_alias="content")
    meta: dict[str, Any] = Field(default_factory=dict)


class ChannelResponse(BaseModel):
    channel: str
    chat_id: int
    text: str
    render: RenderableResponse | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChannelFileResponse(BaseModel):
    channel: str
    chat_id: int
    file_path: str
    caption: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


PartType = Literal["text", "image", "file", "json"]
MessageRole = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class MessagePart:
    type: PartType
    text: str | None = None
    source: dict[str, str] | None = None
    mime: str | None = None
    filename: str | None = None
    value: dict[str, Any] | list[Any] | str | int | float | bool | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": self.type}
        if self.text is not None:
            payload["text"] = self.text
        if self.source is not None:
            payload["source"] = dict(self.source)
        if self.mime is not None:
            payload["mime"] = self.mime
        if self.filename is not None:
            payload["filename"] = self.filename
        if self.value is not None:
            payload["value"] = self.value
        return payload


@dataclass(frozen=True)
class AgentMessage:
    role: MessageRole
    content: list[MessagePart]
    name: str | None = None
    tool_call_id: str | None = None
    raw_content: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": self.role,
            "content": [part.to_dict() for part in self.content],
        }
        if self.name is not None:
            payload["name"] = self.name
        if self.tool_call_id is not None:
            payload["tool_call_id"] = self.tool_call_id
        if self.raw_content is not None:
            payload["raw_content"] = self.raw_content
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload


@dataclass
class AgentState:
    messages: list[AgentMessage] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AppendMessageDirective:
    type: Literal["append_message"]
    message: AgentMessage


Directive = AppendMessageDirective


@dataclass(frozen=True)
class ToolResult:
    content: Any
    directives: list[Directive] = field(default_factory=list)


@dataclass(frozen=True)
class RuntimeLimits:
    max_steps: int = 8
    max_tool_calls: int = 12
    timeout_seconds: int = 60

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class FakeToolCall:
    id: str
    type: str = "function"
    function: dict[str, Any] | None = None
    name: str | None = None
    input: dict[str, Any] | None = None


@dataclass
class FakeMessage:
    content: Any
    tool_calls: list[FakeToolCall] | None = None

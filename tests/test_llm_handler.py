from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

import pytest

from minibot.core.channels import ChannelMessage
from minibot.core.events import MessageEvent
from minibot.core.memory import MemoryEntry
from minibot.app.handlers.llm_handler import LLMMessageHandler, resolve_owner_id
from minibot.llm.provider_factory import LLMClient
from minibot.shared.utils import session_id_for


def _message(**overrides):
    base = {
        "channel": "telegram",
        "user_id": None,
        "chat_id": None,
        "message_id": None,
        "text": "hi",
        "metadata": {},
    }
    base.update(overrides)
    return ChannelMessage(**base)


def test_resolve_owner_prefers_default():
    message = _message(user_id=123)
    assert resolve_owner_id(message, "primary") == "primary"


def test_resolve_owner_uses_user_id():
    message = _message(user_id=42)
    assert resolve_owner_id(message, None) == "42"


def test_resolve_owner_uses_chat():
    message = _message(chat_id=987)
    assert resolve_owner_id(message, None) == "987"


def test_resolve_owner_falls_back_to_session_id():
    message = _message()
    expected = session_id_for(message)
    assert resolve_owner_id(message, None) == expected


class StubMemory:
    def __init__(self) -> None:
        self._store: dict[str, list[MemoryEntry]] = {}

    async def append_history(self, session_id: str, role: str, content: str) -> None:
        entry = MemoryEntry(role=role, content=content, created_at=datetime.now(timezone.utc))
        self._store.setdefault(session_id, []).append(entry)

    async def get_history(self, session_id: str, limit: int = 32) -> list[MemoryEntry]:
        return self._store.get(session_id, [])[-limit:]


class StubLLMClient:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    async def generate(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append({"args": args, "kwargs": kwargs})
        return self.payload


def _handler(llm_payload: Any) -> LLMMessageHandler:
    memory = StubMemory()
    client = StubLLMClient(llm_payload)
    return LLMMessageHandler(memory=memory, llm_client=cast(LLMClient, client))


def _message_event(text: str = "hi") -> MessageEvent:
    return MessageEvent(message=_message(text=text, user_id=1, chat_id=1))


@pytest.mark.asyncio
async def test_handler_returns_structured_answer() -> None:
    handler = _handler({"answer": "hello", "should_answer_to_user": True})
    response = await handler.handle(_message_event("ping"))
    assert response.text == "hello"
    assert response.metadata.get("should_reply") is True


@pytest.mark.asyncio
async def test_handler_respects_silent_flag() -> None:
    handler = _handler({"answer": "internal", "should_answer_to_user": False})
    response = await handler.handle(_message_event("ping"))
    assert response.text == "internal"
    assert response.metadata.get("should_reply") is False


@pytest.mark.asyncio
async def test_handler_falls_back_for_plain_text() -> None:
    handler = _handler("plain text")
    response = await handler.handle(_message_event("ping"))
    assert response.text == "plain text"
    assert response.metadata.get("should_reply") is True

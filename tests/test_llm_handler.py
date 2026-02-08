from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

import pytest

from minibot.core.channels import ChannelMessage
from minibot.core.events import MessageEvent
from minibot.core.memory import MemoryEntry
from minibot.app.handlers.llm_handler import LLMMessageHandler, resolve_owner_id
from minibot.llm.provider_factory import LLMClient, LLMGeneration
from minibot.shared.utils import session_id_for


def _message(**overrides):
    base = {
        "channel": "telegram",
        "user_id": None,
        "chat_id": None,
        "message_id": None,
        "text": "hi",
        "attachments": [],
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
        self.trim_calls: list[tuple[str, int]] = []

    async def append_history(self, session_id: str, role: str, content: str) -> None:
        entry = MemoryEntry(role=role, content=content, created_at=datetime.now(timezone.utc))
        self._store.setdefault(session_id, []).append(entry)

    async def get_history(self, session_id: str, limit: int = 32) -> list[MemoryEntry]:
        return self._store.get(session_id, [])[-limit:]

    async def count_history(self, session_id: str) -> int:
        return len(self._store.get(session_id, []))

    async def trim_history(self, session_id: str, keep_latest: int) -> int:
        self.trim_calls.append((session_id, keep_latest))
        entries = self._store.get(session_id, [])
        if keep_latest <= 0:
            removed = len(entries)
            self._store[session_id] = []
            return removed
        if len(entries) <= keep_latest:
            return 0
        removed = len(entries) - keep_latest
        self._store[session_id] = entries[-keep_latest:]
        return removed


class StubLLMClient:
    def __init__(self, payload: Any, response_id: str | None = None, is_responses: bool = False) -> None:
        self.payload = payload
        self.response_id = response_id
        self.calls: list[dict[str, Any]] = []
        self._is_responses = is_responses

    async def generate(self, *args: Any, **kwargs: Any) -> LLMGeneration:
        self.calls.append({"args": args, "kwargs": kwargs})
        return LLMGeneration(self.payload, self.response_id)

    def is_responses_provider(self) -> bool:
        return self._is_responses


def _handler(
    llm_payload: Any,
    *,
    response_id: str | None = None,
    responses_provider: bool = False,
) -> tuple[LLMMessageHandler, StubLLMClient, StubMemory]:
    memory = StubMemory()
    client = StubLLMClient(llm_payload, response_id=response_id, is_responses=responses_provider)
    handler = LLMMessageHandler(memory=memory, llm_client=cast(LLMClient, client))
    return handler, client, memory


def _message_event(text: str = "hi") -> MessageEvent:
    return MessageEvent(message=_message(text=text, user_id=1, chat_id=1))


@pytest.mark.asyncio
async def test_handler_returns_structured_answer() -> None:
    handler, stub_client, _ = _handler(
        {"answer": "hello", "should_answer_to_user": True},
        responses_provider=True,
        response_id="resp-1",
    )
    response = await handler.handle(_message_event("ping"))
    assert response.text == "hello"
    assert response.metadata.get("should_reply") is True
    call = stub_client.calls[-1]
    assert call["kwargs"].get("prompt_cache_key") == "telegram:1"
    assert call["kwargs"].get("previous_response_id") is None


@pytest.mark.asyncio
async def test_handler_respects_silent_flag() -> None:
    handler, stub_client, _ = _handler(
        {"answer": "internal", "should_answer_to_user": False},
        responses_provider=True,
        response_id="resp-2",
    )
    response = await handler.handle(_message_event("ping"))
    assert response.text == "internal"
    assert response.metadata.get("should_reply") is False
    assert stub_client.calls[-1]["kwargs"].get("prompt_cache_key") == "telegram:1"


@pytest.mark.asyncio
async def test_handler_does_not_reuse_previous_response_id() -> None:
    handler, stub_client, _ = _handler(
        {"answer": "hello", "should_answer_to_user": True},
        responses_provider=True,
        response_id="resp-1",
    )
    await handler.handle(_message_event("ping"))
    stub_client.response_id = "resp-2"
    await handler.handle(_message_event("ping"))
    assert stub_client.calls[-1]["kwargs"].get("previous_response_id") is None


@pytest.mark.asyncio
async def test_handler_falls_back_for_plain_text() -> None:
    handler, _, _ = _handler("plain text")
    response = await handler.handle(_message_event("ping"))
    assert response.text == "plain text"
    assert response.metadata.get("should_reply") is True


@pytest.mark.asyncio
async def test_handler_extracts_result_from_tool_like_payload_string() -> None:
    handler, _, _ = _handler("{'ok': True, 'tool': 'current_datetime', 'result': '2026-02-08T13:09:01Z'}")

    response = await handler.handle(_message_event("ping"))

    assert response.text == "2026-02-08T13:09:01Z"
    assert response.metadata.get("should_reply") is True


@pytest.mark.asyncio
async def test_handler_trims_history_when_limit_is_configured() -> None:
    memory = StubMemory()
    client = StubLLMClient({"answer": "ok", "should_answer_to_user": True})
    handler = LLMMessageHandler(memory=memory, llm_client=cast(LLMClient, client), max_history_messages=2)

    await handler.handle(_message_event("one"))
    await handler.handle(_message_event("two"))

    event = _message_event("two")
    session_id = session_id_for(event.message)
    assert await memory.count_history(session_id) == 2
    assert memory.trim_calls


@pytest.mark.asyncio
async def test_handler_builds_multimodal_input_for_responses_provider() -> None:
    memory = StubMemory()
    client = StubLLMClient({"answer": "done", "should_answer_to_user": True}, is_responses=True)
    handler = LLMMessageHandler(memory=memory, llm_client=cast(LLMClient, client))
    event = MessageEvent(
        message=_message(
            text="what is in this image?",
            attachments=[
                {
                    "type": "input_image",
                    "image_url": "data:image/jpeg;base64,QUJD",
                }
            ],
            user_id=1,
            chat_id=1,
        )
    )

    response = await handler.handle(event)

    assert response.text == "done"
    generate_call = client.calls[-1]["kwargs"]
    user_content = generate_call.get("user_content")
    assert isinstance(user_content, list)
    assert user_content[0]["type"] == "input_text"
    assert user_content[1]["type"] == "input_image"
    session_id = session_id_for(event.message)
    stored = [entry.content for entry in memory._store[session_id]]
    assert any("Attachments: image" in value for value in stored)
    assert all("base64" not in value for value in stored)


@pytest.mark.asyncio
async def test_handler_rejects_media_for_non_responses_provider() -> None:
    memory = StubMemory()
    client = StubLLMClient({"answer": "unused", "should_answer_to_user": True}, is_responses=False)
    handler = LLMMessageHandler(memory=memory, llm_client=cast(LLMClient, client))
    event = MessageEvent(
        message=_message(
            text="",
            attachments=[{"type": "input_file", "filename": "a.pdf", "file_data": "QUJD"}],
            user_id=1,
            chat_id=1,
        )
    )

    response = await handler.handle(event)

    assert "openai_responses" in response.text
    assert not client.calls

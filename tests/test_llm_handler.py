from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, cast

import pytest

from minibot.core.channels import ChannelMessage, ChannelResponse, RenderableResponse
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart
from minibot.core.events import MessageEvent
from minibot.core.memory import MemoryEntry
from minibot.app.agent_runtime import RuntimeResult
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

    async def get_history(self, session_id: str, limit: int | None = None) -> list[MemoryEntry]:
        entries = self._store.get(session_id, [])
        if limit is None:
            return list(entries)
        return entries[-limit:]

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
    def __init__(
        self,
        payload: Any,
        response_id: str | None = None,
        is_responses: bool = False,
        provider: str = "openai",
        system_prompt: str = "You are Minibot, a helpful assistant.",
        prompts_dir: str = "./prompts",
        total_tokens: int | None = None,
        responses_state_mode: str = "full_messages",
        prompt_cache_enabled: bool = True,
    ) -> None:
        self.payload = payload
        self.response_id = response_id
        self.calls: list[dict[str, Any]] = []
        self._is_responses = is_responses
        self._provider = provider
        self._system_prompt = system_prompt
        self._prompts_dir = prompts_dir
        self.total_tokens = total_tokens
        self._responses_state_mode = responses_state_mode
        self._prompt_cache_enabled = prompt_cache_enabled
        self.compact_calls: list[dict[str, Any]] = []
        self.compact_response_id = "cmp-1"
        self.compact_output: list[dict[str, Any]] = [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "ok"}],
            }
        ]
        self.compact_total_tokens: int | None = total_tokens

    async def generate(self, *args: Any, **kwargs: Any) -> LLMGeneration:
        self.calls.append({"args": args, "kwargs": kwargs})
        return LLMGeneration(self.payload, self.response_id, total_tokens=self.total_tokens)

    def is_responses_provider(self) -> bool:
        return self._is_responses

    def supports_media_inputs(self) -> bool:
        return self._provider in {"openai_responses", "openai", "openrouter"}

    def media_input_mode(self) -> str:
        if self._provider == "openai_responses":
            return "responses"
        if self._provider in {"openai", "openrouter"}:
            return "chat_completions"
        return "none"

    def system_prompt(self) -> str:
        return self._system_prompt

    def prompts_dir(self) -> str:
        return self._prompts_dir

    def responses_state_mode(self) -> str:
        return self._responses_state_mode

    def prompt_cache_enabled(self) -> bool:
        return self._prompt_cache_enabled

    async def compact_response(
        self,
        *,
        previous_response_id: str,
        prompt_cache_key: str | None = None,
    ) -> Any:
        from minibot.llm.provider_factory import LLMCompaction

        self.compact_calls.append(
            {
                "previous_response_id": previous_response_id,
                "prompt_cache_key": prompt_cache_key,
            }
        )
        return LLMCompaction(
            response_id=self.compact_response_id,
            output=self.compact_output,
            total_tokens=self.compact_total_tokens,
        )


class FailingLLMClient(StubLLMClient):
    async def generate(self, *args: Any, **kwargs: Any) -> LLMGeneration:
        _ = args, kwargs
        raise TimeoutError("request timed out")


class StubRuntime:
    def __init__(self, responses: list[RuntimeResult]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> RuntimeResult:
        self.calls.append(kwargs)
        return self._responses.pop(0)


def _handler(
    llm_payload: Any,
    *,
    response_id: str | None = None,
    responses_provider: bool = False,
    provider: str = "openai",
) -> tuple[LLMMessageHandler, StubLLMClient, StubMemory]:
    memory = StubMemory()
    client = StubLLMClient(llm_payload, response_id=response_id, is_responses=responses_provider, provider=provider)
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
    token_trace = response.metadata.get("token_trace")
    assert isinstance(token_trace, dict)
    assert token_trace.get("turn_total_tokens") == 0
    assert token_trace.get("session_total_tokens") == 0
    assert token_trace.get("accounting_scope") == "all_turn_calls"
    call = stub_client.calls[-1]
    assert call["kwargs"].get("prompt_cache_key") == "telegram:1"
    assert call["kwargs"].get("previous_response_id") is None


@pytest.mark.asyncio
async def test_handler_returns_rich_text_answer_object() -> None:
    handler, _, _ = _handler(
        {
            "answer": {
                "kind": "html",
                "text": "<b>hello</b>",
                "meta": {"disable_link_preview": True},
            },
            "should_answer_to_user": True,
        }
    )

    response = await handler.handle(_message_event("ping"))

    assert response.text == "<b>hello</b>"
    assert response.render is not None
    assert response.render.kind == "html"
    assert response.render.meta.get("disable_link_preview") is True


@pytest.mark.asyncio
async def test_handler_parses_rich_text_answer_from_json_string() -> None:
    handler, _, _ = _handler('{"answer":{"kind":"markdown_v2","text":"*hi*"},"should_answer_to_user":true}')

    response = await handler.handle(_message_event("ping"))

    assert response.text == "*hi*"
    assert response.render is not None
    assert response.render.kind == "markdown_v2"


@pytest.mark.asyncio
async def test_handler_normalizes_markdown_kind_alias() -> None:
    handler, _, _ = _handler(
        {
            "answer": {
                "kind": "markdown",
                "content": "*hi*",
                "meta": {},
            },
            "should_answer_to_user": True,
        }
    )

    response = await handler.handle(_message_event("ping"))

    assert response.render is not None
    assert response.render.kind == "markdown_v2"
    assert response.text == "*hi*"


@pytest.mark.asyncio
async def test_handler_accepts_string_should_answer_flag() -> None:
    handler, _, _ = _handler('{"answer":{"kind":"markdown_v2","content":"*ok*"},"should_answer_to_user":"true"}')

    response = await handler.handle(_message_event("ping"))

    assert response.render is not None
    assert response.render.kind == "markdown_v2"
    assert response.metadata.get("should_reply") is True


@pytest.mark.asyncio
async def test_handler_defaults_should_answer_when_missing() -> None:
    handler, _, _ = _handler('{"answer":{"kind":"html","content":"<b>ok</b>"}}')

    response = await handler.handle(_message_event("ping"))

    assert response.render is not None
    assert response.render.kind == "html"
    assert response.metadata.get("should_reply") is True


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
async def test_handler_reuses_previous_response_id_when_mode_enabled() -> None:
    memory = StubMemory()
    stub_client = StubLLMClient(
        {"answer": "hello", "should_answer_to_user": True},
        response_id="resp-1",
        is_responses=True,
        provider="openai_responses",
        responses_state_mode="previous_response_id",
    )
    handler = LLMMessageHandler(memory=memory, llm_client=cast(LLMClient, stub_client))

    await handler.handle(_message_event("ping"))
    stub_client.response_id = "resp-2"
    await handler.handle(_message_event("ping"))

    assert stub_client.calls[-1]["kwargs"].get("previous_response_id") == "resp-1"


@pytest.mark.asyncio
async def test_handler_disables_prompt_cache_when_client_turns_it_off() -> None:
    memory = StubMemory()
    stub_client = StubLLMClient(
        {"answer": "hello", "should_answer_to_user": True},
        response_id="resp-1",
        is_responses=True,
        provider="openai_responses",
        prompt_cache_enabled=False,
    )
    handler = LLMMessageHandler(memory=memory, llm_client=cast(LLMClient, stub_client))

    await handler.handle(_message_event("ping"))

    assert stub_client.calls[-1]["kwargs"].get("prompt_cache_key") is None


@pytest.mark.asyncio
async def test_handler_uses_fallback_prompt_cache_key_without_chat_or_user() -> None:
    handler, stub_client, _ = _handler(
        {"answer": "hello", "should_answer_to_user": True},
        responses_provider=True,
        response_id="resp-1",
    )

    await handler.handle(MessageEvent(message=_message(text="ping")))

    key = stub_client.calls[-1]["kwargs"].get("prompt_cache_key")
    assert isinstance(key, str)
    assert key.startswith("telegram:")


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
async def test_handler_compacts_history_when_token_limit_reached() -> None:
    memory = StubMemory()
    client = StubLLMClient({"answer": "ok", "should_answer_to_user": True}, total_tokens=60)
    handler = LLMMessageHandler(
        memory=memory,
        llm_client=cast(LLMClient, client),
        max_history_tokens=50,
        notify_compaction_updates=True,
    )

    response = await handler.handle(_message_event("one"))

    session_id = session_id_for(_message_event("one").message)
    assert await memory.count_history(session_id) == 2
    assert memory._store[session_id][0].role == "user"
    assert memory._store[session_id][0].content == "Please compact the current conversation memory."
    assert memory._store[session_id][1].role == "assistant"
    assert client.calls[-1]["args"][1] == "Please compact the current conversation memory."
    assert response.metadata.get("compaction_updates") == ["running compaction...", "done compacting", "ok"]
    token_trace = response.metadata.get("token_trace")
    assert isinstance(token_trace, dict)
    assert token_trace.get("turn_total_tokens") == 120
    assert token_trace.get("compaction_performed") is True
    assert token_trace.get("session_total_tokens_before_compaction") == 60
    assert token_trace.get("session_total_tokens_after_compaction") == 0


@pytest.mark.asyncio
async def test_handler_uses_responses_compaction_endpoint_in_previous_id_mode() -> None:
    memory = StubMemory()
    client = StubLLMClient(
        {"answer": "ok", "should_answer_to_user": True},
        response_id="resp-1",
        is_responses=True,
        provider="openai_responses",
        total_tokens=60,
        responses_state_mode="previous_response_id",
    )
    client.compact_response_id = "cmp-42"
    client.compact_output = [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "compacted via endpoint"}],
        }
    ]
    client.compact_total_tokens = 7
    handler = LLMMessageHandler(
        memory=memory,
        llm_client=cast(LLMClient, client),
        max_history_tokens=50,
        notify_compaction_updates=True,
    )

    response = await handler.handle(_message_event("one"))

    assert len(client.compact_calls) == 1
    assert client.compact_calls[0]["previous_response_id"] == "resp-1"
    assert client.calls[-1]["args"][1] == "one"
    session_id = session_id_for(_message_event("one").message)
    assert handler._session_previous_response_ids[session_id] == "cmp-42"  # type: ignore[attr-defined]
    assert memory._store[session_id][1].content == "compacted via endpoint"
    assert response.metadata.get("compaction_updates") == [
        "running compaction...",
        "done compacting",
        "compacted via endpoint",
    ]
    token_trace = response.metadata.get("token_trace")
    assert isinstance(token_trace, dict)
    assert token_trace.get("turn_total_tokens") == 67


@pytest.mark.asyncio
async def test_handler_uses_compact_prompt_from_prompts_dir(tmp_path: Path) -> None:
    (tmp_path / "compact.md").write_text("compact with these rules", encoding="utf-8")
    memory = StubMemory()
    client = StubLLMClient(
        {"answer": "ok", "should_answer_to_user": True},
        total_tokens=60,
        prompts_dir=str(tmp_path),
    )
    handler = LLMMessageHandler(memory=memory, llm_client=cast(LLMClient, client), max_history_tokens=50)

    await handler.handle(_message_event("one"))

    assert "compact with these rules" in str(client.calls[-1]["kwargs"].get("system_prompt_override", ""))


@pytest.mark.asyncio
async def test_handler_reports_compaction_error_without_breaking_response() -> None:
    class _CompactionFailClient(StubLLMClient):
        async def generate(self, *args: Any, **kwargs: Any) -> LLMGeneration:
            self.calls.append({"args": args, "kwargs": kwargs})
            if len(self.calls) > 1:
                raise RuntimeError("compact failed")
            return LLMGeneration(self.payload, self.response_id, total_tokens=self.total_tokens)

    memory = StubMemory()
    client = _CompactionFailClient({"answer": "ok", "should_answer_to_user": True}, total_tokens=60)
    handler = LLMMessageHandler(
        memory=memory,
        llm_client=cast(LLMClient, client),
        max_history_tokens=50,
        notify_compaction_updates=True,
    )

    response = await handler.handle(_message_event("one"))

    assert response.text == "ok"
    assert response.metadata.get("compaction_updates") == ["running compaction...", "error compacting"]
    token_trace = response.metadata.get("token_trace")
    assert isinstance(token_trace, dict)
    assert token_trace.get("turn_total_tokens") == 60
    assert token_trace.get("compaction_performed") is False
    assert token_trace.get("session_total_tokens_after_compaction") == 60


@pytest.mark.asyncio
async def test_handler_builds_multimodal_input_for_responses_provider() -> None:
    memory = StubMemory()
    client = StubLLMClient(
        {"answer": "done", "should_answer_to_user": True},
        is_responses=True,
        provider="openai_responses",
    )
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
    client = StubLLMClient(
        {"answer": "unused", "should_answer_to_user": True},
        is_responses=False,
        provider="claude",
    )
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

    assert "openrouter" in response.text
    assert not client.calls


@pytest.mark.asyncio
async def test_handler_builds_multimodal_input_for_openrouter_chat_completions() -> None:
    memory = StubMemory()
    client = StubLLMClient(
        {"answer": "done", "should_answer_to_user": True},
        is_responses=False,
        provider="openrouter",
    )
    handler = LLMMessageHandler(memory=memory, llm_client=cast(LLMClient, client))
    event = MessageEvent(
        message=_message(
            text="summarize",
            attachments=[
                {"type": "input_image", "image_url": "data:image/jpeg;base64,QUJD"},
                {
                    "type": "input_file",
                    "filename": "doc.pdf",
                    "file_data": "data:application/pdf;base64,QUJD",
                },
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
    assert user_content[0] == {"type": "text", "text": "summarize"}
    assert user_content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,QUJD"},
    }
    assert user_content[2] == {
        "type": "file",
        "file": {
            "filename": "doc.pdf",
            "file_data": "data:application/pdf;base64,QUJD",
        },
    }


@pytest.mark.asyncio
async def test_handler_builds_text_notice_for_incoming_managed_files() -> None:
    handler, stub_client, _ = _handler({"answer": "ok", "should_answer_to_user": True})
    event = MessageEvent(
        message=_message(
            text="what is this about?",
            metadata={
                "incoming_files": [
                    {
                        "path": "uploads/temp/friends.jpg",
                        "filename": "friends.jpg",
                        "mime": "image/jpeg",
                        "size_bytes": 594643,
                        "source": "photo",
                        "message_id": 12,
                        "caption": "",
                    }
                ]
            },
            user_id=1,
            chat_id=1,
        )
    )

    await handler.handle(event)

    call_args = stub_client.calls[-1]["args"]
    assert isinstance(call_args[1], str)
    assert "Incoming managed files:" in call_args[1]
    assert "uploads/temp/friends.jpg" in call_args[1]
    assert "For analysis requests" in call_args[1]
    assert stub_client.calls[-1]["kwargs"].get("user_content") is None


@pytest.mark.asyncio
async def test_handler_guides_move_for_save_intent_with_incoming_files() -> None:
    handler, stub_client, _ = _handler({"answer": "ok", "should_answer_to_user": True})
    event = MessageEvent(
        message=_message(
            text="save this",
            metadata={
                "incoming_files": [
                    {
                        "path": "uploads/temp/photo_1.jpg",
                        "filename": "photo_1.jpg",
                        "mime": "image/jpeg",
                        "size_bytes": 100,
                        "source": "photo",
                        "message_id": 1,
                        "caption": "",
                    }
                ]
            },
            user_id=1,
            chat_id=1,
        )
    )

    await handler.handle(event)

    call_args = stub_client.calls[-1]["args"]
    assert isinstance(call_args[1], str)
    assert "For file-management requests" in call_args[1]
    assert "Do NOT call self_insert_artifact" in call_args[1]
    assert "destination_path=uploads/photo_1.jpg" in call_args[1]


@pytest.mark.asyncio
async def test_handler_returns_generic_error_when_not_in_debug_mode() -> None:
    memory = StubMemory()
    client = FailingLLMClient(payload=None)
    handler = LLMMessageHandler(memory=memory, llm_client=cast(LLMClient, client))
    logger = logging.getLogger("minibot.handler")
    original_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        response = await handler.handle(_message_event("ping"))
    finally:
        logger.setLevel(original_level)

    assert response.text == "Sorry, I couldn't answer right now."


@pytest.mark.asyncio
async def test_handler_returns_error_details_in_debug_mode() -> None:
    memory = StubMemory()
    client = FailingLLMClient(payload=None)
    handler = LLMMessageHandler(memory=memory, llm_client=cast(LLMClient, client))
    logger = logging.getLogger("minibot.handler")
    original_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        response = await handler.handle(_message_event("ping"))
    finally:
        logger.setLevel(original_level)

    assert "LLM error (TimeoutError)" in response.text
    assert "request timed out" in response.text


@pytest.mark.asyncio
async def test_handler_with_guardrail_plugin_retries_when_required() -> None:
    from minibot.app.tool_use_guardrail import GuardrailDecision, ToolUseGuardrail

    class _RequireRetryGuardrail:
        async def apply(self, **_: Any) -> GuardrailDecision:
            return GuardrailDecision(
                requires_retry=True,
                retry_system_prompt_suffix="You must call a tool.",
                tokens_used=5,
            )

    memory = StubMemory()
    client = StubLLMClient(payload="unused", provider="openrouter")
    guardrail = cast(ToolUseGuardrail, _RequireRetryGuardrail())
    handler = LLMMessageHandler(memory=memory, llm_client=cast(LLMClient, client), tool_use_guardrail=guardrail)

    first_result = RuntimeResult(
        payload='{"answer":{"kind":"text","content":"Let me check."},"should_answer_to_user":true}',
        response_id="r1",
        state=AgentState(messages=[AgentMessage(role="assistant", content=[MessagePart(type="text", text="x")])]),
    )
    second_result = RuntimeResult(
        payload='{"answer":{"kind":"text","content":"Done via tool."},"should_answer_to_user":true}',
        response_id="r2",
        state=AgentState(
            messages=[
                AgentMessage(role="assistant", content=[MessagePart(type="text", text="x")]),
                AgentMessage(role="tool", content=[MessagePart(type="json", value={"ok": True})]),
            ]
        ),
    )
    runtime = StubRuntime([first_result, second_result])
    handler._runtime = runtime  # type: ignore[attr-defined]

    response = await handler.handle(_message_event("do something"))

    assert response.text == "Done via tool."
    assert len(runtime.calls) == 2
    token_trace = response.metadata.get("token_trace")
    assert isinstance(token_trace, dict)
    assert token_trace.get("turn_total_tokens") == 5


@pytest.mark.asyncio
async def test_handler_guardrail_retry_with_none_suffix_does_not_inject_none_in_prompt() -> None:
    from minibot.app.tool_use_guardrail import GuardrailDecision, ToolUseGuardrail

    class _NullSuffixGuardrail:
        async def apply(self, **_: Any) -> GuardrailDecision:
            return GuardrailDecision(requires_retry=True, retry_system_prompt_suffix=None)

    memory = StubMemory()
    client = StubLLMClient(payload="unused", provider="openrouter")
    guardrail = cast(ToolUseGuardrail, _NullSuffixGuardrail())
    handler = LLMMessageHandler(memory=memory, llm_client=cast(LLMClient, client), tool_use_guardrail=guardrail)

    first_result = RuntimeResult(
        payload='{"answer":{"kind":"text","content":"initial"},"should_answer_to_user":true}',
        response_id="r1",
        state=AgentState(messages=[AgentMessage(role="assistant", content=[MessagePart(type="text", text="x")])]),
    )
    second_result = RuntimeResult(
        payload='{"answer":{"kind":"text","content":"retried"},"should_answer_to_user":true}',
        response_id="r2",
        state=AgentState(
            messages=[
                AgentMessage(role="assistant", content=[MessagePart(type="text", text="x")]),
                AgentMessage(role="tool", content=[MessagePart(type="json", value={"ok": True})]),
            ]
        ),
    )
    runtime = StubRuntime([first_result, second_result])
    handler._runtime = runtime  # type: ignore[attr-defined]

    await handler.handle(_message_event("do something"))

    assert len(runtime.calls) == 2
    retry_state: AgentState = runtime.calls[1]["state"]
    system_message = next(m for m in retry_state.messages if m.role == "system")
    system_text = system_message.content[0].text if system_message.content else ""
    assert "None" not in (system_text or "")


@pytest.mark.asyncio
async def test_handler_with_guardrail_resolved_render_text_skips_runtime_answer() -> None:
    from minibot.app.tool_use_guardrail import GuardrailDecision, ToolUseGuardrail

    class _ResolvedGuardrail:
        async def apply(self, **_: Any) -> GuardrailDecision:
            return GuardrailDecision(requires_retry=False, resolved_render_text="direct answer from guardrail")

    memory = StubMemory()
    client = StubLLMClient(payload="unused", provider="openrouter")
    handler = LLMMessageHandler(
        memory=memory,
        llm_client=cast(LLMClient, client),
        tool_use_guardrail=cast(ToolUseGuardrail, _ResolvedGuardrail()),
    )
    runtime = StubRuntime(
        [
            RuntimeResult(
                payload='{"answer":{"kind":"text","content":"model answer"},"should_answer_to_user":true}',
                response_id="r1",
                state=AgentState(
                    messages=[AgentMessage(role="assistant", content=[MessagePart(type="text", text="x")])]
                ),
            )
        ]
    )
    handler._runtime = runtime  # type: ignore[attr-defined]

    response = await handler.handle(_message_event("do something"))

    assert response.text == "direct answer from guardrail"
    assert response.metadata.get("should_reply") is True


@pytest.mark.asyncio
async def test_handler_guardrail_retry_still_enforces_delegation_unresolved_fallback() -> None:
    from minibot.app.tool_use_guardrail import GuardrailDecision, ToolUseGuardrail

    class _RequireRetryGuardrail:
        async def apply(self, **_: Any) -> GuardrailDecision:
            return GuardrailDecision(requires_retry=True, retry_system_prompt_suffix="Use tools")

    memory = StubMemory()
    client = StubLLMClient(payload="unused", provider="openrouter")
    handler = LLMMessageHandler(
        memory=memory,
        llm_client=cast(LLMClient, client),
        tool_use_guardrail=cast(ToolUseGuardrail, _RequireRetryGuardrail()),
    )
    first_result = RuntimeResult(
        payload='{"answer":{"kind":"text","content":"first"},"should_answer_to_user":true}',
        response_id="r1",
        state=AgentState(messages=[AgentMessage(role="assistant", content=[MessagePart(type="text", text="x")])]),
    )
    second_result = RuntimeResult(
        payload='{"answer":{"kind":"text","content":"delegated unresolved"},"should_answer_to_user":false}',
        response_id="r2",
        state=AgentState(
            messages=[
                AgentMessage(role="assistant", content=[MessagePart(type="text", text="x")]),
                AgentMessage(
                    role="tool",
                    name="invoke_agent",
                    content=[
                        MessagePart(
                            type="json",
                            value={
                                "agent": "workspace_manager",
                                "ok": True,
                                "result_status": "not_user_answerable",
                                "should_answer_to_user": False,
                            },
                        )
                    ],
                ),
            ]
        ),
    )
    third_result = RuntimeResult(
        payload='{"answer":{"kind":"text","content":"resolved after bounded retry"},"should_answer_to_user":true}',
        response_id="r3",
        state=AgentState(
            messages=[
                AgentMessage(role="assistant", content=[MessagePart(type="text", text="x")]),
                AgentMessage(
                    role="tool",
                    name="invoke_agent",
                    content=[
                        MessagePart(
                            type="json",
                            value={
                                "agent": "workspace_manager",
                                "ok": True,
                                "result_status": "success",
                                "should_answer_to_user": True,
                            },
                        )
                    ],
                ),
            ]
        ),
    )
    runtime = StubRuntime([first_result, second_result, third_result])
    handler._runtime = runtime  # type: ignore[attr-defined]

    response = await handler.handle(_message_event("do something"))

    assert len(runtime.calls) == 3
    assert response.text == "resolved after bounded retry"
    assert response.metadata.get("should_reply") is True


@pytest.mark.asyncio
async def test_handler_counts_guardrail_tokens_in_session_compaction_accounting() -> None:
    from minibot.app.tool_use_guardrail import GuardrailDecision, ToolUseGuardrail

    class _TokenOnlyGuardrail:
        async def apply(self, **_: Any) -> GuardrailDecision:
            return GuardrailDecision(requires_retry=False, tokens_used=60)

    memory = StubMemory()
    client = StubLLMClient({"answer": "compact summary", "should_answer_to_user": True}, provider="openrouter")
    handler = LLMMessageHandler(
        memory=memory,
        llm_client=cast(LLMClient, client),
        max_history_tokens=50,
        notify_compaction_updates=True,
        tool_use_guardrail=cast(ToolUseGuardrail, _TokenOnlyGuardrail()),
    )
    runtime = StubRuntime(
        [
            RuntimeResult(
                payload='{"answer":{"kind":"text","content":"runtime answer"},"should_answer_to_user":true}',
                response_id="r1",
                state=AgentState(
                    messages=[AgentMessage(role="assistant", content=[MessagePart(type="text", text="x")])]
                ),
            )
        ]
    )
    handler._runtime = runtime  # type: ignore[attr-defined]

    response = await handler.handle(_message_event("trigger compaction"))

    token_trace = response.metadata.get("token_trace")
    assert isinstance(token_trace, dict)
    assert token_trace.get("compaction_performed") is True
    assert token_trace.get("session_total_tokens_before_compaction") == 60
    assert response.metadata.get("compaction_updates") == [
        "running compaction...",
        "done compacting",
        "compact summary",
    ]


@pytest.mark.asyncio
async def test_handler_injects_channel_prompt_fragment(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    telegram_prompt = prompts_dir / "channels" / "telegram.md"
    telegram_prompt.parent.mkdir(parents=True, exist_ok=True)
    telegram_prompt.write_text("Always use kind=markdown_v2 for markdown requests.", encoding="utf-8")

    memory = StubMemory()
    client = StubLLMClient(
        {"answer": "ok", "should_answer_to_user": True},
        system_prompt="You are Minibot.",
        prompts_dir=str(prompts_dir),
    )
    handler = LLMMessageHandler(memory=memory, llm_client=cast(LLMClient, client))

    await handler.handle(_message_event("ping"))

    call_kwargs = client.calls[-1]["kwargs"]
    override = call_kwargs.get("system_prompt_override")
    assert isinstance(override, str)
    assert "You are Minibot." in override
    assert "Always use kind=markdown_v2" in override


@pytest.mark.asyncio
async def test_handler_injects_environment_prompt_fragment() -> None:
    memory = StubMemory()
    client = StubLLMClient(
        {"answer": "ok", "should_answer_to_user": True},
        system_prompt="You are Minibot.",
    )
    handler = LLMMessageHandler(
        memory=memory,
        llm_client=cast(LLMClient, client),
        environment_prompt_fragment="Environment context:\n- Browser artifacts directory: ./data/files/browser",
    )

    await handler.handle(_message_event("ping"))

    call_kwargs = client.calls[-1]["kwargs"]
    override = call_kwargs.get("system_prompt_override")
    assert isinstance(override, str)
    assert "Browser artifacts directory: ./data/files/browser" in override


@pytest.mark.asyncio
async def test_repair_response_appends_retry_prompt_and_answer_to_history() -> None:
    memory = StubMemory()
    client = StubLLMClient(
        {
            "answer": {
                "kind": "markdown_v2",
                "content": "*fixed*",
                "meta": {},
            },
            "should_answer_to_user": True,
        },
        provider="openrouter",
    )
    handler = LLMMessageHandler(memory=memory, llm_client=cast(LLMClient, client))

    repaired = await handler.repair_format_response(
        response=ChannelResponse(
            channel="telegram",
            chat_id=1,
            text="bad",
            render=RenderableResponse(kind="markdown_v2", text="bad"),
        ),
        parse_error="can't parse entities",
        channel="telegram",
        chat_id=1,
        user_id=1,
        attempt=1,
    )

    session_id = session_id_for(_message(channel="telegram", chat_id=1, user_id=1))
    saved = memory._store.get(session_id, [])
    assert len(saved) == 2
    assert saved[0].role == "user"
    assert "Telegram error" in saved[0].content
    assert saved[1].role == "assistant"
    assert saved[1].content == "*fixed*"
    assert repaired.render is not None
    assert repaired.render.kind == "markdown_v2"
    token_trace = repaired.metadata.get("token_trace")
    assert isinstance(token_trace, dict)
    assert token_trace.get("accounting_scope") == "all_turn_calls"

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, cast

import pytest
from llm_async.models import Tool

from minibot.app.agent_runtime import RuntimeResult
from minibot.app.handlers.services import AudioAutoTranscribePolicy, AudioAutoTranscriptionService, LLMTurnService
from minibot.app.handlers.services import ToolBindingAudioTranscriptionExecutor, build_llm_turn_service
from minibot.app.tool_use_guardrail import NoopToolUseGuardrail
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart
from minibot.core.channels import ChannelMessage, ChannelResponse, RenderableResponse
from minibot.core.events import MessageEvent
from minibot.core.memory import MemoryEntry
from minibot.llm.provider_factory import LLMClient, LLMGeneration
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.shared.utils import session_id_for


def _message(**overrides: Any) -> ChannelMessage:
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


def _message_event(text: str = "hi") -> MessageEvent:
    return MessageEvent(message=_message(text=text, user_id=1, chat_id=1))


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
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        reasoning_output_tokens: int | None = None,
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
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cached_input_tokens = cached_input_tokens
        self.reasoning_output_tokens = reasoning_output_tokens
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
        return LLMGeneration(
            self.payload,
            self.response_id,
            total_tokens=self.total_tokens,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cached_input_tokens=self.cached_input_tokens,
            reasoning_output_tokens=self.reasoning_output_tokens,
        )

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


def _service(
    llm_payload: Any,
    *,
    response_id: str | None = None,
    responses_provider: bool = False,
    provider: str = "openai",
    memory: StubMemory | None = None,
    **kwargs: Any,
) -> tuple[LLMTurnService, StubLLMClient, StubMemory]:
    stub_memory = memory or StubMemory()
    responses_state_mode = kwargs.pop("responses_state_mode", "full_messages")
    client = StubLLMClient(
        llm_payload,
        response_id=response_id,
        is_responses=responses_provider,
        provider=provider,
        responses_state_mode=responses_state_mode,
    )
    service = build_llm_turn_service(
        memory=cast(Any, stub_memory),
        llm_client=cast(LLMClient, client),
        tool_use_guardrail=NoopToolUseGuardrail(),
        **kwargs,
    )
    return service, client, stub_memory


@pytest.mark.asyncio
async def test_turn_service_returns_structured_answer() -> None:
    service, stub_client, _ = _service(
        {"answer": {"kind": "text", "content": "hello"}, "should_answer_to_user": True},
        responses_provider=True,
        response_id="resp-1",
    )

    response = await service.handle(_message_event("ping"))

    assert response.text == "hello"
    assert response.metadata.get("should_reply") is True
    assert stub_client.calls[-1]["kwargs"].get("prompt_cache_key") == "telegram:1"


@pytest.mark.asyncio
async def test_turn_service_includes_usage_trace_metadata() -> None:
    memory = StubMemory()
    stub_client = StubLLMClient(
        {"answer": {"kind": "text", "content": "hello"}, "should_answer_to_user": True},
        is_responses=True,
        provider="openai_responses",
        total_tokens=33,
        input_tokens=21,
        output_tokens=12,
        cached_input_tokens=8,
        reasoning_output_tokens=3,
    )
    service = build_llm_turn_service(
        memory=cast(Any, memory),
        llm_client=cast(LLMClient, stub_client),
        tool_use_guardrail=NoopToolUseGuardrail(),
    )

    response = await service.handle(_message_event("ping"))

    assert response.metadata.get("usage_trace") == {
        "input_tokens": 21,
        "output_tokens": 12,
        "total_tokens": 33,
        "cached_input_tokens": 8,
        "reasoning_output_tokens": 3,
    }


@pytest.mark.asyncio
async def test_turn_service_compaction_endpoint_updates_previous_response_id() -> None:
    service, client, memory = _service(
        {"answer": {"kind": "text", "content": "ok"}, "should_answer_to_user": True},
        response_id="resp-1",
        responses_provider=True,
        provider="openai_responses",
        responses_state_mode="previous_response_id",
        max_history_tokens=50,
        notify_compaction_updates=True,
    )
    client.total_tokens = 60
    client.compact_response_id = "cmp-42"
    client.compact_output = [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "compacted via endpoint"}],
        }
    ]
    client.compact_total_tokens = 7

    response = await service.handle(_message_event("one"))

    session_id = session_id_for(_message_event("one").message)
    assert service.session_state.get_previous_response_id(session_id) == "cmp-42"
    assert memory._store[session_id][1].content == "compacted via endpoint"
    assert response.metadata.get("compaction_updates") == [
        "running compaction...",
        "done compacting",
        "compacted via endpoint",
    ]


@pytest.mark.asyncio
async def test_turn_service_fallback_compaction_updates_previous_response_id() -> None:
    class _FallbackCompactionClient(StubLLMClient):
        def __init__(self) -> None:
            super().__init__(
                {"answer": {"kind": "text", "content": "ok"}, "should_answer_to_user": True},
                response_id="resp-1",
                is_responses=True,
                provider="openai_responses",
                total_tokens=60,
                responses_state_mode="previous_response_id",
            )
            self._generate_calls = 0

        async def generate(self, *args: Any, **kwargs: Any) -> LLMGeneration:
            self.calls.append({"args": args, "kwargs": kwargs})
            self._generate_calls += 1
            if self._generate_calls == 1:
                return LLMGeneration(self.payload, "resp-1", total_tokens=self.total_tokens)
            return LLMGeneration(self.payload, "cmp-fallback", total_tokens=5)

        async def compact_response(
            self,
            *,
            previous_response_id: str,
            prompt_cache_key: str | None = None,
        ) -> Any:
            _ = previous_response_id, prompt_cache_key
            raise RuntimeError("compact endpoint unavailable")

    memory = StubMemory()
    client = _FallbackCompactionClient()
    service = build_llm_turn_service(
        memory=cast(Any, memory),
        llm_client=cast(LLMClient, client),
        max_history_tokens=50,
        notify_compaction_updates=True,
        tool_use_guardrail=NoopToolUseGuardrail(),
    )

    await service.handle(_message_event("one"))

    session_id = session_id_for(_message_event("one").message)
    assert service.session_state.get_previous_response_id(session_id) == "cmp-fallback"


@pytest.mark.asyncio
async def test_turn_service_reuses_previous_response_id_when_mode_enabled() -> None:
    memory = StubMemory()
    stub_client = StubLLMClient(
        {"answer": {"kind": "text", "content": "hello"}, "should_answer_to_user": True},
        response_id="resp-1",
        is_responses=True,
        provider="openai_responses",
        responses_state_mode="previous_response_id",
    )
    service = build_llm_turn_service(
        memory=cast(Any, memory),
        llm_client=cast(LLMClient, stub_client),
        tool_use_guardrail=NoopToolUseGuardrail(),
    )

    await service.handle(_message_event("ping"))
    stub_client.response_id = "resp-2"
    await service.handle(_message_event("ping"))

    assert stub_client.calls[-1]["kwargs"].get("previous_response_id") == "resp-1"


@pytest.mark.asyncio
async def test_turn_service_auto_transcribes_short_incoming_audio_before_generation() -> None:
    memory = StubMemory()
    client = StubLLMClient({"answer": {"kind": "text", "content": "ok"}, "should_answer_to_user": True})
    tool_calls: list[dict[str, Any]] = []

    async def _transcribe(payload: dict[str, Any], _context: ToolContext) -> dict[str, Any]:
        tool_calls.append(payload)
        return {"ok": True, "text": "abre el garage"}

    transcribe_binding = ToolBinding(
        tool=Tool(name="transcribe_audio", description="transcribe", parameters={"type": "object"}),
        handler=_transcribe,
    )
    auto_service = AudioAutoTranscriptionService(
        executor=ToolBindingAudioTranscriptionExecutor(transcribe_binding),
        policy=AudioAutoTranscribePolicy(enabled=True, max_duration_seconds=45),
    )
    service = build_llm_turn_service(
        memory=cast(Any, memory),
        llm_client=cast(LLMClient, client),
        tools=[transcribe_binding],
        audio_auto_transcription_service=auto_service,
        tool_use_guardrail=NoopToolUseGuardrail(),
    )
    event = MessageEvent(
        message=_message(
            text="",
            metadata={
                "incoming_files": [
                    {
                        "path": "uploads/temp/voice_1.ogg",
                        "filename": "voice_1.ogg",
                        "mime": "audio/ogg",
                        "size_bytes": 12000,
                        "source": "voice",
                        "duration_seconds": 12,
                    }
                ]
            },
            user_id=1,
            chat_id=1,
        )
    )

    await service.handle(event)

    assert len(tool_calls) == 1
    model_text = client.calls[-1]["args"][1]
    assert "Automatic audio transcriptions from incoming files:" in model_text


@pytest.mark.asyncio
async def test_turn_service_returns_generic_error_when_not_in_debug_mode() -> None:
    memory = StubMemory()
    client = FailingLLMClient(payload=None)
    service = build_llm_turn_service(
        memory=cast(Any, memory),
        llm_client=cast(LLMClient, client),
        tool_use_guardrail=NoopToolUseGuardrail(),
    )
    logger = logging.getLogger("minibot.handler")
    original_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        response = await service.handle(_message_event("ping"))
    finally:
        logger.setLevel(original_level)

    assert response.text == "Sorry, I couldn't answer right now."


@pytest.mark.asyncio
async def test_turn_service_guardrail_retry_with_runtime() -> None:
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
    service = build_llm_turn_service(
        memory=cast(Any, memory),
        llm_client=cast(LLMClient, client),
        tool_use_guardrail=guardrail,
    )
    runtime = StubRuntime(
        [
            RuntimeResult(
                payload='{"answer":{"kind":"text","content":"Let me check."},"should_answer_to_user":true}',
                response_id="r1",
                state=AgentState(
                    messages=[AgentMessage(role="assistant", content=[MessagePart(type="text", text="x")])]
                ),
            ),
            RuntimeResult(
                payload='{"answer":{"kind":"text","content":"Done via tool."},"should_answer_to_user":true}',
                response_id="r2",
                state=AgentState(
                    messages=[
                        AgentMessage(role="assistant", content=[MessagePart(type="text", text="x")]),
                        AgentMessage(role="tool", content=[MessagePart(type="json", value={"ok": True})]),
                    ]
                ),
            ),
        ]
    )
    service.set_runtime(cast(Any, runtime))

    response = await service.handle(_message_event("do something"))

    assert response.text == "Done via tool."
    assert len(runtime.calls) == 2


@pytest.mark.asyncio
async def test_turn_service_injects_recent_filesystem_paths_in_current_turn_only() -> None:
    memory = StubMemory()
    client = StubLLMClient(payload="unused", provider="openrouter")
    service = build_llm_turn_service(
        memory=cast(Any, memory),
        llm_client=cast(LLMClient, client),
        managed_files_root="./data/files",
        tool_use_guardrail=NoopToolUseGuardrail(),
    )
    runtime = StubRuntime(
        [
            RuntimeResult(
                payload='{"answer":{"kind":"text","content":"saved"},"should_answer_to_user":true}',
                response_id="r1",
                state=AgentState(
                    messages=[
                        AgentMessage(role="assistant", content=[MessagePart(type="text", text="x")]),
                        AgentMessage(
                            role="tool",
                            name="filesystem",
                            content=[
                                MessagePart(
                                    type="json",
                                    value={
                                        "action": "write",
                                        "path": "data/files/count_words.py",
                                        "path_relative": "data/files/count_words.py",
                                        "path_absolute": "/home/johanderson/sandbox/minibot/data/files/count_words.py",
                                        "path_scope": "inside_root",
                                    },
                                )
                            ],
                        ),
                    ]
                ),
            ),
            RuntimeResult(
                payload='{"answer":{"kind":"text","content":"patched"},"should_answer_to_user":true}',
                response_id="r2",
                state=AgentState(
                    messages=[AgentMessage(role="assistant", content=[MessagePart(type="text", text="ok")])]
                ),
            ),
        ]
    )
    service.set_runtime(cast(Any, runtime))

    await service.handle(_message_event("save file"))
    await service.handle(_message_event("patch it"))

    second_state: AgentState = runtime.calls[1]["state"]
    second_user = second_state.messages[-1].content[0].text or ""
    assert "Recent filesystem paths from this session" in second_user
    assert "count_words.py" in second_user
    session_id = session_id_for(_message_event("patch it").message)
    assert service.session_state.recent_files(session_id)


@pytest.mark.asyncio
async def test_turn_service_repair_response_reuses_and_refreshes_previous_response_id_when_mode_enabled() -> None:
    memory = StubMemory()
    client = StubLLMClient(
        {
            "answer": {"kind": "markdown", "content": "*fixed*"},
            "should_answer_to_user": True,
        },
        response_id="resp-repair",
        is_responses=True,
        provider="openai_responses",
        responses_state_mode="previous_response_id",
    )
    service = build_llm_turn_service(
        memory=cast(Any, memory),
        llm_client=cast(LLMClient, client),
        tool_use_guardrail=NoopToolUseGuardrail(),
    )
    session_id = session_id_for(_message(channel="telegram", chat_id=1, user_id=1))
    service.session_state.set_previous_response_id(session_id, "resp-before")

    await service.repair_format_response(
        response=ChannelResponse(
            channel="telegram",
            chat_id=1,
            text="bad",
            render=RenderableResponse(kind="markdown", text="bad"),
        ),
        parse_error="can't parse entities",
        channel="telegram",
        chat_id=1,
        user_id=1,
        attempt=1,
    )

    assert client.calls[-1]["kwargs"].get("previous_response_id") == "resp-before"
    assert service.session_state.get_previous_response_id(session_id) == "resp-repair"


@pytest.mark.asyncio
async def test_turn_service_uses_compact_prompt_from_prompts_dir(tmp_path: Path) -> None:
    (tmp_path / "compact.md").write_text("compact with these rules", encoding="utf-8")
    memory = StubMemory()
    client = StubLLMClient(
        {"answer": {"kind": "text", "content": "ok"}, "should_answer_to_user": True},
        total_tokens=60,
        prompts_dir=str(tmp_path),
    )
    service = build_llm_turn_service(
        memory=cast(Any, memory),
        llm_client=cast(LLMClient, client),
        max_history_tokens=50,
        tool_use_guardrail=NoopToolUseGuardrail(),
    )

    await service.handle(_message_event("one"))

    assert "compact with these rules" in str(client.calls[-1]["kwargs"].get("system_prompt_override", ""))

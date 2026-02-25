from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, cast

import pytest

from minibot.app.agent_runtime import RuntimeResult
from minibot.app.agent_runtime import AgentRuntime
from minibot.app.handlers.services import (
    HistoryCompactionService,
    PromptService,
    ResponseMetadataService,
    RuntimeOrchestrationService,
    SessionStateService,
    UserInputService,
)
from minibot.app.tool_use_guardrail import GuardrailDecision
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart
from minibot.core.channels import ChannelMessage
from minibot.core.memory import MemoryEntry
from minibot.core.memory import MemoryBackend
from minibot.llm.provider_factory import LLMClient, LLMCompaction, LLMGeneration
from minibot.llm.tools.base import ToolContext


class _StubClient:
    def __init__(self) -> None:
        self._provider = "openai_responses"
        self._model = "gpt-5-mini"
        self.compact_calls: list[dict[str, Any]] = []

    def provider_name(self) -> str:
        return self._provider

    def model_name(self) -> str:
        return self._model

    def supports_media_inputs(self) -> bool:
        return True

    def media_input_mode(self) -> str:
        return "chat_completions"

    def prompts_dir(self) -> str:
        return "./prompts"

    def system_prompt(self) -> str:
        return "You are Minibot, a helpful assistant."

    def is_responses_provider(self) -> bool:
        return True

    async def compact_response(self, *, previous_response_id: str, prompt_cache_key: str | None) -> LLMCompaction:
        self.compact_calls.append(
            {
                "previous_response_id": previous_response_id,
                "prompt_cache_key": prompt_cache_key,
            }
        )
        return LLMCompaction(
            response_id="cmp-1",
            output=[
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "compacted text"}],
                }
            ],
            total_tokens=7,
        )


class _StubMemory:
    def __init__(self) -> None:
        self._store: dict[str, list[MemoryEntry]] = {}

    async def append_history(self, session_id: str, role: str, content: str) -> None:
        self._store.setdefault(session_id, []).append(
            MemoryEntry(role=role, content=content, created_at=datetime.now(timezone.utc))
        )

    async def get_history(self, session_id: str, limit: int | None = None) -> list[MemoryEntry]:
        entries = self._store.get(session_id, [])
        if limit is None:
            return list(entries)
        return entries[-limit:]

    async def trim_history(self, session_id: str, keep_latest: int) -> int:
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


class _StubRuntime:
    async def run(self, **_: Any) -> RuntimeResult:
        return RuntimeResult(
            payload='{"answer":{"kind":"text","content":"ignored"},"should_answer_to_user":true}',
            response_id="resp-1",
            state=AgentState(messages=[AgentMessage(role="assistant", content=[MessagePart(type="text", text="x")])]),
            total_tokens=4,
        )


class _ResolvedGuardrail:
    async def apply(self, **_: Any) -> GuardrailDecision:
        return GuardrailDecision(requires_retry=False, resolved_render_text="resolved", tokens_used=3)


def _message(**overrides: Any) -> ChannelMessage:
    base = {
        "channel": "telegram",
        "user_id": 1,
        "chat_id": 1,
        "message_id": None,
        "text": "hi",
        "attachments": [],
        "metadata": {},
    }
    base.update(overrides)
    return ChannelMessage(**base)


def test_metadata_service_returns_provider_and_model() -> None:
    svc = ResponseMetadataService(cast(LLMClient, _StubClient()))

    metadata = svc.response_metadata(True)

    assert metadata["should_reply"] is True
    assert metadata["llm_provider"] == "openai_responses"
    assert metadata["llm_model"] == "gpt-5-mini"


def test_user_input_service_transforms_attachments_for_chat_completions() -> None:
    svc = UserInputService(cast(LLMClient, _StubClient()))
    message = _message(
        text="summarize",
        attachments=[
            {"type": "input_image", "image_url": "data:image/jpeg;base64,QUJD"},
            {
                "type": "input_file",
                "filename": "a.pdf",
                "file_data": "data:application/pdf;base64,QUJD",
            },
        ],
    )

    prompt, user_content = svc.build_model_user_input(message)

    assert prompt == "summarize"
    assert isinstance(user_content, list)
    assert user_content[0] == {"type": "text", "text": "summarize"}
    assert user_content[1]["type"] == "image_url"
    assert user_content[2]["type"] == "file"


def test_prompt_service_builds_format_repair_prompt() -> None:
    prompt = PromptService.build_format_repair_prompt(
        channel="telegram",
        original_kind="markdown_v2",
        parse_error="can't parse entities",
        original_content="*Hello",
    )

    assert "Telegram" in prompt
    assert "can't parse entities" in prompt
    assert "structured output" in prompt


def test_session_state_service_tracks_tokens_and_previous_response_id() -> None:
    state = SessionStateService()

    tracked = state.track_tokens("s1", 12)
    state.set_previous_response_id("s1", "resp-1")
    trace = state.build_token_trace(
        turn_total_tokens=12,
        session_total_tokens_before_compaction=None,
        session_total_tokens_after_compaction=12,
        compaction_performed=False,
    )

    assert tracked == 12
    assert state.current_tokens("s1") == 12
    assert state.get_previous_response_id("s1") == "resp-1"
    assert trace["turn_total_tokens"] == 12


def test_session_state_service_tracks_usage_snapshot() -> None:
    state = SessionStateService()

    state.track_usage(
        "s1",
        input_tokens=120,
        output_tokens=18,
        total_tokens=138,
        cached_input_tokens=40,
        reasoning_output_tokens=7,
    )

    usage_trace = state.latest_usage_trace("s1")
    assert usage_trace["input_tokens"] == 120
    assert usage_trace["output_tokens"] == 18
    assert usage_trace["total_tokens"] == 138
    assert usage_trace["cached_input_tokens"] == 40
    assert usage_trace["reasoning_output_tokens"] == 7


@pytest.mark.asyncio
async def test_compaction_service_uses_responses_endpoint_when_available() -> None:
    client = _StubClient()
    memory = _StubMemory()
    state = SessionStateService()
    state.track_tokens("s1", 20)
    state.set_previous_response_id("s1", "resp-previous")
    await memory.append_history("s1", "user", "hi")
    prompt_service = PromptService(
        llm_client=cast(LLMClient, client),
        tools=[],
        environment_prompt_fragment="",
        logger=logging.getLogger("test"),
    )
    service = HistoryCompactionService(
        memory=cast(MemoryBackend, memory),
        llm_client=cast(LLMClient, client),
        session_state=state,
        prompt_service=prompt_service,
        logger=logging.getLogger("test"),
        max_history_tokens=10,
        compaction_user_request="Please compact the current conversation memory.",
    )

    result = await service.compact_history_if_needed(
        "s1",
        prompt_cache_key="telegram:1",
        system_prompt="system",
        notify=True,
        responses_state_mode="previous_response_id",
    )

    assert result.performed is True
    assert client.compact_calls[0]["previous_response_id"] == "resp-previous"
    assert state.get_previous_response_id("s1") == "cmp-1"
    assert memory._store["s1"][1].content == "compacted text"


@pytest.mark.asyncio
async def test_compaction_service_uses_latest_input_tokens_for_responses_threshold() -> None:
    client = _StubClient()
    memory = _StubMemory()
    state = SessionStateService()
    state.track_tokens("s1", 20)
    state.track_usage(
        "s1",
        input_tokens=120,
        output_tokens=12,
        total_tokens=132,
        cached_input_tokens=None,
        reasoning_output_tokens=None,
    )
    state.set_previous_response_id("s1", "resp-previous")
    await memory.append_history("s1", "user", "hi")
    prompt_service = PromptService(
        llm_client=cast(LLMClient, client),
        tools=[],
        environment_prompt_fragment="",
        logger=logging.getLogger("test"),
    )
    service = HistoryCompactionService(
        memory=cast(MemoryBackend, memory),
        llm_client=cast(LLMClient, client),
        session_state=state,
        prompt_service=prompt_service,
        logger=logging.getLogger("test"),
        max_history_tokens=100,
        compaction_user_request="Please compact the current conversation memory.",
    )

    result = await service.compact_history_if_needed(
        "s1",
        prompt_cache_key="telegram:1",
        system_prompt="system",
        notify=True,
        responses_state_mode="previous_response_id",
    )

    assert result.performed is True
    assert client.compact_calls[0]["previous_response_id"] == "resp-previous"


@pytest.mark.asyncio
async def test_compaction_service_fallback_summary_updates_previous_response_id() -> None:
    class _FallbackClient(_StubClient):
        async def compact_response(self, *, previous_response_id: str, prompt_cache_key: str | None) -> LLMCompaction:
            _ = previous_response_id, prompt_cache_key
            raise RuntimeError("compact endpoint unavailable")

        async def generate(self, *args: Any, **kwargs: Any) -> LLMGeneration:
            _ = args, kwargs
            return LLMGeneration(
                {"answer": {"kind": "text", "content": "summary via fallback"}, "should_answer_to_user": True},
                response_id="cmp-fallback",
                total_tokens=7,
            )

    client = _FallbackClient()
    memory = _StubMemory()
    state = SessionStateService()
    state.track_tokens("s1", 20)
    state.set_previous_response_id("s1", "resp-previous")
    await memory.append_history("s1", "user", "hi")
    prompt_service = PromptService(
        llm_client=cast(LLMClient, client),
        tools=[],
        environment_prompt_fragment="",
        logger=logging.getLogger("test"),
    )
    service = HistoryCompactionService(
        memory=cast(MemoryBackend, memory),
        llm_client=cast(LLMClient, client),
        session_state=state,
        prompt_service=prompt_service,
        logger=logging.getLogger("test"),
        max_history_tokens=10,
        compaction_user_request="Please compact the current conversation memory.",
    )

    result = await service.compact_history_if_needed(
        "s1",
        prompt_cache_key="telegram:1",
        system_prompt="system",
        notify=True,
        responses_state_mode="previous_response_id",
    )

    assert result.performed is True
    assert state.get_previous_response_id("s1") == "cmp-fallback"
    assert memory._store["s1"][1].content == "summary via fallback"


@pytest.mark.asyncio
async def test_compaction_service_fallback_summary_clears_previous_response_id_when_missing() -> None:
    class _FallbackClientNoResponseId(_StubClient):
        async def compact_response(self, *, previous_response_id: str, prompt_cache_key: str | None) -> LLMCompaction:
            _ = previous_response_id, prompt_cache_key
            raise RuntimeError("compact endpoint unavailable")

        async def generate(self, *args: Any, **kwargs: Any) -> LLMGeneration:
            _ = args, kwargs
            return LLMGeneration(
                {"answer": {"kind": "text", "content": "summary via fallback"}, "should_answer_to_user": True},
                response_id=None,
                total_tokens=7,
            )

    client = _FallbackClientNoResponseId()
    memory = _StubMemory()
    state = SessionStateService()
    state.track_tokens("s1", 20)
    state.set_previous_response_id("s1", "resp-previous")
    await memory.append_history("s1", "user", "hi")
    prompt_service = PromptService(
        llm_client=cast(LLMClient, client),
        tools=[],
        environment_prompt_fragment="",
        logger=logging.getLogger("test"),
    )
    service = HistoryCompactionService(
        memory=cast(MemoryBackend, memory),
        llm_client=cast(LLMClient, client),
        session_state=state,
        prompt_service=prompt_service,
        logger=logging.getLogger("test"),
        max_history_tokens=10,
        compaction_user_request="Please compact the current conversation memory.",
    )

    result = await service.compact_history_if_needed(
        "s1",
        prompt_cache_key="telegram:1",
        system_prompt="system",
        notify=True,
        responses_state_mode="previous_response_id",
    )

    assert result.performed is True
    assert state.get_previous_response_id("s1") is None
    assert memory._store["s1"][1].content == "summary via fallback"


@pytest.mark.asyncio
async def test_runtime_service_returns_guardrail_resolved_text() -> None:
    service = RuntimeOrchestrationService(
        runtime=cast(AgentRuntime, _StubRuntime()),
        llm_client=cast(LLMClient, _StubClient()),
        guardrail=_ResolvedGuardrail(),
        session_state=SessionStateService(),
        logger=logging.getLogger("test"),
    )

    result = await service.run_with_agent_runtime(
        session_id="s1",
        history=[],
        model_text="hi",
        model_user_content=None,
        system_prompt="system",
        tool_context=ToolContext(),
        prompt_cache_key=None,
        previous_response_id=None,
        chat_id=1,
        channel="telegram",
        response_schema={"type": "object"},
    )

    assert result.should_reply is True
    assert result.render.text == "resolved"
    assert result.tokens_used == 7

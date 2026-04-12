from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from llm_async.models import Tool

from minibot.app.agent_registry import AgentRegistry
from minibot.app.agent_runtime import AgentRuntime, RuntimeResult
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
from minibot.core.agents import AgentSpec
from minibot.core.channels import ChannelMessage
from minibot.core.memory import MemoryBackend, MemoryEntry
from minibot.llm.provider_factory import LLMClient, LLMCompaction, LLMGeneration
from minibot.llm.tools.base import ToolBinding, ToolContext


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
            MemoryEntry(role=role, content=content, created_at=datetime.now(UTC))
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
            payload="ignored",
            response_id="resp-1",
            state=AgentState(messages=[AgentMessage(role="assistant", content=[MessagePart(type="text", text="x")])]),
            total_tokens=4,
        )


class _ResolvedGuardrail:
    async def apply(self, **_: Any) -> GuardrailDecision:
        return GuardrailDecision(requires_retry=False, resolved_render_text="resolved", tokens_used=3)


class _CountingGuardrail:
    def __init__(self) -> None:
        self.calls = 0

    async def apply(self, **_: Any) -> GuardrailDecision:
        self.calls += 1
        return GuardrailDecision(requires_retry=False)


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
        original_kind="markdown",
        parse_error="can't parse entities",
        original_content="*Hello",
    )

    assert "Telegram" in prompt
    assert "can't parse entities" in prompt
    assert "placeholder" in prompt


def test_prompt_service_prefers_task_delegation_guidance_when_task_tools_available() -> None:
    tools = [
        ToolBinding(tool=Tool(name="spawn_task", description="", parameters={"type": "object"}), handler=_noop_tool),
        ToolBinding(tool=Tool(name="cancel_task", description="", parameters={"type": "object"}), handler=_noop_tool),
        ToolBinding(tool=Tool(name="list_tasks", description="", parameters={"type": "object"}), handler=_noop_tool),
        ToolBinding(tool=Tool(name="invoke_agent", description="", parameters={"type": "object"}), handler=_noop_tool),
    ]
    prompt_service = PromptService(
        llm_client=cast(LLMClient, _StubClient()),
        tools=tools,
        environment_prompt_fragment="",
        logger=logging.getLogger("test"),
        agent_registry=AgentRegistry(
            [AgentSpec(name="worker", description="Does work", system_prompt="x", source_path=Path("worker.md"))]
        ),
    )

    prompt = prompt_service.compose_system_prompt("telegram")

    assert "Asynchronous delegation is available now via `spawn_task`" in prompt
    assert "`invoke_agent` is also available as a local fallback" in prompt
    assert 'metadata.source == "task_worker"' in prompt
    assert "Use `list_tasks` to verify which tasks are still active." in prompt


def test_prompt_service_uses_invoke_agent_guidance_when_task_tools_unavailable() -> None:
    tools = [
        ToolBinding(tool=Tool(name="invoke_agent", description="", parameters={"type": "object"}), handler=_noop_tool),
        ToolBinding(
            tool=Tool(name="fetch_agent_info", description="", parameters={"type": "object"}),
            handler=_noop_tool,
        ),
    ]
    prompt_service = PromptService(
        llm_client=cast(LLMClient, _StubClient()),
        tools=tools,
        environment_prompt_fragment="",
        logger=logging.getLogger("test"),
        agent_registry=AgentRegistry(
            [AgentSpec(name="worker", description="Does work", system_prompt="x", source_path=Path("worker.md"))]
        ),
    )

    prompt = prompt_service.compose_system_prompt("telegram")

    assert "Delegation is available now via `invoke_agent`" in prompt
    assert "`fetch_agent_info` is available" in prompt
    assert "Asynchronous delegation is available now via `spawn_task`" not in prompt
    assert 'metadata.source == "task_worker"' not in prompt


async def _noop_tool(_: Any, __: ToolContext) -> dict[str, Any]:
    return {}


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
                "summary via fallback",
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
                "summary via fallback",
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
    session_state = SessionStateService()
    service = RuntimeOrchestrationService(
        runtime=cast(AgentRuntime, _StubRuntime()),
        llm_client=cast(LLMClient, _StubClient()),
        guardrail=_ResolvedGuardrail(),
        session_state=session_state,
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
    )

    assert result.should_reply is True
    assert result.render.text == "resolved"
    assert result.tokens_used == 7
    assert session_state.current_tokens("s1") == 7


@pytest.mark.asyncio
async def test_runtime_service_skips_guardrail_when_tool_messages_exist() -> None:
    class _ToolRuntime:
        async def run(self, **_: Any) -> RuntimeResult:
            return RuntimeResult(
                payload="done",
                response_id="resp-1",
                state=AgentState(
                    messages=[
                        AgentMessage(role="assistant", content=[MessagePart(type="text", text="tool call happened")]),
                        AgentMessage(
                            role="tool",
                            name="read_file",
                            content=[MessagePart(type="text", text='{"ok":true}')],
                        ),
                    ]
                ),
                total_tokens=4,
            )

    guardrail = _CountingGuardrail()
    service = RuntimeOrchestrationService(
        runtime=cast(AgentRuntime, _ToolRuntime()),
        llm_client=cast(LLMClient, _StubClient()),
        guardrail=guardrail,
        session_state=SessionStateService(),
        logger=logging.getLogger("test"),
    )

    result = await service.run_with_agent_runtime(
        session_id="s1",
        history=[],
        model_text="read file",
        model_user_content=None,
        system_prompt="system",
        tool_context=ToolContext(),
        prompt_cache_key=None,
        previous_response_id=None,
        chat_id=1,
        channel="telegram",
    )

    assert guardrail.calls == 0
    assert result.should_reply is True
    assert result.render.text == "done"


@pytest.mark.asyncio
async def test_runtime_service_calls_guardrail_when_no_tool_messages() -> None:
    guardrail = _CountingGuardrail()
    service = RuntimeOrchestrationService(
        runtime=cast(AgentRuntime, _StubRuntime()),
        llm_client=cast(LLMClient, _StubClient()),
        guardrail=guardrail,
        session_state=SessionStateService(),
        logger=logging.getLogger("test"),
    )

    _ = await service.run_with_agent_runtime(
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
    )

    assert guardrail.calls == 1


@pytest.mark.asyncio
async def test_runtime_service_does_not_retry_when_delegation_times_out() -> None:
    class _TimeoutDelegationRuntime:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, **_: Any) -> RuntimeResult:
            self.calls += 1
            return RuntimeResult(
                payload="delegate timeout surfaced",
                response_id="resp-1",
                state=AgentState(
                    messages=[
                        AgentMessage(role="assistant", content=[MessagePart(type="text", text="delegating")]),
                        AgentMessage(
                            role="tool",
                            name="invoke_agent",
                            content=[
                                MessagePart(
                                    type="json",
                                    value={
                                        "ok": False,
                                        "agent": "playwright_mcp_agent",
                                        "result_status": "timeout",
                                        "error_code": "delegated_timeout",
                                        "error": "delegated agent timed out waiting for provider response",
                                    },
                                )
                            ],
                        ),
                    ]
                ),
                total_tokens=3,
            )

    runtime = _TimeoutDelegationRuntime()
    service = RuntimeOrchestrationService(
        runtime=cast(AgentRuntime, runtime),
        llm_client=cast(LLMClient, _StubClient()),
        guardrail=_CountingGuardrail(),
        session_state=SessionStateService(),
        logger=logging.getLogger("test"),
    )

    result = await service.run_with_agent_runtime(
        session_id="s1",
        history=[],
        model_text="delegate this",
        model_user_content=None,
        system_prompt="system",
        tool_context=ToolContext(),
        prompt_cache_key=None,
        previous_response_id=None,
        chat_id=1,
        channel="telegram",
    )

    assert runtime.calls == 1
    assert result.render.text == "delegate timeout surfaced"
    assert result.agent_trace == [
        {
            "agent": "minibot",
            "decision": "invoke_agent",
            "target": "playwright_mcp_agent",
            "ok": False,
            "result_status": "timeout",
            "error": "delegated agent timed out waiting for provider response",
        }
    ]

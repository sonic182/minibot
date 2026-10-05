from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from llm_async.models import Tool

from minibot.app.agent_runtime import RuntimeResult
from minibot.app.handlers.services import (
    AudioAutoTranscribePolicy,
    AudioAutoTranscriptionService,
    LLMTurnService,
    ToolBindingAudioTranscriptionExecutor,
    build_llm_turn_service,
)
from minibot.app.tool_use_guardrail import NoopToolUseGuardrail
from minibot.app.turn_decision import ShadowTurnDecision
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart
from minibot.core.channels import ChannelMessage, ChannelResponse, RenderableResponse, session_id_for
from minibot.core.decisions import DecisionAnswer, DecisionResult
from minibot.core.events import MessageEvent
from minibot.llm.errors import ProviderHTTPError
from minibot.llm.provider_factory import LLMClient, LLMGeneration
from minibot.llm.tools.base import ToolBinding, ToolContext
from tests.fixtures.memory import InMemoryMemoryStore as StubMemory


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
        latest_output_tokens: int | None = None,
    ) -> None:
        self.payload = payload
        self.latest_output_tokens = latest_output_tokens
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
            latest_output_tokens=self.latest_output_tokens,
        )

    def is_responses_provider(self) -> bool:
        return self._is_responses

    def supports_responses_compaction(self) -> bool:
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


class FailingRuntime:
    async def run(self, **_: Any) -> RuntimeResult:
        raise RuntimeError("runtime exploded")


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
        "hello",
        responses_provider=True,
        response_id="resp-1",
    )

    response = await service.handle(_message_event("ping"))

    assert response.text == "hello"
    assert response.metadata.get("should_reply") is True
    assert stub_client.calls[-1]["kwargs"].get("prompt_cache_key") == "telegram:1"


@pytest.mark.asyncio
async def test_turn_service_runs_a_task_result_as_a_continuation_turn() -> None:
    service, client, memory = _service("noted")
    result_text = "Background task t1 finished. <task_output>\nthe answer\n</task_output>"
    event = MessageEvent(
        message=_message(
            text=result_text,
            user_id=1,
            chat_id=1,
            metadata={"source": "task_result", "task_id": "t1", "task_chain_depth": 2},
        )
    )

    response = await service.handle(event)

    assert client.calls[-1]["kwargs"]["tool_context"].task_chain_depth == 2
    assert response.metadata["task_continuation"] is True
    history = await memory.get_history(session_id_for(event.message))
    assert [(entry.role, entry.content) for entry in history] == [("user", result_text), ("assistant", "noted")]

    plain = await service.handle(_message_event("ping"))

    assert "task_continuation" not in plain.metadata
    assert client.calls[-1]["kwargs"]["tool_context"].task_chain_depth == 0


@pytest.mark.asyncio
async def test_turn_service_includes_usage_trace_metadata() -> None:
    memory = StubMemory()
    stub_client = StubLLMClient(
        "hello",
        is_responses=True,
        provider="openai_responses",
        total_tokens=33,
        input_tokens=21,
        output_tokens=12,
        latest_output_tokens=12,
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
async def test_turn_service_keeps_the_last_step_output_as_compaction_pressure() -> None:
    stub_client = StubLLMClient("hello", input_tokens=20, output_tokens=30, latest_output_tokens=5)
    service = build_llm_turn_service(
        memory=cast(Any, StubMemory()),
        llm_client=cast(LLMClient, stub_client),
        tool_use_guardrail=NoopToolUseGuardrail(),
    )

    await service.handle(_message_event("ping"))

    assert list(service._session_state.session_latest_output_tokens.values()) == [5]


@pytest.mark.asyncio
async def test_turn_service_ignores_accumulated_output_when_last_step_output_is_missing() -> None:
    stub_client = StubLLMClient(
        "hello",
        total_tokens=120,
        input_tokens=70,
        output_tokens=35,
        latest_output_tokens=None,
    )
    service = build_llm_turn_service(
        memory=cast(Any, StubMemory()),
        llm_client=cast(LLMClient, stub_client),
        max_history_tokens=100,
        tool_use_guardrail=NoopToolUseGuardrail(),
    )

    response = await service.handle(_message_event("ping"))

    assert response.metadata["token_trace"]["compaction_performed"] is False
    assert response.metadata["token_trace"]["session_total_tokens"] == 70
    assert response.metadata["token_trace"]["turn_total_tokens"] == 120


@pytest.mark.asyncio
async def test_turn_service_compaction_endpoint_updates_previous_response_id() -> None:
    service, client, memory = _service(
        "ok",
        response_id="resp-1",
        responses_provider=True,
        provider="openai_responses",
        responses_state_mode="previous_response_id",
        max_history_tokens=50,
        notify_compaction_updates="brief",
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
    ]


@pytest.mark.asyncio
async def test_turn_service_fallback_compaction_updates_previous_response_id() -> None:
    class _FallbackCompactionClient(StubLLMClient):
        def __init__(self) -> None:
            super().__init__(
                "ok",
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
        notify_compaction_updates="brief",
        tool_use_guardrail=NoopToolUseGuardrail(),
    )

    await service.handle(_message_event("one"))

    session_id = session_id_for(_message_event("one").message)
    assert service.session_state.get_previous_response_id(session_id) == "cmp-fallback"


@pytest.mark.asyncio
async def test_turn_service_reuses_previous_response_id_when_mode_enabled() -> None:
    memory = StubMemory()
    stub_client = StubLLMClient(
        "hello",
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
    client = StubLLMClient("ok")
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
async def test_turn_service_reports_provider_out_of_credits() -> None:
    class OutOfCreditsLLMClient(StubLLMClient):
        async def generate(self, *args: Any, **kwargs: Any) -> LLMGeneration:
            _ = args, kwargs
            raise ProviderHTTPError(
                403,
                '{"code":"permission-denied","error":"Your team has either used all '
                'available credits or reached its monthly spending limit."}',
            )

    memory = StubMemory()
    service = build_llm_turn_service(
        memory=cast(Any, memory),
        llm_client=cast(LLMClient, OutOfCreditsLLMClient(payload=None)),
        tool_use_guardrail=NoopToolUseGuardrail(),
    )
    logger = logging.getLogger("minibot.handler")
    original_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        response = await service.handle(_message_event("ping"))
    finally:
        logger.setLevel(original_level)

    assert "out of credits" in response.text
    assert "monthly spending limit" in response.text


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
                payload="Let me check.",
                response_id="r1",
                state=AgentState(
                    messages=[AgentMessage(role="assistant", content=[MessagePart(type="text", text="x")])]
                ),
            ),
            RuntimeResult(
                payload="Done via tool.",
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
                payload="saved",
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
                payload="patched",
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
        "*fixed*",
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
        "ok",
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


@pytest.mark.asyncio
async def test_turn_service_runtime_exception_returns_fallback_response() -> None:
    service, _, memory = _service(
        "unused",
    )
    service.set_runtime(cast(Any, FailingRuntime()))
    event = _message_event("ping")

    response = await service.handle(event)

    assert response.metadata.get("should_reply") is True
    assert response.text == "Sorry, I couldn't answer right now."
    history = await memory.get_history(session_id_for(event.message))
    assert [(entry.role, entry.content) for entry in history] == [("user", "ping")]


@pytest.mark.asyncio
async def test_turn_service_keeps_history_per_chat_for_one_owner() -> None:
    service, _, memory = _service("ok", owner_id="primary")
    owner_chat = _message(user_id=7, chat_id=100)
    other_chat = _message(user_id=7, chat_id=200)

    for text in ("first question", "second question"):
        await service.handle(MessageEvent(message=_message(text=text, user_id=7, chat_id=100)))
    await service.handle(MessageEvent(message=_message(text="unrelated chat", user_id=7, chat_id=200)))

    owner_history = await memory.get_history(session_id_for(owner_chat))
    other_history = await memory.get_history(session_id_for(other_chat))

    assert len(owner_history) == 4
    assert [entry.role for entry in owner_history] == ["user", "assistant", "user", "assistant"]
    assert len(other_history) == 2
    assert not [entry for entry in other_history if "question" in entry.content]


class _RecordCollector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def decision_records() -> Any:
    collector = _RecordCollector()
    logger = logging.getLogger("minibot.turn_decision")
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(collector)
    yield collector.records
    logger.removeHandler(collector)
    logger.setLevel(previous_level)


async def _drain_background_tasks() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_turn_service_shadow_decision_logs_choice_without_changing_the_reply(decision_records: Any) -> None:
    decision_client = AsyncMock()
    decision_client.ask.return_value = DecisionResult(
        model="inception/mercury-decide-20260930",
        answers={
            "route": DecisionAnswer(
                type="choice", choice="answer_directly", probabilities={"answer_directly": 0.98}, confidence=0.98
            ),
            "needs_web": DecisionAnswer(type="noul", noul=0.02),
        },
        cost=0.0,
        latency_seconds=0.31,
    )
    shadow = ShadowTurnDecision(client=decision_client, tools=[], timeout_seconds=3.0)
    service, _, _ = _service("hello", turn_decision=shadow)

    response = await service.handle(_message_event("ping"))
    await _drain_background_tasks()

    assert response.text == "hello"
    assert decision_client.ask.await_args.args[0]["user_message"] == "ping"
    record = next(item for item in decision_records if item.getMessage() == "turn decision")
    assert record.route == "answer_directly"
    assert record.needs_web == 0.02
    assert record.handed_off is False


@pytest.mark.asyncio
async def test_turn_service_shadow_decision_failure_never_breaks_the_turn(decision_records: Any) -> None:
    decision_client = AsyncMock()
    decision_client.ask.side_effect = RuntimeError("decision backend down")
    shadow = ShadowTurnDecision(client=decision_client, tools=[], timeout_seconds=3.0)
    service, _, _ = _service("hello", turn_decision=shadow)

    response = await service.handle(_message_event("ping"))
    await _drain_background_tasks()

    assert response.text == "hello"
    assert [item.getMessage() for item in decision_records] == ["turn decision failed"]


@pytest.mark.asyncio
async def test_turn_service_records_and_forwards_messages_sent_during_the_turn() -> None:
    from minibot.app.turn_inbox import TurnInbox

    inbox = TurnInbox()
    drained: list[AgentMessage] = []

    class _SteeredRuntime(StubRuntime):
        async def run(self, **kwargs: Any) -> RuntimeResult:
            inbox.put(_message_event("also Barcelona"))
            turn_input = kwargs["turn_input"]
            assert turn_input.has_pending()
            drained.extend(await turn_input.drain())
            return await super().run(**kwargs)

    service, _, memory = _service("unused", provider="openrouter")
    result = RuntimeResult(payload="Madrid and Barcelona", response_id=None, state=AgentState())
    service.set_runtime(cast(Any, _SteeredRuntime([result])))

    response = await service.handle(_message_event("search Madrid"), turn_input=inbox)

    assert response.text == "Madrid and Barcelona"
    assert [(message.role, message.content[0].text, message.metadata) for message in drained] == [
        ("user", "also Barcelona", {"steering": True})
    ]
    history = await memory.get_history(session_id_for(_message(chat_id=1)))
    assert [(entry.role, entry.content) for entry in history] == [
        ("user", "search Madrid"),
        ("user", "also Barcelona"),
        ("assistant", "Madrid and Barcelona"),
    ]
    assert len(inbox.consumed) == 1


@pytest.mark.asyncio
async def test_turn_service_requeues_a_mid_turn_message_it_could_not_record() -> None:
    from minibot.app.turn_inbox import TurnInbox

    class _FailingMemory(StubMemory):
        async def append_history(self, session_id: str, role: str, content: str, **kwargs: Any) -> None:
            if content == "also Barcelona":
                raise RuntimeError("database is locked")
            await super().append_history(session_id, role, content, **kwargs)

    inbox = TurnInbox()

    class _SteeredRuntime(StubRuntime):
        async def run(self, **kwargs: Any) -> RuntimeResult:
            inbox.put(_message_event("also Barcelona"))
            await kwargs["turn_input"].drain()
            return await super().run(**kwargs)

    service, _, _ = _service("unused", provider="openrouter", memory=_FailingMemory())
    result = RuntimeResult(payload="unused", response_id=None, state=AgentState())
    service.set_runtime(cast(Any, _SteeredRuntime([result])))

    await service.handle(_message_event("search Madrid"), turn_input=inbox)

    assert inbox.consumed == []
    assert [event.message.text for event in inbox.leftover()] == ["also Barcelona"]

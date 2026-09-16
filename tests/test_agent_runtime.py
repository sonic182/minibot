from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from minibot.app.agent_runtime import _CONTINUE_AFTER_COMPACTION, AgentRuntime
from minibot.core.agent_runtime import (
    AgentMessage,
    AgentState,
    AppendMessageDirective,
    MessagePart,
    ToolResult,
)
from minibot.core.tasks import TaskStopReason
from minibot.llm.provider_factory import LLMClient, LLMCompletionStep, ToolExecutionRecord
from minibot.llm.tools.base import ToolContext
from tests.fixtures.llm.fakes import FakeMessage as _FakeMessage
from tests.fixtures.llm.fakes import FakeToolCall as _FakeToolCall


class _StubRuntimeLLMClient:
    def __init__(
        self,
        steps: list[LLMCompletionStep],
        executions: list[list[ToolExecutionRecord]],
        *,
        is_responses_provider: bool = False,
        responses_state_mode: str = "full_messages",
    ) -> None:
        self._steps = steps
        self._executions = executions
        self._is_responses_provider = is_responses_provider
        self._responses_state_mode = responses_state_mode
        self.complete_once_calls = 0
        self.execute_calls = 0
        self.complete_once_kwargs: list[dict[str, Any]] = []

    async def complete_once(self, **_: Any) -> LLMCompletionStep:
        self.complete_once_kwargs.append(dict(_))
        step = self._steps[self.complete_once_calls]
        self.complete_once_calls += 1
        return step

    async def execute_tool_calls_for_runtime(self, *args: Any, **kwargs: Any) -> list[ToolExecutionRecord]:
        _ = args, kwargs
        records = self._executions[self.execute_calls]
        self.execute_calls += 1
        return records

    def is_responses_provider(self) -> bool:
        return self._is_responses_provider

    def responses_state_mode(self) -> str:
        return self._responses_state_mode

    def media_input_mode(self) -> str:
        return "responses"


def _ping_state() -> AgentState:
    return AgentState(messages=[AgentMessage(role="user", content=[MessagePart(type="text", text="ping")])])


def _runtime(llm_client: _StubRuntimeLLMClient, *, tools: list[Any] | None = None, **kwargs: Any) -> AgentRuntime:
    return AgentRuntime(llm_client=cast(LLMClient, llm_client), tools=tools or [], **kwargs)


def _http_tool_call(name: str = "http_request") -> _FakeToolCall:
    return _FakeToolCall(id="call-1", function={"name": name, "arguments": "{}"})


def _tool_step(tool_call: _FakeToolCall, response_id: str, *, total_tokens: int = 3) -> LLMCompletionStep:
    return LLMCompletionStep(
        message=_FakeMessage(content="", tool_calls=[tool_call]),
        response_id=response_id,
        total_tokens=total_tokens,
    )


def _final_step(response_id: str, *, content: str = "done", total_tokens: int = 3) -> LLMCompletionStep:
    return LLMCompletionStep(
        message=_FakeMessage(content=content),
        response_id=response_id,
        total_tokens=total_tokens,
    )


def _http_record(*, content: str, body: Any = "ok") -> ToolExecutionRecord:
    return ToolExecutionRecord(
        tool_name="http_request",
        call_id="call-1",
        message_payload={"role": "tool", "content": content},
        result=ToolResult(content={"status": 200, "body": body}),
    )


def _failure_record(*, content: str, signature: str) -> ToolExecutionRecord:
    return ToolExecutionRecord(
        tool_name="http_request",
        call_id="call-1",
        message_payload={"role": "tool", "content": content},
        result=ToolResult(
            content={
                "ok": False,
                "tool": "http_request",
                "error_code": "tool_execution_failed",
                "error": "boom",
                "failure_signature": signature,
                "is_repeated_failure_candidate": True,
            }
        ),
    )


def _responses_record() -> ToolExecutionRecord:
    return ToolExecutionRecord(
        tool_name="http_request",
        call_id="call-1",
        message_payload={"type": "function_call_output", "call_id": "call-1", "output": '{"status": 200}'},
        result=ToolResult(content={"status": 200}),
    )


@pytest.mark.asyncio
async def test_runtime_returns_final_message_without_tool_calls() -> None:
    llm_client = _StubRuntimeLLMClient(
        steps=[
            LLMCompletionStep(
                message=_FakeMessage(content="hello"), response_id="resp-1", total_tokens=7, input_tokens=5
            )
        ],
        executions=[],
    )
    runtime = _runtime(llm_client)

    result = await runtime.run(state=_ping_state(), tool_context=ToolContext(owner_id="1"))

    assert result.payload == "hello"
    assert result.response_id == "resp-1"
    assert result.total_tokens == 7
    assert result.input_tokens == 5
    assert result.state.messages[-1].role == "assistant"


@pytest.mark.asyncio
async def test_runtime_returns_final_message_without_tool_retry_when_tools_are_available() -> None:
    llm_client = _StubRuntimeLLMClient(
        steps=[LLMCompletionStep(message=_FakeMessage(content="hello"), response_id="resp-1", total_tokens=7)],
        executions=[],
    )
    runtime = _runtime(llm_client, tools=[cast(Any, object())])

    result = await runtime.run(state=_ping_state(), tool_context=ToolContext(owner_id="1"))

    assert result.payload == "hello"
    assert result.response_id == "resp-1"
    assert result.total_tokens == 7
    assert llm_client.complete_once_calls == 1
    assert llm_client.execute_calls == 0


@pytest.mark.asyncio
async def test_runtime_applies_append_message_directive_for_trusted_tool() -> None:
    tool_call = _http_tool_call("self_insert_artifact")
    steps = [
        _tool_step(tool_call, "resp-1", total_tokens=4),
        _final_step("resp-2", total_tokens=6),
    ]
    directive = AppendMessageDirective(
        type="append_message",
        message=AgentMessage(role="user", content=[MessagePart(type="text", text="analyze this")]),
    )
    executions = [
        [
            ToolExecutionRecord(
                tool_name="self_insert_artifact",
                call_id="call-1",
                message_payload={"role": "tool", "content": "ok"},
                result=ToolResult(content={"status": "ok"}, directives=[directive]),
            )
        ]
    ]
    llm_client = _StubRuntimeLLMClient(steps=steps, executions=executions)
    runtime = _runtime(llm_client, allowed_append_message_tools=["self_insert_artifact"])

    result = await runtime.run(state=_ping_state(), tool_context=ToolContext(owner_id="1"))

    assert result.payload == "done"
    assert result.total_tokens == 10
    assert any(message.metadata.get("synthetic") is True for message in result.state.messages)
    assert any(message.metadata.get("source_tool") == "self_insert_artifact" for message in result.state.messages)


@pytest.mark.asyncio
async def test_runtime_resends_response_tool_history_for_stateless_responses_provider() -> None:
    llm_client = _StubRuntimeLLMClient(
        steps=[_tool_step(_http_tool_call(), "resp-1"), _final_step("resp-2")],
        executions=[[_responses_record()]],
        is_responses_provider=True,
    )

    result = await _runtime(llm_client).run(state=_ping_state(), tool_context=ToolContext(owner_id="1"))

    assert result.payload == "done"
    follow_up = llm_client.complete_once_kwargs[1]
    assert follow_up["previous_response_id"] is None
    assert any(message.get("tool_calls") for message in follow_up["messages"])
    assert {"type": "function_call_output", "call_id": "call-1", "output": '{"status": 200}'} in follow_up["messages"]


@pytest.mark.asyncio
async def test_runtime_sends_only_tool_output_for_stateful_responses_provider() -> None:
    llm_client = _StubRuntimeLLMClient(
        steps=[_tool_step(_http_tool_call(), "resp-1"), _final_step("resp-2")],
        executions=[[_responses_record()]],
        is_responses_provider=True,
        responses_state_mode="previous_response_id",
    )

    result = await _runtime(llm_client).run(state=_ping_state(), tool_context=ToolContext(owner_id="1"))

    assert result.payload == "done"
    follow_up = llm_client.complete_once_kwargs[1]
    assert follow_up["previous_response_id"] == "resp-1"
    assert follow_up["messages"] == [
        {"type": "function_call_output", "call_id": "call-1", "output": '{"status": 200}'}
    ]


@pytest.mark.asyncio
async def test_runtime_recovers_pseudo_tool_call_from_text() -> None:
    steps = [
        LLMCompletionStep(
            message=_FakeMessage(content='<tool_call>{"name":"http_request","arguments":{}}</tool_call>'),
            response_id="resp-1",
            total_tokens=4,
        ),
        LLMCompletionStep(message=_FakeMessage(content="done"), response_id="resp-2", total_tokens=5),
    ]
    llm_client = _StubRuntimeLLMClient(steps=steps, executions=[])
    runtime = _runtime(llm_client)
    runtime._tools = [cast(Any, object())]

    result = await runtime.run(state=_ping_state(), tool_context=ToolContext(owner_id="1"))

    assert result.payload == "done"
    assert llm_client.complete_once_calls == 2
    assert llm_client.execute_calls == 0
    second_call_messages = llm_client.complete_once_kwargs[1]["messages"]
    assert any(
        message["role"] == "user" and "tool calling interface" in message["content"]
        for message in second_call_messages
    )


@pytest.mark.asyncio
async def test_runtime_nudges_and_continues_on_repeated_identical_tool_failures() -> None:
    # A call that cannot succeed (an unreachable host, say) is a finding to report, not a reason
    # to throw away everything the run already produced. The runtime tells the agent to stop
    # retrying and keeps going; the step limits remain the ceiling for a genuine loop.
    tool_call = _http_tool_call()
    steps = [_tool_step(tool_call, "resp-1"), _tool_step(tool_call, "resp-2"), _final_step("resp-3")]
    executions = [
        [_failure_record(content="err1", signature="sig-1")],
        [_failure_record(content="err2", signature="sig-1")],
    ]
    llm_client = _StubRuntimeLLMClient(steps=steps, executions=executions)
    runtime = _runtime(llm_client)

    result = await runtime.run(state=_ping_state(), tool_context=ToolContext(owner_id="1"))

    assert llm_client.complete_once_calls == 3
    assert result.payload == "done"
    assert result.stop_reason is TaskStopReason.COMPLETED
    nudges = [
        message
        for message in result.state.messages
        if message.role == "user" and "Do not retry it" in (message.content[0].text or "")
    ]
    assert len(nudges) == 1, "the agent should be told once per distinct failure, not on every repeat"
    assert "http_request" in (nudges[0].content[0].text or "")


@pytest.mark.asyncio
async def test_runtime_stops_on_repeated_identical_successful_tool_outputs() -> None:
    legacy_tool_call = _http_tool_call("http_client")
    canonical_tool_call = _http_tool_call()
    steps = [
        _tool_step(legacy_tool_call, "resp-1"),
        _tool_step(canonical_tool_call, "resp-2"),
        _tool_step(legacy_tool_call, "resp-3"),
        _final_step("resp-4"),
    ]
    record = _http_record(
        content='{"status":200,"body":"{\\"bitcoin\\":{\\"usd\\":1}}"}',
        body='{"bitcoin":{"usd":1}}',
    )
    executions = [[record], [record], [record]]
    llm_client = _StubRuntimeLLMClient(steps=steps, executions=executions)
    runtime = _runtime(llm_client)
    runtime._tools = [cast(Any, object())]

    result = await runtime.run(state=_ping_state(), tool_context=ToolContext(owner_id="1"))

    assert llm_client.complete_once_calls == 3
    assert isinstance(result.payload, str)
    assert "tool-loop safeguard" in result.payload


@pytest.mark.asyncio
async def test_runtime_tool_loop_fallback_payload_is_plain_string() -> None:
    tool_call = _http_tool_call()
    steps = [_tool_step(tool_call, "resp-1"), _tool_step(tool_call, "resp-2"), _tool_step(tool_call, "resp-3")]
    record = _http_record(content='{"status":200,"body":"ok"}')
    executions = [[record], [record], [record]]
    llm_client = _StubRuntimeLLMClient(steps=steps, executions=executions)
    runtime = _runtime(llm_client, tools=[cast(Any, object())])

    result = await runtime.run(state=_ping_state(), tool_context=ToolContext(owner_id="1"))

    assert isinstance(result.payload, str)
    assert "tool-loop safeguard" in result.payload
    assert "http_request" in result.payload
    assert '{"status":200,"body":"ok"}' in result.payload


@pytest.mark.asyncio
async def test_runtime_does_not_stop_when_failure_signatures_differ() -> None:
    tool_call = _http_tool_call()
    steps = [_tool_step(tool_call, "resp-1"), _tool_step(tool_call, "resp-2"), _final_step("resp-3")]
    executions = [
        [_failure_record(content="err1", signature="sig-1")],
        [_failure_record(content="err2", signature="sig-2")],
    ]
    llm_client = _StubRuntimeLLMClient(steps=steps, executions=executions)
    runtime = _runtime(llm_client)

    result = await runtime.run(state=_ping_state(), tool_context=ToolContext(owner_id="1"))

    assert llm_client.complete_once_calls == 3
    assert result.payload == "done"


class _RecordingCompactor:
    """Stands in for RuntimeCompactor: the runtime only depends on this shape."""

    threshold_tokens = 100

    def __init__(self, *, response_id: str | None = "compacted-1") -> None:
        self._response_id = response_id
        self.calls: list[int] = []

    def should_compact(self, input_tokens: int | None) -> bool:
        return isinstance(input_tokens, int) and input_tokens >= self.threshold_tokens

    async def compact(self, state: AgentState, **_: Any) -> Any:
        self.calls.append(len(state.messages))
        state.messages[:] = [AgentMessage(role="user", content=[MessagePart(type="text", text="summary")])]
        return SimpleNamespace(performed=True, response_id=self._response_id, summary="summary")


@pytest.mark.asyncio
async def test_runtime_compacts_once_the_provider_reports_pressure() -> None:
    # The pressure signal is the provider's own input_tokens for the previous step, and the check
    # runs at the top of the loop so every tool call already has its result appended.
    tool_call = _http_tool_call()
    steps = [
        LLMCompletionStep(
            message=_FakeMessage(content="", tool_calls=[tool_call]),
            response_id="r1",
            total_tokens=3,
            input_tokens=150,
        ),
        _final_step("r2"),
    ]
    llm_client = _StubRuntimeLLMClient(steps, [[_http_record(content="ok")]])
    compactor = _RecordingCompactor()
    runtime = _runtime(llm_client, compactor=compactor)

    result = await runtime.run(state=_ping_state(), tool_context=ToolContext(owner_id="primary"))

    assert compactor.calls, "compaction never ran despite input_tokens above the threshold"
    assert result.payload == "done"


@pytest.mark.asyncio
async def test_runtime_sends_a_minimal_nudge_after_native_compaction_not_the_full_render() -> None:
    # Regression: chaining off previous_response_id normally sends only new content, relying on
    # the provider to hold the rest server-side. Right after a native compaction, the new
    # previous_response_id already IS that state, so falling back to a full local render here
    # would resend system+task+summary on top of a response that already holds them.
    tool_call = _http_tool_call()
    steps = [
        LLMCompletionStep(
            message=_FakeMessage(content="", tool_calls=[tool_call]),
            response_id="r1",
            total_tokens=3,
            input_tokens=150,
        ),
        _final_step("r2"),
    ]
    llm_client = _StubRuntimeLLMClient(
        steps,
        [[_http_record(content="ok")]],
        is_responses_provider=True,
        responses_state_mode="previous_response_id",
    )
    compactor = _RecordingCompactor(response_id="compacted-1")
    runtime = _runtime(llm_client, compactor=compactor)

    result = await runtime.run(state=_ping_state(), tool_context=ToolContext(owner_id="primary"))

    assert compactor.calls
    assert result.payload == "done"
    second_call_messages = llm_client.complete_once_kwargs[1]["messages"]
    assert second_call_messages == [{"role": "user", "content": _CONTINUE_AFTER_COMPACTION}]
    assert llm_client.complete_once_kwargs[1]["previous_response_id"] == "compacted-1"


@pytest.mark.asyncio
async def test_runtime_leaves_the_loop_alone_below_the_threshold() -> None:
    tool_call = _http_tool_call()
    steps = [
        LLMCompletionStep(
            message=_FakeMessage(content="", tool_calls=[tool_call]),
            response_id="r1",
            total_tokens=3,
            input_tokens=10,
        ),
        _final_step("r2"),
    ]
    llm_client = _StubRuntimeLLMClient(steps, [[_http_record(content="ok")]])
    compactor = _RecordingCompactor()
    runtime = _runtime(llm_client, compactor=compactor)

    result = await runtime.run(state=_ping_state(), tool_context=ToolContext(owner_id="primary"))

    assert compactor.calls == []
    assert result.payload == "done"


@pytest.mark.asyncio
async def test_runtime_without_a_compactor_is_unchanged() -> None:
    # The main turn passes no compactor; it must keep behaving exactly as before.
    tool_call = _http_tool_call()
    steps = [
        LLMCompletionStep(
            message=_FakeMessage(content="", tool_calls=[tool_call]),
            response_id="r1",
            total_tokens=3,
            input_tokens=10_000_000,
        ),
        _final_step("r2"),
    ]
    llm_client = _StubRuntimeLLMClient(steps, [[_http_record(content="ok")]])
    runtime = _runtime(llm_client)

    result = await runtime.run(state=_ping_state(), tool_context=ToolContext(owner_id="primary"))

    assert result.payload == "done"
    assert llm_client.complete_once_calls == 2

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest

from minibot.app.agent_runtime import AgentRuntime
from minibot.app.runtime_structured_output import RuntimeStructuredOutputValidator
from minibot.core.agent_runtime import (
    AgentMessage,
    AgentState,
    AppendMessageDirective,
    MessagePart,
    RuntimeLimits,
    ToolResult,
)
from minibot.llm.provider_factory import LLMClient
from minibot.llm.provider_factory import LLMCompletionStep, ToolExecutionRecord
from minibot.llm.tools.agent_delegate import _DelegatedPayload
from minibot.llm.tools.base import ToolContext


@dataclass
class _FakeToolCall:
    id: str
    type: str = "function"
    function: dict[str, Any] | None = None
    name: str | None = None
    input: dict[str, Any] | None = None


@dataclass
class _FakeMessage:
    content: Any
    tool_calls: list[_FakeToolCall] | None = None


class _StubRuntimeLLMClient:
    def __init__(self, steps: list[LLMCompletionStep], executions: list[list[ToolExecutionRecord]]) -> None:
        self._steps = steps
        self._executions = executions
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
        return False

    def media_input_mode(self) -> str:
        return "responses"


@pytest.mark.asyncio
async def test_runtime_returns_final_message_without_tool_calls() -> None:
    llm_client = _StubRuntimeLLMClient(
        steps=[LLMCompletionStep(message=_FakeMessage(content="hello"), response_id="resp-1", total_tokens=7)],
        executions=[],
    )
    runtime = AgentRuntime(llm_client=cast(LLMClient, llm_client), tools=[])
    state = AgentState(messages=[AgentMessage(role="user", content=[MessagePart(type="text", text="ping")])])

    result = await runtime.run(state=state, tool_context=ToolContext(owner_id="1"))

    assert result.payload == "hello"
    assert result.response_id == "resp-1"
    assert result.total_tokens == 7
    assert result.state.messages[-1].role == "assistant"


@pytest.mark.asyncio
async def test_runtime_returns_final_message_without_tool_retry_when_tools_are_available() -> None:
    llm_client = _StubRuntimeLLMClient(
        steps=[LLMCompletionStep(message=_FakeMessage(content="hello"), response_id="resp-1", total_tokens=7)],
        executions=[],
    )
    runtime = AgentRuntime(llm_client=cast(LLMClient, llm_client), tools=[cast(Any, object())])
    state = AgentState(messages=[AgentMessage(role="user", content=[MessagePart(type="text", text="ping")])])

    result = await runtime.run(state=state, tool_context=ToolContext(owner_id="1"))

    assert result.payload == "hello"
    assert result.response_id == "resp-1"
    assert result.total_tokens == 7
    assert llm_client.complete_once_calls == 1
    assert llm_client.execute_calls == 0


@pytest.mark.asyncio
async def test_runtime_applies_append_message_directive_for_trusted_tool() -> None:
    tool_call = _FakeToolCall(id="call-1", function={"name": "self_insert_artifact", "arguments": "{}"})
    steps = [
        LLMCompletionStep(
            message=_FakeMessage(content="", tool_calls=[tool_call]),
            response_id="resp-1",
            total_tokens=4,
        ),
        LLMCompletionStep(message=_FakeMessage(content="done"), response_id="resp-2", total_tokens=6),
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
    runtime = AgentRuntime(
        llm_client=cast(LLMClient, llm_client),
        tools=[],
        allowed_append_message_tools=["self_insert_artifact"],
    )
    state = AgentState(messages=[AgentMessage(role="user", content=[MessagePart(type="text", text="start")])])

    result = await runtime.run(state=state, tool_context=ToolContext(owner_id="1"))

    assert result.payload == "done"
    assert result.total_tokens == 10
    assert any(message.metadata.get("synthetic") is True for message in result.state.messages)
    assert any(message.metadata.get("source_tool") == "self_insert_artifact" for message in result.state.messages)


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
    runtime = AgentRuntime(llm_client=cast(LLMClient, llm_client), tools=[])
    runtime._tools = [cast(Any, object())]
    state = AgentState(messages=[AgentMessage(role="user", content=[MessagePart(type="text", text="ping")])])

    result = await runtime.run(state=state, tool_context=ToolContext(owner_id="1"))

    assert result.payload == "done"
    assert llm_client.complete_once_calls == 2
    assert llm_client.execute_calls == 0
    second_call_messages = llm_client.complete_once_kwargs[1]["messages"]
    assert any(
        message["role"] == "user" and "tool calling interface" in message["content"]
        for message in second_call_messages
    )


@pytest.mark.asyncio
async def test_runtime_validates_structured_output_with_ratchet() -> None:
    llm_client = _StubRuntimeLLMClient(
        steps=[
            LLMCompletionStep(
                message=_FakeMessage(
                    content='{"answer":{"kind":"markdown","content":"*done*"},"should_continue":false}'
                ),
                response_id="resp-1",
                total_tokens=7,
            )
        ],
        executions=[],
    )
    runtime = AgentRuntime(llm_client=cast(LLMClient, llm_client), tools=[])
    state = AgentState(messages=[AgentMessage(role="user", content=[MessagePart(type="text", text="ping")])])

    result = await runtime.run(
        state=state,
        tool_context=ToolContext(owner_id="1"),
        response_schema={"type": "object"},
    )

    assert isinstance(result.payload, dict)
    assert result.payload["answer"]["kind"] == "markdown"
    assert result.payload["answer"]["content"] == "*done*"
    assert result.payload["should_continue"] is False


@pytest.mark.asyncio
async def test_runtime_retries_invalid_structured_output_then_succeeds() -> None:
    llm_client = _StubRuntimeLLMClient(
        steps=[
            LLMCompletionStep(message=_FakeMessage(content="not-json"), response_id="resp-1", total_tokens=4),
            LLMCompletionStep(
                message=_FakeMessage(content='{"answer":{"kind":"text","content":"ok"},"should_continue":false}'),
                response_id="resp-2",
                total_tokens=5,
            ),
        ],
        executions=[],
    )
    runtime = AgentRuntime(llm_client=cast(LLMClient, llm_client), tools=[])
    state = AgentState(messages=[AgentMessage(role="user", content=[MessagePart(type="text", text="ping")])])

    result = await runtime.run(
        state=state,
        tool_context=ToolContext(owner_id="1"),
        response_schema={"type": "object"},
    )

    assert llm_client.complete_once_calls == 2
    assert isinstance(result.payload, dict)
    assert result.payload["answer"]["content"] == "ok"
    second_call_messages = llm_client.complete_once_kwargs[1]["messages"]
    assert any(message["role"] == "assistant" for message in second_call_messages)
    assert any(message["role"] == "user" and message["content"] != "ping" for message in second_call_messages)


@pytest.mark.asyncio
async def test_runtime_returns_non_user_answerable_structured_payload_without_retry() -> None:
    llm_client = _StubRuntimeLLMClient(
        steps=[
            LLMCompletionStep(
                message=_FakeMessage(
                    content='{"answer":{"kind":"text","content":"still working"},"should_continue":true}'
                ),
                response_id="resp-1",
                total_tokens=2,
            ),
        ],
        executions=[],
    )
    runtime = AgentRuntime(llm_client=cast(LLMClient, llm_client), tools=[cast(Any, object())])
    state = AgentState(messages=[AgentMessage(role="user", content=[MessagePart(type="text", text="ping")])])

    result = await runtime.run(
        state=state,
        tool_context=ToolContext(owner_id="1"),
        response_schema={"type": "object"},
    )

    assert result.payload["answer"]["kind"] == "text"
    assert result.payload["answer"]["content"] == "still working"
    assert result.payload["should_continue"] is True
    assert llm_client.complete_once_calls == 1


@pytest.mark.asyncio
async def test_runtime_returns_fallback_after_structured_output_exhausted_retries() -> None:
    llm_client = _StubRuntimeLLMClient(
        steps=[
            LLMCompletionStep(message=_FakeMessage(content="bad-1"), response_id="resp-1", total_tokens=1),
            LLMCompletionStep(message=_FakeMessage(content="bad-2"), response_id="resp-2", total_tokens=1),
            LLMCompletionStep(message=_FakeMessage(content="bad-3"), response_id="resp-3", total_tokens=1),
            LLMCompletionStep(message=_FakeMessage(content="bad-4"), response_id="resp-4", total_tokens=1),
        ],
        executions=[],
    )
    runtime = AgentRuntime(llm_client=cast(LLMClient, llm_client), tools=[])
    state = AgentState(messages=[AgentMessage(role="user", content=[MessagePart(type="text", text="ping")])])

    result = await runtime.run(
        state=state,
        tool_context=ToolContext(owner_id="1"),
        response_schema={"type": "object"},
    )

    assert llm_client.complete_once_calls == 4
    assert isinstance(result.payload, dict)
    assert result.payload["answer"]["kind"] == "text"
    assert "valid structured response" in result.payload["answer"]["content"]
    assert result.payload["should_continue"] is False


@pytest.mark.asyncio
async def test_runtime_retries_do_not_consume_step_budget() -> None:
    """M1 fix: RetryAction must not increment step; a retry should not use up the step budget."""
    valid_json = '{"answer":{"kind":"text","content":"ok"},"should_continue":false}'
    llm_client = _StubRuntimeLLMClient(
        steps=[
            LLMCompletionStep(message=_FakeMessage(content="not-json"), response_id="resp-1", total_tokens=3),
            LLMCompletionStep(message=_FakeMessage(content=valid_json), response_id="resp-2", total_tokens=5),
        ],
        executions=[],
    )
    runtime = AgentRuntime(
        llm_client=cast(LLMClient, llm_client),
        tools=[],
        limits=RuntimeLimits(max_steps=1),
    )
    state = AgentState(messages=[AgentMessage(role="user", content=[MessagePart(type="text", text="ping")])])

    result = await runtime.run(
        state=state,
        tool_context=ToolContext(owner_id="1"),
        response_schema={"type": "object"},
    )

    # Both LLM calls must have happened (retry did not exhaust max_steps=1)
    assert llm_client.complete_once_calls == 2
    assert isinstance(result.payload, dict)
    assert result.payload["answer"]["content"] == "ok"


@pytest.mark.asyncio
async def test_runtime_custom_validator_honors_caller_schema() -> None:
    """P1 fix: a custom validator with _DelegatedPayload must accept kind='json' without retrying."""
    delegated_json = '{"answer":{"kind":"json","content":"{\\"key\\":\\"value\\"}"},"should_continue":false}'
    llm_client = _StubRuntimeLLMClient(
        steps=[
            LLMCompletionStep(
                message=_FakeMessage(content=delegated_json),
                response_id="resp-1",
                total_tokens=10,
            )
        ],
        executions=[],
    )
    runtime = AgentRuntime(llm_client=cast(LLMClient, llm_client), tools=[])
    state = AgentState(messages=[AgentMessage(role="user", content=[MessagePart(type="text", text="ping")])])

    result = await runtime.run(
        state=state,
        tool_context=ToolContext(owner_id="1"),
        response_schema={"type": "object"},
        structured_validator=RuntimeStructuredOutputValidator(schema_model=_DelegatedPayload, max_attempts=3),
    )

    # No retries — validated on first attempt
    assert llm_client.complete_once_calls == 1
    assert isinstance(result.payload, dict)
    assert result.payload["answer"]["kind"] == "json"


@pytest.mark.asyncio
async def test_runtime_stops_on_repeated_identical_tool_failure_signatures() -> None:
    tool_call = _FakeToolCall(id="call-1", function={"name": "http_request", "arguments": "{}"})
    steps = [
        LLMCompletionStep(
            message=_FakeMessage(content="", tool_calls=[tool_call]),
            response_id="resp-1",
            total_tokens=3,
        ),
        LLMCompletionStep(
            message=_FakeMessage(content="", tool_calls=[tool_call]),
            response_id="resp-2",
            total_tokens=3,
        ),
        LLMCompletionStep(message=_FakeMessage(content="done"), response_id="resp-3", total_tokens=3),
    ]
    executions = [
        [
            ToolExecutionRecord(
                tool_name="http_request",
                call_id="call-1",
                message_payload={"role": "tool", "content": "err1"},
                result=ToolResult(
                    content={
                        "ok": False,
                        "tool": "http_request",
                        "error_code": "tool_execution_failed",
                        "error": "boom",
                        "failure_signature": "sig-1",
                        "is_repeated_failure_candidate": True,
                    }
                ),
            )
        ],
        [
            ToolExecutionRecord(
                tool_name="http_request",
                call_id="call-1",
                message_payload={"role": "tool", "content": "err2"},
                result=ToolResult(
                    content={
                        "ok": False,
                        "tool": "http_request",
                        "error_code": "tool_execution_failed",
                        "error": "boom",
                        "failure_signature": "sig-1",
                        "is_repeated_failure_candidate": True,
                    }
                ),
            )
        ],
    ]
    llm_client = _StubRuntimeLLMClient(steps=steps, executions=executions)
    runtime = AgentRuntime(llm_client=cast(LLMClient, llm_client), tools=[])
    state = AgentState(messages=[AgentMessage(role="user", content=[MessagePart(type="text", text="ping")])])

    result = await runtime.run(
        state=state,
        tool_context=ToolContext(owner_id="1"),
        response_schema={"type": "object"},
    )

    assert llm_client.complete_once_calls == 2
    assert isinstance(result.payload, dict)
    assert "same tool error repeatedly" in result.payload["answer"]["content"]
    assert result.payload["should_continue"] is False


@pytest.mark.asyncio
async def test_runtime_stops_on_repeated_identical_successful_tool_outputs() -> None:
    legacy_tool_call = _FakeToolCall(id="call-1", function={"name": "http_client", "arguments": "{}"})
    canonical_tool_call = _FakeToolCall(id="call-1", function={"name": "http_request", "arguments": "{}"})
    steps = [
        LLMCompletionStep(
            message=_FakeMessage(content="", tool_calls=[legacy_tool_call]),
            response_id="resp-1",
            total_tokens=3,
        ),
        LLMCompletionStep(
            message=_FakeMessage(content="", tool_calls=[canonical_tool_call]),
            response_id="resp-2",
            total_tokens=3,
        ),
        LLMCompletionStep(
            message=_FakeMessage(content="", tool_calls=[legacy_tool_call]),
            response_id="resp-3",
            total_tokens=3,
        ),
        LLMCompletionStep(message=_FakeMessage(content="done"), response_id="resp-4", total_tokens=3),
    ]
    executions = [
        [
            ToolExecutionRecord(
                tool_name="http_request",
                call_id="call-1",
                message_payload={"role": "tool", "content": '{"status":200,"body":"{\\"bitcoin\\":{\\"usd\\":1}}"}'},
                result=ToolResult(content={"status": 200, "body": '{"bitcoin":{"usd":1}}'}),
            )
        ],
        [
            ToolExecutionRecord(
                tool_name="http_request",
                call_id="call-1",
                message_payload={"role": "tool", "content": '{"status":200,"body":"{\\"bitcoin\\":{\\"usd\\":1}}"}'},
                result=ToolResult(content={"status": 200, "body": '{"bitcoin":{"usd":1}}'}),
            )
        ],
        [
            ToolExecutionRecord(
                tool_name="http_request",
                call_id="call-1",
                message_payload={"role": "tool", "content": '{"status":200,"body":"{\\"bitcoin\\":{\\"usd\\":1}}"}'},
                result=ToolResult(content={"status": 200, "body": '{"bitcoin":{"usd":1}}'}),
            )
        ],
    ]
    llm_client = _StubRuntimeLLMClient(steps=steps, executions=executions)
    runtime = AgentRuntime(llm_client=cast(LLMClient, llm_client), tools=[])
    runtime._tools = [cast(Any, object())]
    state = AgentState(messages=[AgentMessage(role="user", content=[MessagePart(type="text", text="ping")])])

    result = await runtime.run(
        state=state,
        tool_context=ToolContext(owner_id="1"),
        response_schema={"type": "object"},
    )

    assert llm_client.complete_once_calls == 3
    assert isinstance(result.payload, dict)
    assert result.payload["answer"]["kind"] == "text"
    assert "tool-loop safeguard" in result.payload["answer"]["content"]
    assert result.payload["should_continue"] is False
    assert result.payload["attachments"] == []


@pytest.mark.asyncio
async def test_runtime_tool_loop_fallback_payload_stays_structured() -> None:
    tool_call = _FakeToolCall(id="call-1", function={"name": "http_request", "arguments": "{}"})
    steps = [
        LLMCompletionStep(
            message=_FakeMessage(content="", tool_calls=[tool_call]),
            response_id="resp-1",
            total_tokens=3,
        ),
        LLMCompletionStep(
            message=_FakeMessage(content="", tool_calls=[tool_call]),
            response_id="resp-2",
            total_tokens=3,
        ),
        LLMCompletionStep(
            message=_FakeMessage(content="", tool_calls=[tool_call]),
            response_id="resp-3",
            total_tokens=3,
        ),
    ]
    executions = [
        [
            ToolExecutionRecord(
                tool_name="http_request",
                call_id="call-1",
                message_payload={"role": "tool", "content": '{"status":200,"body":"ok"}'},
                result=ToolResult(content={"status": 200, "body": "ok"}),
            )
        ],
        [
            ToolExecutionRecord(
                tool_name="http_request",
                call_id="call-1",
                message_payload={"role": "tool", "content": '{"status":200,"body":"ok"}'},
                result=ToolResult(content={"status": 200, "body": "ok"}),
            )
        ],
        [
            ToolExecutionRecord(
                tool_name="http_request",
                call_id="call-1",
                message_payload={"role": "tool", "content": '{"status":200,"body":"ok"}'},
                result=ToolResult(content={"status": 200, "body": "ok"}),
            )
        ],
    ]
    llm_client = _StubRuntimeLLMClient(steps=steps, executions=executions)
    runtime = AgentRuntime(llm_client=cast(LLMClient, llm_client), tools=[cast(Any, object())])
    state = AgentState(messages=[AgentMessage(role="user", content=[MessagePart(type="text", text="ping")])])

    result = await runtime.run(
        state=state,
        tool_context=ToolContext(owner_id="1"),
        response_schema={"type": "object"},
    )

    assert isinstance(result.payload, dict)
    assert result.payload["answer"]["kind"] == "text"
    assert "tool-loop safeguard" in result.payload["answer"]["content"]
    assert "http_request" in result.payload["answer"]["content"]
    assert '{"status":200,"body":"ok"}' in result.payload["answer"]["content"]
    assert result.payload["should_continue"] is False
    assert result.payload["attachments"] == []


@pytest.mark.asyncio
async def test_runtime_does_not_stop_when_failure_signatures_differ() -> None:
    tool_call = _FakeToolCall(id="call-1", function={"name": "http_request", "arguments": "{}"})
    steps = [
        LLMCompletionStep(
            message=_FakeMessage(content="", tool_calls=[tool_call]),
            response_id="resp-1",
            total_tokens=3,
        ),
        LLMCompletionStep(
            message=_FakeMessage(content="", tool_calls=[tool_call]),
            response_id="resp-2",
            total_tokens=3,
        ),
        LLMCompletionStep(message=_FakeMessage(content="done"), response_id="resp-3", total_tokens=3),
    ]
    executions = [
        [
            ToolExecutionRecord(
                tool_name="http_request",
                call_id="call-1",
                message_payload={"role": "tool", "content": "err1"},
                result=ToolResult(
                    content={
                        "ok": False,
                        "tool": "http_request",
                        "error_code": "tool_execution_failed",
                        "error": "boom",
                        "failure_signature": "sig-1",
                        "is_repeated_failure_candidate": True,
                    }
                ),
            )
        ],
        [
            ToolExecutionRecord(
                tool_name="http_request",
                call_id="call-1",
                message_payload={"role": "tool", "content": "err2"},
                result=ToolResult(
                    content={
                        "ok": False,
                        "tool": "http_request",
                        "error_code": "tool_execution_failed",
                        "error": "boom",
                        "failure_signature": "sig-2",
                        "is_repeated_failure_candidate": True,
                    }
                ),
            )
        ],
    ]
    llm_client = _StubRuntimeLLMClient(steps=steps, executions=executions)
    runtime = AgentRuntime(llm_client=cast(LLMClient, llm_client), tools=[])
    state = AgentState(messages=[AgentMessage(role="user", content=[MessagePart(type="text", text="ping")])])

    result = await runtime.run(state=state, tool_context=ToolContext(owner_id="1"))

    assert llm_client.complete_once_calls == 3
    assert result.payload == "done"

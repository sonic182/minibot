from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytest

from minibot.app.agent_runtime import AgentRuntime
from minibot.core.agent_runtime import (
    AgentMessage,
    AgentState,
    AppendMessageDirective,
    MessagePart,
    ToolResult,
)
from minibot.llm.provider_factory import LLMClient
from minibot.llm.provider_factory import LLMCompletionStep, ToolExecutionRecord
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
async def test_runtime_renders_managed_file_reference_to_input_file_data_url(tmp_path) -> None:
    managed_root = tmp_path / "files"
    managed_root.mkdir(parents=True, exist_ok=True)
    artifact_path = managed_root / "uploads" / "a.txt"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("hello", encoding="utf-8")
    llm_client = _StubRuntimeLLMClient(
        steps=[LLMCompletionStep(message=_FakeMessage(content="done"), response_id="resp-1")],
        executions=[],
    )
    runtime = AgentRuntime(llm_client=cast(LLMClient, llm_client), tools=[], managed_files_root=str(managed_root))
    state = AgentState(
        messages=[
            AgentMessage(
                role="user",
                content=[
                    MessagePart(
                        type="file",
                        source={"type": "managed_file", "path": "uploads/a.txt"},
                        mime="text/plain",
                        filename="a.txt",
                    )
                ],
            )
        ]
    )

    await runtime.run(state=state, tool_context=ToolContext(owner_id="1"))

    rendered_messages = llm_client.complete_once_kwargs[0]["messages"]
    user_content = rendered_messages[0]["content"]
    assert isinstance(user_content, list)
    assert user_content[0]["type"] == "input_file"
    assert user_content[0]["filename"] == "a.txt"
    assert user_content[0]["file_data"].startswith("data:text/plain;base64,")

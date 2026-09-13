from types import SimpleNamespace
from typing import Any

import pytest
from llm_async.models import Message, ToolCall

from minibot.adapters.config.schema import LLMMConfig
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart
from minibot.llm.provider_factory import LLMClient
from minibot.llm.providers.openai_responses import PatchedOpenAIResponsesProvider
from minibot.llm.services.runtime_message_renderer import RuntimeMessageRenderer


def _provider() -> PatchedOpenAIResponsesProvider:
    return PatchedOpenAIResponsesProvider(api_key="test-key")


def test_messages_to_input_replays_reasoning_item_before_function_call() -> None:
    reasoning_item = {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque"}
    tool_call = {"id": "fc_1", "type": "function", "function": {"name": "current_datetime", "arguments": "{}"}}
    messages = [
        {"role": "user", "content": "what time is it?"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_details": [reasoning_item],
            "tool_calls": [tool_call],
        },
        {"type": "function_call_output", "call_id": "fc_1", "output": "2026-09-13"},
    ]

    result = PatchedOpenAIResponsesProvider._messages_to_input(_provider(), messages)

    types = [item.get("type") or item.get("role") for item in result]
    assert types == ["user", "reasoning", "function_call", "function_call_output"]
    assert result[1] == reasoning_item
    assert result[2]["call_id"] == "fc_1"


def test_messages_to_input_matches_base_behavior_without_reasoning_details() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "fc_2", "type": "function", "function": {"name": "noop", "arguments": "{}"}}],
        }
    ]

    result = PatchedOpenAIResponsesProvider._messages_to_input(_provider(), messages)

    assert [item.get("type") for item in result] == ["function_call"]


@pytest.mark.asyncio
async def test_complete_once_replays_raw_reasoning_through_full_history(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    reasoning_item = {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque"}
    function_call = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "noop", "arguments": "{}"}

    class _ResponsesProvider(PatchedOpenAIResponsesProvider):
        async def acomplete(self, **_: Any) -> SimpleNamespace:
            return SimpleNamespace(
                main_response=Message(
                    role="assistant",
                    content="",
                    tool_calls=[ToolCall.from_responses_api_function_call("fc_1", "call_1", "noop", "{}")],
                ),
                original={"id": "resp_1", "output": [reasoning_item, function_call]},
            )

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai_responses", _ResponsesProvider)
    client = LLMClient(LLMMConfig(provider="openai_responses", api_key="test-key", model="test-model"))
    completion = await client.complete_once(messages=[{"role": "user", "content": "run the tool"}])

    renderer = RuntimeMessageRenderer(media_input_mode="responses", is_responses_provider=True)
    state = AgentState(
        messages=[
            AgentMessage(role="user", content=[MessagePart(type="text", text="run the tool")]),
            renderer.from_provider_assistant_tool_call_message(completion.message),
            AgentMessage(
                role="tool",
                tool_call_id="call_1",
                content=[MessagePart(type="text", text="tool output")],
            ),
        ]
    )

    provider = client._provider
    assert isinstance(provider, _ResponsesProvider)
    result = provider._messages_to_input(renderer.render_messages(state))

    assert [item.get("type") or item.get("role") for item in result] == [
        "user",
        "reasoning",
        "function_call",
        "function_call_output",
    ]
    assert result[1] == reasoning_item
    assert result[2]["call_id"] == "call_1"
    assert result[3]["call_id"] == "call_1"

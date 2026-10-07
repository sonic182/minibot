from types import SimpleNamespace
from typing import Any

import pytest
from llm_async_codex import CodexCredentials

from minibot.adapters.config.schema import LLMMConfig
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart
from minibot.llm.errors import ProviderResponseError
from minibot.llm.provider_factory import LLMClient
from minibot.llm.providers import openai_responses as openai_responses_module
from minibot.llm.providers.codex import PatchedCodexProvider
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


def test_codex_parse_response_keeps_raw_reasoning_items() -> None:
    reasoning_item = {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque"}
    function_call = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "noop", "arguments": "{}"}
    provider = PatchedCodexProvider(CodexCredentials(access_token="test-token"))

    message = provider._parse_response({"output": [reasoning_item, function_call]})

    assert message.reasoning_details == [reasoning_item]
    assert [call.function["name"] for call in message.tool_calls] == ["noop"]


def test_codex_messages_to_input_replays_reasoning_item_before_function_call() -> None:
    reasoning_item = {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque"}
    tool_call = {"id": "fc_1", "type": "function", "function": {"name": "current_datetime", "arguments": "{}"}}
    messages = [
        {"role": "user", "content": "what time is it?"},
        {"role": "assistant", "content": "", "reasoning_details": [reasoning_item], "tool_calls": [tool_call]},
        {"type": "function_call_output", "call_id": "fc_1", "output": "2026-09-13"},
    ]
    provider = PatchedCodexProvider(CodexCredentials(access_token="test-token"))

    result = provider._messages_to_input(messages)

    assert [item.get("type") or item.get("role") for item in result] == [
        "user",
        "reasoning",
        "function_call",
        "function_call_output",
    ]
    assert result[1] == reasoning_item


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
            original = {"id": "resp_1", "output": [reasoning_item, function_call]}
            return SimpleNamespace(main_response=self._parse_response(original), original=original)

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


@pytest.mark.asyncio
@pytest.mark.parametrize(("configured", "expected"), [(None, "0.160.0"), ("0.200.1", "0.200.1")])
async def test_codex_lists_models_with_the_configured_client_version(configured: str | None, expected: str) -> None:
    requested: list[str] = []

    async def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        requested.append(path)
        return {"models": [{"slug": "gpt-6.1-sol"}]}

    async def fresh_credentials() -> None:
        return None

    provider = PatchedCodexProvider(CodexCredentials(access_token="test-token"), models_client_version=configured)
    provider.request = request
    provider._ensure_fresh_credentials = fresh_credentials

    await provider._ensure_models_cache()

    assert requested == [f"/models?client_version={expected}"]


def _fake_stream(events: list[dict[str, Any]]) -> Any:
    async def _stream(*_: Any, **__: Any) -> Any:
        for event in events:
            yield event

    return _stream


async def _drain(provider: PatchedOpenAIResponsesProvider) -> Any:
    response = provider._stream_responses_request("https://example.test/responses", {}, {})
    async for _ in response.stream_generator:
        pass
    return response


@pytest.mark.asyncio
async def test_stream_keeps_terminal_response_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    item = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hi"}]}
    events = [
        {"type": "response.output_item.done", "item": item},
        {
            "type": "response.completed",
            "response": {"id": "resp-1", "status": "completed", "usage": {"total_tokens": 7}},
        },
    ]
    monkeypatch.setattr(openai_responses_module, "stream_json", _fake_stream(events))

    response = await _drain(_provider())

    assert response.original["status"] == "completed"
    assert response.original["usage"] == {"total_tokens": 7}
    assert response.main_response.content == "hi"


@pytest.mark.asyncio
async def test_stream_raises_structured_error_on_failed_response(monkeypatch: pytest.MonkeyPatch) -> None:
    events = [
        {
            "type": "response.failed",
            "response": {"id": "resp-2", "status": "failed", "error": {"code": "server_error", "message": "x"}},
        }
    ]
    monkeypatch.setattr(openai_responses_module, "stream_json", _fake_stream(events))

    with pytest.raises(ProviderResponseError) as exc_info:
        await _drain(_provider())

    assert exc_info.value.code == "server_error"
    assert exc_info.value.response_id == "resp-2"


@pytest.mark.asyncio
async def test_stream_error_event_keeps_message_param_and_response_id(monkeypatch: pytest.MonkeyPatch) -> None:
    events = [
        {"type": "response.created", "response": {"id": "resp-3", "status": "in_progress"}},
        {"type": "error", "code": None, "message": "Upstream overloaded", "param": "input"},
    ]
    monkeypatch.setattr(openai_responses_module, "stream_json", _fake_stream(events))

    with pytest.raises(ProviderResponseError) as exc_info:
        await _drain(_provider())

    assert exc_info.value.response_id == "resp-3"
    assert exc_info.value.param == "input"
    assert str(exc_info.value) == "provider response failed: error (Upstream overloaded)"


def test_codex_provider_uses_patched_stream() -> None:
    assert PatchedCodexProvider._stream_responses_request is PatchedOpenAIResponsesProvider._stream_responses_request


@pytest.mark.asyncio
async def test_stream_error_event_reads_a_nested_error_object(monkeypatch: pytest.MonkeyPatch) -> None:
    events = [{"type": "error", "error": {"type": "server_error", "code": "overloaded", "message": "Try again"}}]
    monkeypatch.setattr(openai_responses_module, "stream_json", _fake_stream(events))

    with pytest.raises(ProviderResponseError) as exc_info:
        await _drain(_provider())

    assert exc_info.value.code == "overloaded"
    assert exc_info.value.error_type == "server_error"
    assert exc_info.value.message == "Try again"


def test_formatted_tools_give_every_object_schema_properties() -> None:
    from llm_async.models import Tool

    parameters = {
        "type": "object",
        "properties": {
            "metadata": {"type": ["object", "null"], "additionalProperties": False},
            "properties": {"type": "string", "enum": [{"type": "object"}]},
        },
    }

    formatted = _provider()._format_tools([Tool(name="t", description="d", parameters=parameters)])

    properties = formatted[0]["parameters"]["properties"]
    assert properties["metadata"] == {"type": ["object", "null"], "additionalProperties": False, "properties": {}}
    assert properties["properties"] == {"type": "string", "enum": [{"type": "object"}]}

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any

import pytest

from llm_async.models import Tool

from minibot.adapters.config.schema import LLMMConfig
from minibot.core.memory import MemoryEntry
from minibot.llm.provider_factory import LLMClient
from minibot.llm.tools.base import ToolBinding, ToolContext


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
    role: str = "assistant"
    tool_calls: list[_FakeToolCall] | None = None
    original: dict[str, Any] | None = None


@dataclass
class _FakeResponse:
    main_response: _FakeMessage
    original: dict[str, Any] | None = None


class _FakeProvider:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.calls: list[dict[str, Any]] = []

    async def acomplete(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse(main_response=_FakeMessage(content="ok", tool_calls=None), original={"id": "resp-1"})


@pytest.mark.asyncio
async def test_generate_falls_back_to_echo_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm import provider_factory

    monkeypatch.setitem(provider_factory.LLM_PROVIDERS, "openai", _FakeProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="", model="x"))

    result = await client.generate([], "hello")

    assert result.payload == "Echo: hello"


@pytest.mark.asyncio
async def test_generate_parses_structured_json_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm import provider_factory

    class _StructuredProvider(_FakeProvider):
        async def acomplete(self, **kwargs: Any) -> _FakeResponse:
            self.calls.append(kwargs)
            return _FakeResponse(
                main_response=_FakeMessage(
                    content='{"answer":"done","should_answer_to_user":false}',
                    tool_calls=None,
                ),
                original={"id": "resp-structured"},
            )

    monkeypatch.setitem(provider_factory.LLM_PROVIDERS, "openai", _StructuredProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="x"))

    result = await client.generate([], "hello", response_schema={"type": "object"})

    assert result.payload == {"answer": "done", "should_answer_to_user": False}
    assert result.response_id == "resp-structured"


@pytest.mark.asyncio
async def test_execute_tool_calls_handles_missing_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm import provider_factory

    monkeypatch.setitem(provider_factory.LLM_PROVIDERS, "openai", _FakeProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="x"))
    calls = [_FakeToolCall(id="tc-1", function={"name": "unknown", "arguments": "{}"})]

    result = await client._execute_tool_calls(calls, {}, ToolContext(owner_id="o"))

    assert result[0]["role"] == "tool"
    assert result[0]["name"] == "unknown"
    assert "not registered" in result[0]["content"]


@pytest.mark.asyncio
async def test_generate_stops_after_tool_loop_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm import provider_factory

    class _LoopProvider(_FakeProvider):
        async def acomplete(self, **kwargs: Any) -> _FakeResponse:
            self.calls.append(kwargs)
            call = _FakeToolCall(id="tc-loop", function={"name": "noop", "arguments": "{}"})
            message = _FakeMessage(content="", tool_calls=[call], original={"role": "assistant", "content": ""})
            return _FakeResponse(main_response=message, original={"id": "resp-loop"})

    async def _noop_handler(_: dict[str, Any], __: ToolContext) -> dict[str, Any]:
        return {"ok": True}

    tool = Tool(name="noop", description="noop", parameters={"type": "object", "properties": {}, "required": []})
    binding = ToolBinding(tool=tool, handler=_noop_handler)

    monkeypatch.setitem(provider_factory.LLM_PROVIDERS, "openai", _LoopProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="x"))

    result = await client.generate(
        [MemoryEntry(role="user", content="first", created_at=datetime.now(timezone.utc))],
        "hello",
        tools=[binding],
        response_schema={"type": "object"},
    )

    assert isinstance(result.payload, dict)
    assert "tool-loop safeguard" in result.payload["answer"]
    assert result.payload["should_answer_to_user"] is True


@pytest.mark.asyncio
async def test_generate_stops_when_tool_outputs_repeat_identically(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm import provider_factory

    class _LoopProvider(_FakeProvider):
        async def acomplete(self, **kwargs: Any) -> _FakeResponse:
            self.calls.append(kwargs)
            call = _FakeToolCall(id="tc-loop", function={"name": "noop", "arguments": "{}"})
            message = _FakeMessage(content="", tool_calls=[call], original={"role": "assistant", "content": ""})
            return _FakeResponse(main_response=message, original={"id": "resp-loop"})

    async def _noop_handler(_: dict[str, Any], __: ToolContext) -> dict[str, Any]:
        return {"ok": True, "value": "constant"}

    tool = Tool(name="noop", description="noop", parameters={"type": "object", "properties": {}, "required": []})
    binding = ToolBinding(tool=tool, handler=_noop_handler)

    monkeypatch.setitem(provider_factory.LLM_PROVIDERS, "openai", _LoopProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="x", max_tool_iterations=10))

    result = await client.generate([], "hello", tools=[binding], response_schema={"type": "object"})

    assert isinstance(result.payload, dict)
    assert "tool-loop safeguard" in result.payload["answer"]
    assert result.payload["should_answer_to_user"] is True
    assert len(client._provider.calls) == 3


@pytest.mark.asyncio
async def test_generate_sanitizes_assistant_message_before_tool_followup(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm import provider_factory

    class _ToolThenAnswerProvider(_FakeProvider):
        async def acomplete(self, **kwargs: Any) -> _FakeResponse:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                call = _FakeToolCall(id="tc-1", function={"name": "noop", "arguments": "{}"}, name="noop")
                message = _FakeMessage(
                    content="",
                    tool_calls=[call],
                    original={
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "tc-1",
                                "type": "function",
                                "function": {"name": "noop", "arguments": "{}"},
                            }
                        ],
                        "reasoning": {"trace": "provider-specific"},
                    },
                )
                return _FakeResponse(main_response=message, original={"id": "resp-tool"})
            return _FakeResponse(main_response=_FakeMessage(content='{"answer":"ok","should_answer_to_user":true}'))

    async def _noop_handler(_: dict[str, Any], __: ToolContext) -> dict[str, Any]:
        return {"ok": True}

    tool = Tool(name="noop", description="noop", parameters={"type": "object", "properties": {}, "required": []})
    binding = ToolBinding(tool=tool, handler=_noop_handler)

    monkeypatch.setitem(provider_factory.LLM_PROVIDERS, "openrouter", _ToolThenAnswerProvider)
    client = LLMClient(LLMMConfig(provider="openrouter", api_key="secret", model="x"))

    result = await client.generate([], "hello", tools=[binding], response_schema={"type": "object"})

    assert result.payload == {"answer": "ok", "should_answer_to_user": True}
    second_call_messages = client._provider.calls[1]["messages"]
    assistant_message = second_call_messages[-2]
    assert assistant_message["role"] == "assistant"
    assert assistant_message["tool_calls"][0]["function"]["name"] == "noop"
    assert "reasoning" not in assistant_message


@pytest.mark.asyncio
async def test_generate_uses_user_content_when_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm import provider_factory

    monkeypatch.setitem(provider_factory.LLM_PROVIDERS, "openai", _FakeProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="x"))

    multimodal_content = [
        {"type": "input_text", "text": "describe"},
        {"type": "input_image", "image_url": "data:image/jpeg;base64,QUJD"},
    ]
    await client.generate([], "describe", user_content=multimodal_content)

    call = client._provider.calls[-1]
    assert call["messages"][-1]["content"] == multimodal_content


@pytest.mark.asyncio
async def test_generate_skips_reasoning_effort_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm import provider_factory

    class _FakeResponsesProvider(_FakeProvider):
        pass

    monkeypatch.setattr(provider_factory, "OpenAIResponsesProvider", _FakeResponsesProvider)
    monkeypatch.setitem(provider_factory.LLM_PROVIDERS, "openai_responses", _FakeResponsesProvider)
    client = LLMClient(
        LLMMConfig(
            provider="openai_responses",
            api_key="secret",
            model="gpt-4.1-mini",
            send_reasoning_effort=False,
        )
    )

    await client.generate([], "hello")

    call = client._provider.calls[-1]
    assert "reasoning" not in call


@pytest.mark.asyncio
async def test_generate_includes_reasoning_effort_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm import provider_factory

    class _FakeResponsesProvider(_FakeProvider):
        pass

    monkeypatch.setattr(provider_factory, "OpenAIResponsesProvider", _FakeResponsesProvider)
    monkeypatch.setitem(provider_factory.LLM_PROVIDERS, "openai_responses", _FakeResponsesProvider)
    client = LLMClient(
        LLMMConfig(
            provider="openai_responses",
            api_key="secret",
            model="gpt-5-mini",
            send_reasoning_effort=True,
            reasoning_effort="medium",
        )
    )

    await client.generate([], "hello")

    call = client._provider.calls[-1]
    assert call["reasoning"] == {"effort": "medium"}


@pytest.mark.asyncio
async def test_generate_includes_openrouter_routing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm import provider_factory

    monkeypatch.setitem(provider_factory.LLM_PROVIDERS, "openrouter", _FakeProvider)
    client = LLMClient(
        LLMMConfig(
            provider="openrouter",
            api_key="secret",
            model="openrouter/auto",
            openrouter={
                "models": ["anthropic/claude-3.5-sonnet", "gryphe/mythomax-l2-13b"],
                "plugins": [{"id": "file-parser", "pdf": {"engine": "pdf-text"}}],
                "provider": {
                    "order": ["anthropic", "openai"],
                    "allow_fallbacks": True,
                    "data_collection": "deny",
                    "provider_extra": {"custom_hint": "value"},
                },
            },
        )
    )

    await client.generate([], "hello")

    call = client._provider.calls[-1]
    assert call["models"] == ["anthropic/claude-3.5-sonnet", "gryphe/mythomax-l2-13b"]
    assert call["provider"]["order"] == ["anthropic", "openai"]
    assert call["provider"]["allow_fallbacks"] is True
    assert call["provider"]["data_collection"] == "deny"
    assert call["provider"]["custom_hint"] == "value"
    assert call["plugins"] == [{"id": "file-parser", "pdf": {"engine": "pdf-text"}}]


def test_media_support_modes() -> None:
    openrouter_client = LLMClient(LLMMConfig(provider="openrouter", api_key="secret", model="x"))
    openai_client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="x"))
    responses_client = LLMClient(LLMMConfig(provider="openai_responses", api_key="secret", model="x"))
    claude_client = LLMClient(LLMMConfig(provider="claude", api_key="secret", model="x"))

    assert openrouter_client.supports_media_inputs() is True
    assert openrouter_client.media_input_mode() == "chat_completions"
    assert openai_client.supports_media_inputs() is True
    assert openai_client.media_input_mode() == "chat_completions"
    assert responses_client.supports_media_inputs() is True
    assert responses_client.media_input_mode() == "responses"
    assert claude_client.supports_media_inputs() is False
    assert claude_client.media_input_mode() == "none"


def test_parse_tool_call_accepts_python_dict_string() -> None:
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="x"))
    call = _FakeToolCall(
        id="tc-1",
        function={"name": "current_datetime", "arguments": "{'format': '%Y-%m-%dT%H:%M:%SZ'}"},
    )

    tool_name, arguments = client._parse_tool_call(call)

    assert tool_name == "current_datetime"
    assert arguments == {"format": "%Y-%m-%dT%H:%M:%SZ"}


def test_parse_tool_call_repairs_unclosed_json_object() -> None:
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="x"))
    call = _FakeToolCall(
        id="tc-1",
        function={"name": "http_request", "arguments": '{"url": "https://www.ecosbox.com", "method": "GET"'},
    )

    tool_name, arguments = client._parse_tool_call(call)

    assert tool_name == "http_request"
    assert arguments == {"url": "https://www.ecosbox.com", "method": "GET"}


def test_stringify_result_prefers_yaml_for_structured_payloads() -> None:
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="x"))

    rendered = client._stringify_result({"ok": True, "items": ["a", "b"]})

    assert "ok: true" in rendered
    assert "items:" in rendered
    assert "- a" in rendered


@pytest.mark.asyncio
async def test_generate_surfaces_invalid_tool_arguments_for_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm import provider_factory

    class _MalformedArgsProvider(_FakeProvider):
        async def acomplete(self, **kwargs: Any) -> _FakeResponse:
            self.calls.append(kwargs)
            call = _FakeToolCall(id="tc-1", function={"name": "current_datetime", "arguments": "{invalid"})
            message = _FakeMessage(content="", tool_calls=[call], original={"role": "assistant", "content": ""})
            return _FakeResponse(main_response=message, original={"id": "resp-malformed"})

    async def _time_handler(_: dict[str, Any], __: ToolContext) -> dict[str, Any]:
        return {"timestamp": "unused"}

    tool = Tool(
        name="current_datetime",
        description="time",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    binding = ToolBinding(tool=tool, handler=_time_handler)

    monkeypatch.setitem(provider_factory.LLM_PROVIDERS, "openrouter", _MalformedArgsProvider)
    client = LLMClient(LLMMConfig(provider="openrouter", api_key="secret", model="x", max_tool_iterations=2))

    result = await client.generate([], "time", tools=[binding], response_schema={"type": "object"})

    assert isinstance(result.payload, dict)
    assert "tool-loop safeguard" in result.payload["answer"]
    assert "current_datetime" in result.payload["answer"]
    assert result.payload["should_answer_to_user"] is True
    assert len(client._provider.calls) == 2


@pytest.mark.asyncio
async def test_generate_retries_with_required_tool_choice_for_explicit_tool_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minibot.llm import provider_factory

    class _RetryRequiredProvider(_FakeProvider):
        async def acomplete(self, **kwargs: Any) -> _FakeResponse:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return _FakeResponse(main_response=_FakeMessage(content="I'll do that now.", tool_calls=None))
            if len(self.calls) == 2:
                call = _FakeToolCall(id="tc-1", function={"name": "current_datetime", "arguments": "{}"})
                return _FakeResponse(
                    main_response=_FakeMessage(
                        content="",
                        tool_calls=[call],
                        original={"role": "assistant", "content": "", "tool_calls": []},
                    )
                )
            return _FakeResponse(main_response=_FakeMessage(content='{"answer":"done","should_answer_to_user":true}'))

    async def _time_handler(_: dict[str, Any], __: ToolContext) -> dict[str, Any]:
        return {"timestamp": "2026-02-08T14:00:00Z"}

    tool = Tool(
        name="current_datetime",
        description="time",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    binding = ToolBinding(tool=tool, handler=_time_handler)

    monkeypatch.setitem(provider_factory.LLM_PROVIDERS, "openrouter", _RetryRequiredProvider)
    client = LLMClient(LLMMConfig(provider="openrouter", api_key="secret", model="x"))

    result = await client.generate(
        [],
        "do execute the tool please",
        tools=[binding],
        response_schema={"type": "object"},
    )

    assert result.payload == {"answer": "done", "should_answer_to_user": True}
    assert len(client._provider.calls) == 3
    assert client._provider.calls[1]["tool_choice"] == "required"


@pytest.mark.asyncio
async def test_generate_retries_when_continue_loop_hint_is_true(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm import provider_factory

    class _ContinueLoopProvider(_FakeProvider):
        async def acomplete(self, **kwargs: Any) -> _FakeResponse:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                payload = {
                    "answer": "I will continue with tools.",
                    "should_answer_to_user": False,
                    "continue_loop": True,
                }
                return _FakeResponse(main_response=_FakeMessage(content=json.dumps(payload), tool_calls=None))
            if len(self.calls) == 2:
                call = _FakeToolCall(id="tc-1", function={"name": "current_datetime", "arguments": "{}"})
                return _FakeResponse(
                    main_response=_FakeMessage(
                        content="",
                        tool_calls=[call],
                        original={"role": "assistant", "content": "", "tool_calls": []},
                    )
                )
            return _FakeResponse(main_response=_FakeMessage(content='{"answer":"done","should_answer_to_user":true}'))

    async def _time_handler(_: dict[str, Any], __: ToolContext) -> dict[str, Any]:
        return {"timestamp": "2026-02-08T14:00:00Z"}

    tool = Tool(
        name="current_datetime",
        description="time",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    binding = ToolBinding(tool=tool, handler=_time_handler)

    monkeypatch.setitem(provider_factory.LLM_PROVIDERS, "openrouter", _ContinueLoopProvider)
    client = LLMClient(LLMMConfig(provider="openrouter", api_key="secret", model="x"))

    result = await client.generate([], "continue", tools=[binding], response_schema={"type": "object"})

    assert result.payload == {"answer": "done", "should_answer_to_user": True}
    assert len(client._provider.calls) == 3
    assert client._provider.calls[1]["tool_choice"] == "required"

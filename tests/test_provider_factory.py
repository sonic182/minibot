from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import pytest

from llm_async.models import Tool

from minibot.adapters.config.schema import LLMMConfig
from minibot.core.memory import MemoryEntry
from minibot.llm.provider_factory import LLMClient
from minibot.llm.services.tool_executor import parse_tool_call, sanitize_tool_arguments_for_log, stringify_result
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
    def __init__(
        self,
        api_key: str,
        base_url: str = "",
        retry_config: Any = None,
        client_kwargs: dict[str, Any] | None = None,
        http2: bool = False,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.retry_config = retry_config
        self.client_kwargs = client_kwargs or {}
        self.http2 = http2
        self.calls: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []

    async def acomplete(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse(main_response=_FakeMessage(content="ok", tool_calls=None), original={"id": "resp-1"})

    async def request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        self.requests.append(
            {
                "method": method,
                "path": path,
                "json_data": json_data or {},
            }
        )
        return {
            "id": "cmp-1",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "compacted"}],
                }
            ],
            "usage": {"total_tokens": 9},
        }


@pytest.mark.asyncio
async def test_generate_falls_back_to_echo_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai", _FakeProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="", model="x"))

    result = await client.generate([], "hello")

    assert result.payload == "Echo: hello"


@pytest.mark.asyncio
async def test_generate_parses_structured_json_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

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

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai", _StructuredProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="x"))

    result = await client.generate([], "hello", response_schema={"type": "object"})

    assert result.payload == {"answer": "done", "should_answer_to_user": False}
    assert result.response_id == "resp-structured"


@pytest.mark.asyncio
async def test_generate_parses_structured_json_from_fenced_block(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _FencedStructuredProvider(_FakeProvider):
        async def acomplete(self, **kwargs: Any) -> _FakeResponse:
            self.calls.append(kwargs)
            return _FakeResponse(
                main_response=_FakeMessage(
                    content='```json\n{"answer":"done","should_answer_to_user":true}\n```',
                    tool_calls=None,
                ),
                original={"id": "resp-fenced"},
            )

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai", _FencedStructuredProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="x"))

    result = await client.generate([], "hello", response_schema={"type": "object"})

    assert result.payload == {"answer": "done", "should_answer_to_user": True}
    assert result.response_id == "resp-fenced"


@pytest.mark.asyncio
async def test_generate_captures_total_tokens_from_openai_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _UsageProvider(_FakeProvider):
        async def acomplete(self, **kwargs: Any) -> _FakeResponse:
            self.calls.append(kwargs)
            return _FakeResponse(
                main_response=_FakeMessage(content="ok", tool_calls=None),
                original={"id": "resp-usage", "usage": {"prompt_tokens": 19, "completion_tokens": 10}},
            )

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai", _UsageProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="x"))

    result = await client.generate([], "hello")

    assert result.total_tokens == 29


@pytest.mark.asyncio
async def test_generate_captures_total_tokens_from_responses_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _UsageProvider(_FakeProvider):
        async def acomplete(self, **kwargs: Any) -> _FakeResponse:
            self.calls.append(kwargs)
            return _FakeResponse(
                main_response=_FakeMessage(content="ok", tool_calls=None),
                original={"id": "resp-usage", "usage": {"input_tokens": 11, "output_tokens": 7}},
            )

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai_responses", _UsageProvider)
    client = LLMClient(LLMMConfig(provider="openai_responses", api_key="secret", model="x"))

    result = await client.generate([], "hello")

    assert result.total_tokens == 18


@pytest.mark.asyncio
async def test_complete_once_captures_total_tokens_from_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _UsageProvider(_FakeProvider):
        async def acomplete(self, **kwargs: Any) -> _FakeResponse:
            self.calls.append(kwargs)
            return _FakeResponse(
                main_response=_FakeMessage(content="ok", tool_calls=None),
                original={"id": "resp-step", "usage": {"input_tokens": 5, "output_tokens": 3}},
            )

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai_responses", _UsageProvider)
    client = LLMClient(LLMMConfig(provider="openai_responses", api_key="secret", model="x"))

    result = await client.complete_once(messages=[{"role": "user", "content": "hello"}])

    assert result.response_id == "resp-step"
    assert result.total_tokens == 8


@pytest.mark.asyncio
async def test_generate_uses_system_prompt_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai", _FakeProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="x"))

    await client.generate([], "hello", system_prompt_override="Override prompt")

    call = client._provider.calls[-1]
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][0]["content"] == "Override prompt"


def test_load_system_prompt_from_file(tmp_path: Path) -> None:
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("You are a test assistant from file.", encoding="utf-8")

    config = LLMMConfig(
        provider="openai",
        api_key="secret",
        model="x",
        system_prompt_file=str(prompt_file),
    )
    client = LLMClient(config)

    assert client.system_prompt() == "You are a test assistant from file."


def test_load_system_prompt_fallback_to_default(tmp_path: Path) -> None:
    config = LLMMConfig(
        provider="openai",
        api_key="secret",
        model="x",
        system_prompt="Custom default prompt",
        system_prompt_file=None,
    )
    client = LLMClient(config)

    assert client.system_prompt() == "Custom default prompt"


def test_load_system_prompt_fails_when_file_missing(tmp_path: Path) -> None:
    config = LLMMConfig(
        provider="openai",
        api_key="secret",
        model="x",
        system_prompt_file=str(tmp_path / "nonexistent.md"),
    )

    with pytest.raises(FileNotFoundError, match="system_prompt_file configured but file not found"):
        LLMClient(config)


def test_load_system_prompt_fails_when_file_is_directory(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()

    config = LLMMConfig(
        provider="openai",
        api_key="secret",
        model="x",
        system_prompt_file=str(prompt_dir),
    )

    with pytest.raises(ValueError, match="system_prompt_file configured but path is not a file"):
        LLMClient(config)


def test_load_system_prompt_fails_when_file_is_empty(tmp_path: Path) -> None:
    prompt_file = tmp_path / "empty.md"
    prompt_file.write_text("   \n\n  ", encoding="utf-8")

    config = LLMMConfig(
        provider="openai",
        api_key="secret",
        model="x",
        system_prompt_file=str(prompt_file),
    )

    with pytest.raises(ValueError, match="system_prompt_file configured but file is empty"):
        LLMClient(config)


@pytest.mark.asyncio
async def test_generate_normalizes_response_schema_for_openai_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai", _FakeProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="gpt-5-mini"))

    schema = {
        "type": "object",
        "properties": {
            "answer": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["text"],
            },
            "ok": {"type": "boolean"},
        },
        "required": ["answer"],
    }
    await client.generate([], "hello", response_schema=schema)

    sent_schema = client._provider.calls[-1]["response_schema"]
    assert sent_schema["required"] == ["answer", "ok"]
    assert sent_schema["additionalProperties"] is False
    assert sent_schema["properties"]["ok"]["type"] == ["boolean", "null"]
    nested = sent_schema["properties"]["answer"]
    assert nested["required"] == ["text", "url"]
    assert nested["additionalProperties"] is False
    assert nested["properties"]["url"]["type"] == ["string", "null"]


@pytest.mark.asyncio
async def test_generate_keeps_schema_unchanged_for_non_openai_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai", _FakeProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="claude-3-5-sonnet"))

    schema = {
        "type": "object",
        "properties": {
            "answer": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "url": {"type": "string"},
                },
                "required": ["text"],
            }
        },
        "required": ["answer"],
    }
    await client.generate([], "hello", response_schema=schema)

    sent_schema = client._provider.calls[-1]["response_schema"]
    assert sent_schema == schema


@pytest.mark.asyncio
async def test_openrouter_defaults_max_tokens_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openrouter", _FakeProvider)
    client = LLMClient(LLMMConfig(provider="openrouter", api_key="secret", model="x"))

    await client.complete_once(messages=[{"role": "user", "content": "hi"}], tools=None)

    call = client._provider.calls[-1]
    assert call["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_openrouter_clamps_max_tokens_to_provider_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openrouter", _FakeProvider)
    client = LLMClient(LLMMConfig(provider="openrouter", api_key="secret", model="x", max_new_tokens=65536))

    await client.complete_once(messages=[{"role": "user", "content": "hi"}], tools=None)

    call = client._provider.calls[-1]
    assert call["max_tokens"] == 32768


@pytest.mark.asyncio
async def test_openai_responses_uses_max_output_tokens_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _FakeResponsesProvider(_FakeProvider):
        pass

    monkeypatch.setattr(provider_registry, "OpenAIResponsesProvider", _FakeResponsesProvider)
    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai_responses", _FakeResponsesProvider)
    client = LLMClient(
        LLMMConfig(
            provider="openai_responses",
            api_key="secret",
            model="gpt-5-mini",
            max_new_tokens=1234,
        )
    )

    await client.generate([], "hello")
    generate_call = client._provider.calls[-1]
    assert generate_call["max_output_tokens"] == 1234
    assert "max_tokens" not in generate_call

    await client.complete_once(messages=[{"role": "user", "content": "hello"}])
    completion_call = client._provider.calls[-1]
    assert completion_call["max_output_tokens"] == 1234
    assert "max_tokens" not in completion_call


@pytest.mark.asyncio
async def test_openai_responses_sets_instructions_from_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _FakeResponsesProvider(_FakeProvider):
        pass

    monkeypatch.setattr(provider_registry, "OpenAIResponsesProvider", _FakeResponsesProvider)
    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai_responses", _FakeResponsesProvider)
    client = LLMClient(
        LLMMConfig(
            provider="openai_responses",
            api_key="secret",
            model="gpt-5-mini",
            system_prompt="Custom system prompt",
            system_prompt_file=None,
        )
    )

    await client.generate([], "hello")

    call = client._provider.calls[-1]
    assert call["instructions"] == "Custom system prompt"


@pytest.mark.asyncio
async def test_generate_extracts_usage_details_from_responses_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _UsageResponsesProvider(_FakeProvider):
        async def acomplete(self, **kwargs: Any) -> _FakeResponse:
            self.calls.append(kwargs)
            return _FakeResponse(
                main_response=_FakeMessage(content="ok", tool_calls=None),
                original={
                    "id": "resp-usage",
                    "status": "completed",
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 12,
                        "total_tokens": 32,
                        "input_tokens_details": {"cached_tokens": 7},
                        "output_tokens_details": {"reasoning_tokens": 5},
                    },
                },
            )

    monkeypatch.setattr(provider_registry, "OpenAIResponsesProvider", _UsageResponsesProvider)
    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai_responses", _UsageResponsesProvider)
    client = LLMClient(LLMMConfig(provider="openai_responses", api_key="secret", model="gpt-5-mini"))

    result = await client.generate([], "hello")

    assert result.input_tokens == 20
    assert result.output_tokens == 12
    assert result.total_tokens == 32
    assert result.cached_input_tokens == 7
    assert result.reasoning_output_tokens == 5
    assert result.status == "completed"
    assert result.incomplete_reason is None


@pytest.mark.asyncio
async def test_generate_auto_continues_incomplete_response_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _IncompleteThenCompleteProvider(_FakeProvider):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._index = 0

        async def acomplete(self, **kwargs: Any) -> _FakeResponse:
            self.calls.append(kwargs)
            self._index += 1
            if self._index == 1:
                return _FakeResponse(
                    main_response=_FakeMessage(content='{"answer":"hello'),
                    original={
                        "id": "resp-1",
                        "status": "incomplete",
                        "incomplete_details": {"reason": "max_output_tokens"},
                        "usage": {"input_tokens": 20, "output_tokens": 5, "total_tokens": 25},
                    },
                )
            return _FakeResponse(
                main_response=_FakeMessage(content=' world","should_answer_to_user":true}'),
                original={
                    "id": "resp-2",
                    "status": "completed",
                    "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
                },
            )

    monkeypatch.setattr(provider_registry, "OpenAIResponsesProvider", _IncompleteThenCompleteProvider)
    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai_responses", _IncompleteThenCompleteProvider)
    client = LLMClient(LLMMConfig(provider="openai_responses", api_key="secret", model="gpt-5-mini"))

    result = await client.generate([], "hello", response_schema={"type": "object"})

    assert result.payload == {"answer": "hello world", "should_answer_to_user": True}
    assert result.response_id == "resp-2"
    assert result.total_tokens == 32
    assert result.input_tokens == 23
    assert result.output_tokens == 9


@pytest.mark.asyncio
async def test_execute_tool_calls_handles_missing_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai", _FakeProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="x"))
    calls = [_FakeToolCall(id="tc-1", function={"name": "unknown", "arguments": "{}"})]

    result = await client.execute_tool_calls_for_runtime(calls, [], ToolContext(owner_id="o"))

    assert result[0].message_payload["role"] == "tool"
    assert result[0].message_payload["name"] == "unknown"
    assert "not registered" in result[0].message_payload["content"]


@pytest.mark.asyncio
async def test_generate_stops_after_tool_loop_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

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

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai", _LoopProvider)
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
    from minibot.llm.services import provider_registry

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

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai", _LoopProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="x", max_tool_iterations=10))

    result = await client.generate([], "hello", tools=[binding], response_schema={"type": "object"})

    assert isinstance(result.payload, dict)
    assert "tool-loop safeguard" in result.payload["answer"]
    assert result.payload["should_answer_to_user"] is True
    assert len(client._provider.calls) == 3


@pytest.mark.asyncio
async def test_generate_sanitizes_assistant_message_before_tool_followup(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

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

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openrouter", _ToolThenAnswerProvider)
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
    from minibot.llm.services import provider_registry

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai", _FakeProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="x"))

    multimodal_content = [
        {"type": "input_text", "text": "describe"},
        {"type": "input_image", "image_url": "data:image/jpeg;base64,QUJD"},
    ]
    await client.generate([], "describe", user_content=multimodal_content)

    call = client._provider.calls[-1]
    assert call["messages"][-1]["content"] == multimodal_content


@pytest.mark.asyncio
async def test_generate_omits_reasoning_effort_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _FakeResponsesProvider(_FakeProvider):
        pass

    monkeypatch.setattr(provider_registry, "OpenAIResponsesProvider", _FakeResponsesProvider)
    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai_responses", _FakeResponsesProvider)
    client = LLMClient(
        LLMMConfig(
            provider="openai_responses",
            api_key="secret",
            model="gpt-4.1-mini",
        )
    )

    await client.generate([], "hello")

    call = client._provider.calls[-1]
    assert "reasoning" not in call


@pytest.mark.asyncio
async def test_generate_includes_reasoning_effort_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _FakeResponsesProvider(_FakeProvider):
        pass

    monkeypatch.setattr(provider_registry, "OpenAIResponsesProvider", _FakeResponsesProvider)
    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai_responses", _FakeResponsesProvider)
    client = LLMClient(
        LLMMConfig(
            provider="openai_responses",
            api_key="secret",
            model="gpt-5-mini",
            reasoning_effort="medium",
        )
    )

    await client.generate([], "hello")

    call = client._provider.calls[-1]
    assert call["reasoning"] == {"effort": "medium"}


@pytest.mark.asyncio
async def test_generate_includes_openrouter_routing_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openrouter", _FakeProvider)
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


def test_provider_uses_configured_transport_timeouts_and_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai", _FakeProvider)
    client = LLMClient(
        LLMMConfig(
            provider="openai",
            api_key="secret",
            model="x",
            request_timeout_seconds=55,
            sock_connect_timeout_seconds=12,
            sock_read_timeout_seconds=47,
            retry_attempts=3,
            retry_delay_seconds=2.0,
        )
    )

    provider = client._provider
    connector = provider.client_kwargs.get("connector")

    assert connector is not None
    assert connector.timeouts.request_timeout == 55.0
    assert connector.timeouts.sock_connect == 12.0
    assert connector.timeouts.sock_read == 47.0
    assert provider.retry_config is not None
    assert provider.retry_config.max_attempts == 4
    assert provider.retry_config.base_delay == 2.0
    assert provider.retry_config.max_delay == 2.0
    assert provider.retry_config.backoff_factor == 1.0
    assert provider.retry_config.jitter is False


def test_provider_passes_http2_to_provider_constructor(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai", _FakeProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="x", http2=True))

    assert client._provider.http2 is True


@pytest.mark.asyncio
async def test_generate_sends_prompt_cache_retention_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _FakeResponsesProvider(_FakeProvider):
        pass

    monkeypatch.setattr(provider_registry, "OpenAIResponsesProvider", _FakeResponsesProvider)
    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai_responses", _FakeResponsesProvider)
    client = LLMClient(
        LLMMConfig(
            provider="openai_responses",
            api_key="secret",
            model="gpt-5-mini",
            prompt_cache_retention="24h",
            prompt_cache_enabled=True,
        )
    )

    await client.generate([], "hello", prompt_cache_key="session-1")

    call = client._provider.calls[-1]
    assert call["prompt_cache_key"] == "session-1"
    assert call["prompt_cache_retention"] == "24h"


@pytest.mark.asyncio
async def test_generate_omits_prompt_cache_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _FakeResponsesProvider(_FakeProvider):
        pass

    monkeypatch.setattr(provider_registry, "OpenAIResponsesProvider", _FakeResponsesProvider)
    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai_responses", _FakeResponsesProvider)
    client = LLMClient(
        LLMMConfig(
            provider="openai_responses",
            api_key="secret",
            model="gpt-5-mini",
            prompt_cache_enabled=False,
            prompt_cache_retention="24h",
        )
    )

    await client.generate([], "hello", prompt_cache_key="session-1")

    call = client._provider.calls[-1]
    assert "prompt_cache_key" not in call
    assert "prompt_cache_retention" not in call


@pytest.mark.asyncio
async def test_compact_response_calls_responses_compact_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _FakeResponsesProvider(_FakeProvider):
        pass

    monkeypatch.setattr(provider_registry, "OpenAIResponsesProvider", _FakeResponsesProvider)
    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai_responses", _FakeResponsesProvider)
    client = LLMClient(LLMMConfig(provider="openai_responses", api_key="secret", model="gpt-5-mini"))

    compacted = await client.compact_response(previous_response_id="resp-1", prompt_cache_key="session-1")

    req = client._provider.requests[-1]
    assert req["method"] == "POST"
    assert req["path"] == "/responses/compact"
    assert req["json_data"]["model"] == "gpt-5-mini"
    assert req["json_data"]["previous_response_id"] == "resp-1"
    assert req["json_data"]["prompt_cache_key"] == "session-1"
    assert compacted.response_id == "cmp-1"
    assert compacted.total_tokens == 9


@pytest.mark.asyncio
async def test_compact_response_omits_prompt_cache_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _FakeResponsesProvider(_FakeProvider):
        pass

    monkeypatch.setattr(provider_registry, "OpenAIResponsesProvider", _FakeResponsesProvider)
    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai_responses", _FakeResponsesProvider)
    client = LLMClient(
        LLMMConfig(
            provider="openai_responses",
            api_key="secret",
            model="gpt-5-mini",
            prompt_cache_enabled=False,
        )
    )

    await client.compact_response(previous_response_id="resp-1", prompt_cache_key="session-1")

    req = client._provider.requests[-1]
    assert "prompt_cache_key" not in req["json_data"]


@pytest.mark.asyncio
async def test_compact_response_rejects_non_responses_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai", _FakeProvider)
    client = LLMClient(LLMMConfig(provider="openai", api_key="secret", model="gpt-5-mini"))

    with pytest.raises(RuntimeError):
        await client.compact_response(previous_response_id="resp-1")


@pytest.mark.asyncio
async def test_compact_response_retries_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _FlakyResponsesProvider(_FakeProvider):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._request_attempt = 0

        async def request(
            self,
            method: str,
            path: str,
            json_data: dict[str, Any] | None = None,
            **_: Any,
        ) -> dict[str, Any]:
            self._request_attempt += 1
            self.requests.append(
                {
                    "method": method,
                    "path": path,
                    "json_data": json_data or {},
                    "attempt": self._request_attempt,
                }
            )
            if self._request_attempt <= 2:
                raise RuntimeError("temporary failure")
            return {
                "id": "cmp-1",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "compacted"}]}],
                "usage": {"total_tokens": 9},
            }

    slept: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(provider_registry, "OpenAIResponsesProvider", _FlakyResponsesProvider)
    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai_responses", _FlakyResponsesProvider)
    client = LLMClient(
        LLMMConfig(
            provider="openai_responses",
            api_key="secret",
            model="gpt-5-mini",
            retry_delay_seconds=0.25,
        )
    )

    compacted = await client.compact_response(previous_response_id="resp-1")

    assert compacted.response_id == "cmp-1"
    assert len(client._provider.requests) == 3
    assert slept == [0.25, 0.5]


@pytest.mark.asyncio
async def test_compact_response_retries_and_fails_when_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _AlwaysFailResponsesProvider(_FakeProvider):
        async def request(
            self,
            method: str,
            path: str,
            json_data: dict[str, Any] | None = None,
            **_: Any,
        ) -> dict[str, Any]:
            self.requests.append({"method": method, "path": path, "json_data": json_data or {}})
            raise RuntimeError("temporary failure")

    slept: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(provider_registry, "OpenAIResponsesProvider", _AlwaysFailResponsesProvider)
    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai_responses", _AlwaysFailResponsesProvider)
    client = LLMClient(
        LLMMConfig(
            provider="openai_responses",
            api_key="secret",
            model="gpt-5-mini",
            retry_delay_seconds=0.25,
        )
    )

    with pytest.raises(RuntimeError, match="temporary failure"):
        await client.compact_response(previous_response_id="resp-1")

    assert len(client._provider.requests) == 3
    assert slept == [0.25, 0.5]


@pytest.mark.asyncio
async def test_compact_response_does_not_retry_invalid_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _InvalidPayloadResponsesProvider(_FakeProvider):
        async def request(
            self,
            method: str,
            path: str,
            json_data: dict[str, Any] | None = None,
            **_: Any,
        ) -> dict[str, Any]:
            self.requests.append({"method": method, "path": path, "json_data": json_data or {}})
            return {"output": [], "usage": {"total_tokens": 1}}

    slept: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(provider_registry, "OpenAIResponsesProvider", _InvalidPayloadResponsesProvider)
    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openai_responses", _InvalidPayloadResponsesProvider)
    client = LLMClient(LLMMConfig(provider="openai_responses", api_key="secret", model="gpt-5-mini"))

    with pytest.raises(RuntimeError, match="without id"):
        await client.compact_response(previous_response_id="resp-1")

    assert len(client._provider.requests) == 1
    assert slept == []


def test_parse_tool_call_accepts_python_dict_string() -> None:
    call = _FakeToolCall(
        id="tc-1",
        function={"name": "current_datetime", "arguments": "{'format': '%Y-%m-%dT%H:%M:%SZ'}"},
    )

    tool_name, arguments = parse_tool_call(call)

    assert tool_name == "current_datetime"
    assert arguments == {"format": "%Y-%m-%dT%H:%M:%SZ"}


def test_parse_tool_call_repairs_unclosed_json_object() -> None:
    call = _FakeToolCall(
        id="tc-1",
        function={"name": "http_request", "arguments": '{"url": "https://www.ecosbox.com", "method": "GET"'},
    )

    tool_name, arguments = parse_tool_call(call)

    assert tool_name == "http_request"
    assert arguments == {"url": "https://www.ecosbox.com", "method": "GET"}


def test_stringify_result_serializes_structured_payloads_as_json() -> None:
    rendered = stringify_result({"ok": True, "items": ["a", "b"]})

    assert rendered == '{"ok": true, "items": ["a", "b"]}'


@pytest.mark.asyncio
async def test_generate_surfaces_invalid_tool_arguments_for_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

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

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openrouter", _MalformedArgsProvider)
    client = LLMClient(LLMMConfig(provider="openrouter", api_key="secret", model="x", max_tool_iterations=2))

    result = await client.generate([], "time", tools=[binding], response_schema={"type": "object"})

    assert isinstance(result.payload, dict)
    assert "tool-loop safeguard" in result.payload["answer"]
    assert "current_datetime" in result.payload["answer"]
    assert result.payload["should_answer_to_user"] is True
    assert len(client._provider.calls) == 2


@pytest.mark.asyncio
async def test_generate_does_not_force_tool_choice_for_explicit_tool_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from minibot.llm.services import provider_registry

    class _RetryRequiredProvider(_FakeProvider):
        async def acomplete(self, **kwargs: Any) -> _FakeResponse:
            self.calls.append(kwargs)
            return _FakeResponse(main_response=_FakeMessage(content='{"answer":"done","should_answer_to_user":true}'))

    async def _time_handler(_: dict[str, Any], __: ToolContext) -> dict[str, Any]:
        return {"timestamp": "2026-02-08T14:00:00Z"}

    tool = Tool(
        name="current_datetime",
        description="time",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    binding = ToolBinding(tool=tool, handler=_time_handler)

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openrouter", _RetryRequiredProvider)
    client = LLMClient(LLMMConfig(provider="openrouter", api_key="secret", model="x"))

    result = await client.generate(
        [],
        "do execute the tool please",
        tools=[binding],
        response_schema={"type": "object"},
    )

    assert result.payload == {"answer": "done", "should_answer_to_user": True}
    assert len(client._provider.calls) == 1


@pytest.mark.asyncio
async def test_generate_ignores_continue_loop_hint_when_no_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from minibot.llm.services import provider_registry

    class _ContinueLoopProvider(_FakeProvider):
        async def acomplete(self, **kwargs: Any) -> _FakeResponse:
            self.calls.append(kwargs)
            payload = {
                "answer": "I will continue with tools.",
                "should_answer_to_user": False,
                "continue_loop": True,
            }
            return _FakeResponse(main_response=_FakeMessage(content=json.dumps(payload), tool_calls=None))

    async def _time_handler(_: dict[str, Any], __: ToolContext) -> dict[str, Any]:
        return {"timestamp": "2026-02-08T14:00:00Z"}

    tool = Tool(
        name="current_datetime",
        description="time",
        parameters={"type": "object", "properties": {}, "required": []},
    )
    binding = ToolBinding(tool=tool, handler=_time_handler)

    monkeypatch.setitem(provider_registry.LLM_PROVIDERS, "openrouter", _ContinueLoopProvider)
    client = LLMClient(LLMMConfig(provider="openrouter", api_key="secret", model="x"))

    result = await client.generate([], "continue", tools=[binding], response_schema={"type": "object"})

    assert result.payload == {
        "answer": "I will continue with tools.",
        "should_answer_to_user": False,
        "continue_loop": True,
    }
    assert len(client._provider.calls) == 1


def test_sanitize_tool_arguments_for_log_masks_secrets_and_caps_size() -> None:
    long_text = "x" * 500
    sanitized = sanitize_tool_arguments_for_log(
        {
            "url": "https://example.com",
            "api_key": "super-secret",
            "payload": {
                "Authorization": "Bearer abc",
                "text": long_text,
            },
            "items": list(range(25)),
        }
    )

    assert sanitized["url"] == "https://example.com"
    assert sanitized["api_key"] == "***"
    assert sanitized["payload"]["Authorization"] == "***"
    assert isinstance(sanitized["payload"]["text"], str)
    assert len(sanitized["payload"]["text"]) < len(long_text)
    assert sanitized["items"][-1] == "...(+5 items)"


def test_sanitize_tool_arguments_for_log_keeps_primitives() -> None:
    sanitized = sanitize_tool_arguments_for_log(
        {
            "enabled": True,
            "timeout_seconds": 15,
            "temperature": 0.2,
            "note": None,
        }
    )

    assert sanitized == {
        "enabled": True,
        "timeout_seconds": 15,
        "temperature": 0.2,
        "note": None,
    }

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
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
    function: dict[str, Any] | None = None
    name: str | None = None
    input: dict[str, Any] | None = None


@dataclass
class _FakeMessage:
    content: Any
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

from __future__ import annotations

from typing import Any

import pytest
from llm_async.models import Tool

from minibot.app.tool_use_guardrail import LLMClassifierToolUseGuardrail
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart
from minibot.llm.services.models import LLMGeneration
from minibot.llm.tools.base import ToolBinding, ToolContext


class _StubClassifierClient:
    def __init__(self, payloads: list[Any], tokens: list[int | None] | None = None) -> None:
        self._payloads = list(payloads)
        self._tokens = list(tokens or [None] * len(payloads))
        self.calls: list[dict[str, Any]] = []

    async def generate(self, *args: Any, **kwargs: Any) -> LLMGeneration:
        self.calls.append({"args": args, "kwargs": kwargs})
        payload = self._payloads.pop(0)
        total_tokens = self._tokens.pop(0)
        return LLMGeneration(payload=payload, total_tokens=total_tokens)


def _state(user_text: str) -> AgentState:
    return AgentState(messages=[AgentMessage(role="user", content=[MessagePart(type="text", text=user_text)])])


@pytest.mark.asyncio
async def test_guardrail_requires_retry_when_tools_needed() -> None:
    client = _StubClassifierClient(
        payloads=[
            {"requires_tools": True, "suggested_tool": "filesystem", "path": "tmp/a.txt", "reason": "needs file io"}
        ],
        tokens=[11],
    )
    guardrail = LLMClassifierToolUseGuardrail(llm_client=client, tools=[_filesystem_binding()])

    decision = await guardrail.apply(
        session_id="s1",
        user_text="delete tmp/a.txt",
        tool_context=ToolContext(owner_id="u1"),
        state=_state("delete tmp/a.txt"),
        system_prompt="system",
        prompt_cache_key="telegram:1",
    )

    assert decision.requires_retry is True
    assert decision.suggested_tool == "filesystem"
    assert decision.suggested_path == "tmp/a.txt"
    assert decision.reason == "needs file io"
    assert decision.attempts == 1
    assert decision.tokens_used == 11
    assert client.calls[0]["kwargs"]["include_provider_native_tools"] is False


@pytest.mark.asyncio
async def test_guardrail_retries_with_prompt_patch_when_classifier_payload_invalid() -> None:
    client = _StubClassifierClient(
        payloads=[
            "not valid json",
            {"requires_tools": False, "suggested_tool": None, "path": None, "reason": None},
        ],
        tokens=[3, 4],
    )
    guardrail = LLMClassifierToolUseGuardrail(llm_client=client, tools=[_filesystem_binding()])

    decision = await guardrail.apply(
        session_id="s1",
        user_text="say hi",
        tool_context=ToolContext(owner_id="u1"),
        state=_state("say hi"),
        system_prompt="system",
        prompt_cache_key=None,
    )

    assert decision.requires_retry is False
    assert decision.attempts == 2
    assert decision.tokens_used == 7
    second_prompt = str(client.calls[1]["args"][1])
    assert "Validation feedback:" in second_prompt


@pytest.mark.asyncio
async def test_guardrail_classifier_history_is_text_only() -> None:
    client = _StubClassifierClient(
        payloads=[{"requires_tools": False, "suggested_tool": None, "path": None, "reason": None}],
    )
    guardrail = LLMClassifierToolUseGuardrail(llm_client=client, tools=[_filesystem_binding()])
    state = AgentState(
        messages=[
            AgentMessage(role="system", content=[MessagePart(type="text", text="system")]),
            AgentMessage(
                role="assistant",
                content=[MessagePart(type="json", value={"status": "ok"}), MessagePart(type="text", text="done")],
            ),
            AgentMessage(role="user", content=[MessagePart(type="text", text="go")]),
        ]
    )

    decision = await guardrail.apply(
        session_id="s1",
        user_text="go",
        tool_context=ToolContext(owner_id="u1"),
        state=state,
        system_prompt="system",
        prompt_cache_key=None,
    )

    assert decision.requires_retry is False
    history = client.calls[0]["args"][0]
    assert all(isinstance(entry.content, str) for entry in history)
    assert [entry.role for entry in history] == ["assistant", "user"]


@pytest.mark.asyncio
async def test_guardrail_fail_open_when_validation_exhausted() -> None:
    client = _StubClassifierClient(payloads=["bad", "bad"], tokens=[2, 2])
    guardrail = LLMClassifierToolUseGuardrail(
        llm_client=client,
        tools=[_filesystem_binding()],
        validation_max_attempts=1,
        fail_open=True,
    )

    decision = await guardrail.apply(
        session_id="s1",
        user_text="do stuff",
        tool_context=ToolContext(owner_id="u1"),
        state=_state("do stuff"),
        system_prompt="system",
        prompt_cache_key=None,
    )

    assert decision.requires_retry is False
    assert decision.reason is not None and decision.reason.startswith("guardrail_validation_failed:")
    assert decision.attempts == 2
    assert decision.tokens_used == 4


@pytest.mark.asyncio
async def test_guardrail_fail_closed_when_validation_exhausted() -> None:
    client = _StubClassifierClient(payloads=["bad", "bad"], tokens=[1, 1])
    guardrail = LLMClassifierToolUseGuardrail(
        llm_client=client,
        tools=[_filesystem_binding()],
        validation_max_attempts=1,
        fail_open=False,
    )

    decision = await guardrail.apply(
        session_id="s1",
        user_text="do stuff",
        tool_context=ToolContext(owner_id="u1"),
        state=_state("do stuff"),
        system_prompt="system",
        prompt_cache_key=None,
    )

    assert decision.requires_retry is True
    assert decision.retry_system_prompt_suffix is not None
    assert decision.reason is not None and decision.reason.startswith("guardrail_validation_failed:")
    assert decision.attempts == 2
    assert decision.tokens_used == 2


@pytest.mark.asyncio
async def test_guardrail_never_executes_delete_side_effects_directly() -> None:
    deleted: list[dict[str, Any]] = []

    async def _filesystem_handler(payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        deleted.append(payload)
        return {"deleted_count": 1, "message": "deleted ok"}

    filesystem_binding = ToolBinding(
        tool=Tool(name="filesystem", description="filesystem tool", parameters={"type": "object"}),
        handler=_filesystem_handler,
    )
    client = _StubClassifierClient(
        payloads=[
            {"requires_tools": True, "suggested_tool": "filesystem", "path": "tmp/a.txt", "reason": "delete request"}
        ],
        tokens=[5],
    )
    guardrail = LLMClassifierToolUseGuardrail(llm_client=client, tools=[filesystem_binding])

    decision = await guardrail.apply(
        session_id="s1",
        user_text="delete tmp/a.txt",
        tool_context=ToolContext(owner_id="u1"),
        state=_state("delete tmp/a.txt"),
        system_prompt="system",
        prompt_cache_key=None,
    )

    assert decision.requires_retry is True
    assert decision.resolved_render_text is None
    assert deleted == []


@pytest.mark.asyncio
async def test_guardrail_does_not_rewrite_tool_routing_with_text_heuristics() -> None:
    client = _StubClassifierClient(
        payloads=[
            {
                "requires_tools": True,
                "suggested_tool": "filesystem",
                "path": "count_words.py",
                "reason": "edit file",
            }
        ],
    )
    guardrail = LLMClassifierToolUseGuardrail(
        llm_client=client,
        tools=[_filesystem_binding()],
    )

    decision = await guardrail.apply(
        session_id="s1",
        user_text="refactor count_words.py to use logging",
        tool_context=ToolContext(owner_id="u1"),
        state=_state("refactor count_words.py"),
        system_prompt="system",
        prompt_cache_key=None,
    )

    assert decision.requires_retry is True
    assert decision.suggested_tool == "filesystem"
    assert decision.suggested_path == "count_words.py"


def _filesystem_binding() -> ToolBinding:
    async def _handler(_: dict[str, Any], __: ToolContext) -> dict[str, Any]:
        return {"deleted_count": 0}

    return ToolBinding(
        tool=Tool(name="filesystem", description="filesystem tool", parameters={"type": "object"}),
        handler=_handler,
    )

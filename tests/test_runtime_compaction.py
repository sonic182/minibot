from __future__ import annotations

import logging
from typing import Any

import pytest

from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart
from minibot.llm.services.runtime_compaction import (
    RuntimeCompactor,
    build_compactor,
    threshold_from_context_limit,
)

LOGGER = logging.getLogger("test.runtime_compaction")


def _state() -> AgentState:
    return AgentState(
        messages=[
            AgentMessage(role="system", content=[MessagePart(type="text", text="system prompt")]),
            AgentMessage(role="user", content=[MessagePart(type="text", text="the original task")]),
            AgentMessage(role="assistant", content=[MessagePart(type="text", text="thinking out loud")]),
            AgentMessage(role="tool", name="grep", content=[MessagePart(type="text", text="a big tool result")]),
        ]
    )


class _Compaction:
    def __init__(self, response_id: str, text: str) -> None:
        self.response_id = response_id
        self.output = [{"content": [{"text": text}]}]
        self.total_tokens = 10


class _Generation:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.response_id = "gen-1"
        self.total_tokens = 10


class _ClientStub:
    def __init__(self, *, native: bool, state_mode: str = "previous_response_id") -> None:
        self._native = native
        self._state_mode = state_mode
        self.compact_calls: list[str] = []
        self.generate_calls: list[str] = []
        self.native_raises = False

    def supports_responses_compaction(self) -> bool:
        return self._native

    def responses_state_mode(self) -> str:
        return self._state_mode

    async def compact_response(self, *, previous_response_id: str, prompt_cache_key: str | None = None) -> Any:
        self.compact_calls.append(previous_response_id)
        if self.native_raises:
            raise RuntimeError("compact endpoint exploded")
        return _Compaction("compacted-1", "native summary")

    async def generate(self, history: Any, user_message: str, **kwargs: Any) -> Any:
        self.generate_calls.append(user_message)
        return _Generation("summary of the work so far")


def test_should_compact_only_at_or_above_the_threshold() -> None:
    compactor = RuntimeCompactor(llm_client=_ClientStub(native=True), threshold_tokens=100, logger=LOGGER)

    assert compactor.should_compact(100) is True
    assert compactor.should_compact(101) is True
    assert compactor.should_compact(99) is False
    assert compactor.should_compact(None) is False


@pytest.mark.asyncio
async def test_native_endpoint_is_used_when_the_provider_supports_it() -> None:
    client = _ClientStub(native=True)
    compactor = RuntimeCompactor(llm_client=client, threshold_tokens=10, logger=LOGGER)
    state = _state()

    outcome = await compactor.compact(state, previous_response_id="resp-9", prompt_cache_key="k")

    assert outcome.performed is True
    assert outcome.response_id == "compacted-1"
    assert client.compact_calls == ["resp-9"]
    assert not client.generate_calls
    # system + original task + summary, nothing of the working transcript left.
    assert [message.role for message in state.messages] == ["system", "user", "assistant"]
    assert state.messages[1].content[0].text == "the original task"
    assert state.messages[2].content[0].text == "native summary"


@pytest.mark.asyncio
async def test_falls_back_to_a_summary_without_the_native_endpoint() -> None:
    client = _ClientStub(native=False)
    compactor = RuntimeCompactor(llm_client=client, threshold_tokens=10, logger=LOGGER)
    state = _state()

    outcome = await compactor.compact(state, previous_response_id="resp-9", prompt_cache_key="k")

    assert outcome.performed is True
    # No response id: the summary lives locally, so the next step must not chain onto a response
    # that still carries the old context.
    assert outcome.response_id is None
    assert client.compact_calls == []
    assert "a big tool result" in client.generate_calls[0]
    assert state.messages[-1].content[0].text == "summary of the work so far"


@pytest.mark.asyncio
async def test_a_failing_compaction_leaves_the_run_untouched() -> None:
    client = _ClientStub(native=True)
    client.native_raises = True
    compactor = RuntimeCompactor(llm_client=client, threshold_tokens=10, logger=LOGGER)
    state = _state()
    before = list(state.messages)

    outcome = await compactor.compact(state, previous_response_id="resp-9", prompt_cache_key="k")

    assert outcome.performed is False
    assert state.messages == before


def test_build_compactor_needs_a_threshold() -> None:
    client = _ClientStub(native=True)

    assert build_compactor(llm_client=client, threshold_tokens=None, logger=LOGGER) is None
    assert build_compactor(llm_client=client, threshold_tokens=0, logger=LOGGER) is None
    assert build_compactor(llm_client=client, threshold_tokens=10, logger=LOGGER) is not None


def test_threshold_from_context_limit() -> None:
    assert threshold_from_context_limit(1_000_000, 0.95) == 950_000
    assert threshold_from_context_limit(None, 0.95) is None
    assert threshold_from_context_limit(1_000_000, 0) is None

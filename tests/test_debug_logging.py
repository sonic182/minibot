from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pytest
from llm_async.models import Tool

from minibot.llm.services.debug_logging import log_provider_response
from minibot.llm.services.tool_executor import execute_tool_calls_for_runtime
from minibot.llm.tools.base import ToolBinding, ToolContext


@dataclass
class _FakeToolCall:
    id: str
    name: str | None = None
    arguments: Any = None
    function: dict[str, Any] | None = None
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
    response_id: str | None = None


def test_log_provider_response_strips_raw_payloads_and_previews(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test.debug_logging")
    response = _FakeResponse(
        main_response=_FakeMessage(
            content="secret reasoning content",
            tool_calls=[
                _FakeToolCall(
                    id="call-1",
                    function={"name": "rag_index", "arguments": '{"file_path":"secret.pdf"}'},
                )
            ],
            original={"reasoning": "hidden"},
        ),
        original={"id": "resp-1", "usage": {"total_tokens": 10}},
    )

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        log_provider_response(
            logger=logger,
            response=response,
            context="complete_once",
            provider_name="openrouter",
            strip_logs=False,
        )

    record = caplog.records[-1]
    assert record.response_id == "resp-1"
    assert record.response_original_present is True
    assert record.message_content_type == "str"
    assert record.message_tool_calls_count == 1
    assert record.message_tool_call_names == ["rag_index"]
    assert not hasattr(record, "response_original")
    assert not hasattr(record, "message_original")
    assert not hasattr(record, "message_content_preview")
    assert not hasattr(record, "message_tool_calls")


@pytest.mark.asyncio
async def test_execute_tool_calls_logs_only_argument_keys(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test.tool_executor")
    binding = ToolBinding(
        tool=Tool(name="rag_index", description="desc", parameters={"type": "object"}),
        handler=_fake_handler,
    )
    tool_call = _FakeToolCall(
        id="call-1",
        function={"name": "rag_index", "arguments": '{"file_path":"secret.pdf","tags":["private"]}'},
    )

    with caplog.at_level(logging.INFO, logger=logger.name):
        await execute_tool_calls_for_runtime(
            [tool_call],  # type: ignore[list-item]
            [binding],
            ToolContext(owner_id="primary"),
            responses_mode=False,
            logger=logger,
        )

    executing = next(record for record in caplog.records if record.msg == "executing tool")
    assert executing.argument_keys == ["file_path", "tags"]
    assert not hasattr(executing, "arguments")


async def _fake_handler(payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
    return {"ok": True, "received": sorted(payload)}

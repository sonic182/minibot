from __future__ import annotations

import logging
from typing import Any, cast

import pytest
from llm_async.models import Tool, ToolCall

from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.llm.services.tool_executor import execute_tool_calls_for_runtime
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.shared.errors import ToolInputError


def test_missing_file_raises_actionable_typed_error(tmp_path: Any) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1024)

    with pytest.raises(ToolInputError) as excinfo:
        storage.resolve_existing_file("crm_leads.db")

    assert excinfo.value.error_code == "file_not_found"
    assert "crm_leads.db" in str(excinfo.value)


def test_missing_folder_raises_actionable_typed_error(tmp_path: Any) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1024)

    with pytest.raises(ToolInputError) as excinfo:
        storage.resolve_dir("nope")

    assert excinfo.value.error_code == "folder_not_found"


@pytest.mark.asyncio
async def test_tool_input_error_is_logged_as_warning_without_traceback(caplog: pytest.LogCaptureFixture) -> None:
    async def handler(_: dict[str, Any], __: ToolContext) -> dict[str, Any]:
        raise ToolInputError("bad argument", error_code="demo:bad_argument")

    with caplog.at_level(logging.WARNING, logger="test.tool_input"):
        records = await execute_tool_calls_for_runtime(
            [ToolCall(id="call_1", type="function", name="demo", function={"name": "demo", "arguments": "{}"})],
            [ToolBinding(tool=Tool(name="demo", description="d", parameters={"type": "object"}), handler=handler)],
            ToolContext(owner_id="primary"),
            responses_mode=False,
            logger=logging.getLogger("test.tool_input"),
        )

    assert records[0].result.content["error_code"] == "demo:bad_argument"
    logged = [record for record in caplog.records if record.name == "test.tool_input"]
    assert [record.levelno for record in logged] == [logging.WARNING]
    assert logged[0].exc_info is None


@pytest.mark.asyncio
async def test_only_malformed_call_arguments_are_labelled_invalid_tool_arguments() -> None:
    async def handler(_: dict[str, Any], __: ToolContext) -> dict[str, Any]:
        raise ValueError("could not build arguments for the upstream API")

    records = await execute_tool_calls_for_runtime(
        [
            ToolCall(id="call_1", type="function", name="demo", function={"name": "demo", "arguments": "{}"}),
            ToolCall(id="call_2", type="function", name="demo", function={"name": "demo", "arguments": "{invalid"}),
        ],
        [ToolBinding(tool=Tool(name="demo", description="d", parameters={"type": "object"}), handler=handler)],
        ToolContext(owner_id="primary"),
        responses_mode=False,
        logger=logging.getLogger("test.tool_arguments"),
    )

    assert [record.result.content["error_code"] for record in records] == [
        "tool_execution_failed",
        "invalid_tool_arguments",
    ]


@pytest.mark.asyncio
async def test_tool_failure_reaches_the_model_with_the_typed_error_code(tmp_path: Any) -> None:
    storage = LocalFileStorage(root_dir=str(tmp_path), max_write_bytes=1024)

    async def handler(payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        return {"ok": True, "info": storage.file_info(cast(str, payload["path"]))}

    records = await execute_tool_calls_for_runtime(
        [
            ToolCall(
                id="call_1",
                type="function",
                name="filesystem",
                function={"name": "filesystem", "arguments": '{"path": "data/files/crm_leads.db"}'},
            )
        ],
        [
            ToolBinding(
                tool=Tool(name="filesystem", description="fs", parameters={"type": "object"}),
                handler=handler,
            )
        ],
        ToolContext(owner_id="primary"),
        responses_mode=False,
        logger=logging.getLogger("test"),
    )

    content = records[0].result.content
    assert content["ok"] is False
    assert content["error_code"] == "file_not_found"
    assert "crm_leads.db" in content["error"]

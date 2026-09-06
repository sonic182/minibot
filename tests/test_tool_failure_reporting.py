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

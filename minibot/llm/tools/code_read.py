from __future__ import annotations

from typing import Any

from llm_async.models import Tool

from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.llm.tools.arg_utils import int_with_default, require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import nullable_integer, strict_object


class CodeReadTool:
    _DEFAULT_LIMIT = 200
    _MAX_LIMIT = 400

    def __init__(self, storage: LocalFileStorage) -> None:
        self._storage = storage

    def bindings(self) -> list[ToolBinding]:
        return [ToolBinding(tool=self._schema(), handler=self._handle)]

    def _schema(self) -> Tool:
        return Tool(
            name="code_read",
            description=load_tool_description("code_read"),
            parameters=strict_object(
                properties={
                    "path": {"type": "string", "description": "Relative file path under managed root."},
                    "offset": nullable_integer(minimum=0, description="Zero-based starting line offset."),
                    "limit": nullable_integer(
                        minimum=1,
                        description=(
                            f"Number of lines to return. Defaults to {self._DEFAULT_LIMIT}; "
                            f"max {self._MAX_LIMIT}."
                        ),
                    ),
                },
                required=["path", "offset", "limit"],
            ),
        )

    async def _handle(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        path = require_non_empty_str(payload, "path")
        offset = int_with_default(
            payload.get("offset"),
            default=0,
            field="offset",
            min_value=0,
            allow_string=False,
            type_error="offset must be an integer >= 0",
            min_error="offset must be an integer >= 0",
        )
        limit = int_with_default(
            payload.get("limit"),
            default=self._DEFAULT_LIMIT,
            field="limit",
            min_value=1,
            max_value=self._MAX_LIMIT,
            clamp_max=True,
            allow_string=False,
            type_error="limit must be an integer >= 1",
            min_error="limit must be an integer >= 1",
        )
        result = self._storage.read_text_lines(path, offset=offset, limit=limit)
        return {
            "ok": True,
            **result,
        }

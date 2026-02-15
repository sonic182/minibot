from __future__ import annotations

from llm_async.models import Tool

from minibot.core.memory import MemoryBackend
from minibot.llm.tools.arg_utils import optional_int, require_channel
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.schema_utils import empty_object_schema, integer_field, strict_object
from minibot.shared.utils import session_id_from_parts


class ChatMemoryTool:
    def __init__(self, memory: MemoryBackend, max_history_messages: int | None = None) -> None:
        self._memory = memory
        self._max_history_messages = max_history_messages

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._history_schema(), handler=self._history),
            ToolBinding(tool=self._info_schema(), handler=self._info),
            ToolBinding(tool=self._trim_schema(), handler=self._trim),
        ]

    def _history_schema(self) -> Tool:
        return Tool(
            name="history",
            description=("Conversation history operations for the current chat. Use action=info|trim."),
            parameters=strict_object(
                properties={
                    "action": {
                        "type": "string",
                        "enum": ["info", "trim"],
                        "description": "History operation to perform.",
                    },
                    "keep_latest": integer_field(
                        minimum=0,
                        description="Required when action=trim. 0 means clear all messages.",
                    ),
                },
                required=["action", "keep_latest"],
            ),
        )

    def _info_schema(self) -> Tool:
        return Tool(
            name="chat_history_info",
            description=(
                "Read-only conversation-history diagnostic for the current conversation. "
                "Returns history message count and configured max-history cap only. "
                "Use only when the user asks about chat history size/status. "
                "Also use when deciding whether a history trim is needed. "
                "Do not call for normal answering."
            ),
            parameters=empty_object_schema(),
        )

    def _trim_schema(self) -> Tool:
        return Tool(
            name="chat_history_trim",
            description=(
                "Destructive conversation-history operation for the current conversation. "
                "Permanently deletes older history entries and keeps only the latest N. "
                "Use only when the user explicitly asks to clear/reset/forget/trim chat history. "
                "Do not call during normal reasoning."
            ),
            parameters=strict_object(
                properties={
                    "keep_latest": integer_field(
                        minimum=0,
                        description=(
                            "How many latest messages to keep. 0 means clear all messages for this conversation."
                        ),
                    )
                },
                required=["keep_latest"],
            ),
        )

    async def _info(self, _: dict[str, object], context: ToolContext) -> dict[str, object]:
        session_id = self._session_id(context)
        total = await self._memory.count_history(session_id)
        return {
            "session_id": session_id,
            "total_messages": total,
            "max_history_messages": self._max_history_messages,
        }

    async def _trim(self, payload: dict[str, object], context: ToolContext) -> dict[str, object]:
        keep_latest = self._to_non_negative_int(payload.get("keep_latest"), key="keep_latest")
        session_id = self._session_id(context)
        removed = await self._memory.trim_history(session_id, keep_latest)
        remaining = await self._memory.count_history(session_id)
        return {
            "session_id": session_id,
            "keep_latest": keep_latest,
            "removed_messages": removed,
            "remaining_messages": remaining,
            "max_history_messages": self._max_history_messages,
        }

    async def _history(self, payload: dict[str, object], context: ToolContext) -> dict[str, object]:
        action = payload.get("action")
        if action == "info":
            return await self._info(payload, context)
        if action == "trim":
            return await self._trim(payload, context)
        raise ValueError("action must be one of: info, trim")

    def _session_id(self, context: ToolContext) -> str:
        channel = require_channel(context)
        return session_id_from_parts(channel, context.chat_id, context.user_id)

    @staticmethod
    def _to_non_negative_int(value: object, key: str) -> int:
        parsed = optional_int(
            value,
            field=key,
            min_value=0,
            allow_float=False,
            allow_string=True,
            reject_bool=True,
            type_error=f"{key} must be an integer",
            min_error=f"{key} must be >= 0",
        )
        if parsed is None:
            raise ValueError(f"{key} is required")
        return parsed

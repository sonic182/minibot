from __future__ import annotations

from llm_async.models import Tool

from minibot.core.memory import MemoryBackend
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.shared.utils import session_id_from_parts


class ChatMemoryTool:
    def __init__(self, memory: MemoryBackend, max_history_messages: int | None = None) -> None:
        self._memory = memory
        self._max_history_messages = max_history_messages

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._info_schema(), handler=self._info),
            ToolBinding(tool=self._trim_schema(), handler=self._trim),
        ]

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
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
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
            parameters={
                "type": "object",
                "properties": {
                    "keep_latest": {
                        "type": "integer",
                        "minimum": 0,
                        "description": (
                            "How many latest messages to keep. 0 means clear all messages for this conversation."
                        ),
                    }
                },
                "required": ["keep_latest"],
                "additionalProperties": False,
            },
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

    def _session_id(self, context: ToolContext) -> str:
        if not context.channel:
            raise ValueError("channel context is required")
        return session_id_from_parts(context.channel, context.chat_id, context.user_id)

    @staticmethod
    def _to_non_negative_int(value: object, key: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{key} must be an integer")
        if isinstance(value, int):
            if value < 0:
                raise ValueError(f"{key} must be >= 0")
            return value
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError(f"{key} is required")
            parsed = int(stripped)
            if parsed < 0:
                raise ValueError(f"{key} must be >= 0")
            return parsed
        raise ValueError(f"{key} must be an integer")

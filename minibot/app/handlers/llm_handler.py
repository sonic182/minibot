from __future__ import annotations

import logging
from typing import Sequence

import json
from typing import Any

from minibot.core.channels import ChannelMessage, ChannelResponse
from minibot.core.events import MessageEvent
from minibot.core.memory import MemoryBackend
from minibot.llm.provider_factory import LLMClient
from minibot.shared.utils import session_id_for
from minibot.llm.tools.base import ToolBinding, ToolContext


class LLMMessageHandler:
    def __init__(
        self,
        memory: MemoryBackend,
        llm_client: LLMClient,
        tools: Sequence[ToolBinding] | None = None,
        default_owner_id: str | None = None,
    ) -> None:
        self._memory = memory
        self._llm_client = llm_client
        self._tools = list(tools or [])
        self._default_owner_id = default_owner_id
        self._logger = logging.getLogger("minibot.handler")

    async def handle(self, event: MessageEvent) -> ChannelResponse:
        message = event.message
        session_id = session_id_for(message)
        await self._memory.append_history(session_id, "user", message.text)

        history = list(await self._memory.get_history(session_id))
        owner_id = resolve_owner_id(message, self._default_owner_id)
        tool_context = ToolContext(
            owner_id=owner_id,
            channel=message.channel,
            chat_id=message.chat_id,
            user_id=message.user_id,
        )
        try:
            structured = await self._llm_client.generate(
                history,
                message.text,
                tools=self._tools,
                tool_context=tool_context,
                response_schema=self._response_schema(),
            )
            answer, should_reply = self._extract_answer(structured)
        except Exception as exc:
            self._logger.exception("LLM call failed", exc_info=exc)
            answer = "Sorry, I couldn't answer right now."
            should_reply = True
        await self._memory.append_history(session_id, "assistant", answer)

        chat_id = message.chat_id or message.user_id or 0
        return ChannelResponse(
            channel=message.channel, chat_id=chat_id, text=answer, metadata={"should_reply": should_reply}
        )

    def _response_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "should_answer_to_user": {"type": "boolean"},
            },
            "required": ["answer", "should_answer_to_user"],
            "additionalProperties": True,
        }

    def _extract_answer(self, payload: Any) -> tuple[str, bool]:
        if isinstance(payload, dict):
            answer = payload.get("answer")
            should = payload.get("should_answer_to_user")
            if isinstance(answer, str) and isinstance(should, bool):
                return answer, should
        if isinstance(payload, str):
            try:
                parsed = json.loads(payload)
                if isinstance(parsed, dict):
                    answer = parsed.get("answer")
                    should = parsed.get("should_answer_to_user")
                    if isinstance(answer, str) and isinstance(should, bool):
                        return answer, should
            except Exception:
                pass
            return payload, True
        return str(payload), True


def resolve_owner_id(message: ChannelMessage, default_owner_id: str | None) -> str:
    if default_owner_id:
        return default_owner_id
    if message.user_id is not None:
        return str(message.user_id)
    if message.chat_id is not None:
        return str(message.chat_id)
    return session_id_for(message)

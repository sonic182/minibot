from __future__ import annotations

import logging

from minibot.core.channels import ChannelResponse
from minibot.core.events import MessageEvent
from minibot.core.memory import MemoryBackend
from minibot.llm.provider_factory import LLMClient
from minibot.shared.utils import session_id_for


class LLMMessageHandler:
    def __init__(self, memory: MemoryBackend, llm_client: LLMClient) -> None:
        self._memory = memory
        self._llm_client = llm_client
        self._logger = logging.getLogger("minibot.handler")

    async def handle(self, event: MessageEvent) -> ChannelResponse:
        message = event.message
        session_id = session_id_for(message)
        await self._memory.append_history(session_id, "user", message.text)

        history = list(await self._memory.get_history(session_id))
        try:
            text = await self._llm_client.generate(history, message.text)
        except Exception as exc:
            self._logger.exception("LLM call failed", exc_info=exc)
            text = "Sorry, I couldn\'t answer right now."
        await self._memory.append_history(session_id, "assistant", text)

        chat_id = message.chat_id or message.user_id or 0
        return ChannelResponse(channel=message.channel, chat_id=chat_id, text=text)

from __future__ import annotations

from minibot.app.handlers.services import LLMTurnService
from minibot.app.turn_inbox import TurnInbox
from minibot.core.channels import ChannelResponse
from minibot.core.events import MessageEvent


class LLMMessageHandler:
    def __init__(self, turn_service: LLMTurnService) -> None:
        self._turn_service = turn_service

    async def handle(self, event: MessageEvent, turn_input: TurnInbox | None = None) -> ChannelResponse:
        return await self._turn_service.handle(event, turn_input=turn_input)

    async def repair_format_response(
        self,
        *,
        response: ChannelResponse,
        parse_error: str,
        channel: str,
        chat_id: int,
        user_id: int | None,
        attempt: int,
    ) -> ChannelResponse:
        return await self._turn_service.repair_format_response(
            response=response,
            parse_error=parse_error,
            channel=channel,
            chat_id=chat_id,
            user_id=user_id,
            attempt=attempt,
        )

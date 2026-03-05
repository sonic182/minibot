from __future__ import annotations

from typing import Any

import pytest

from minibot.app.handlers.llm_handler import LLMMessageHandler
from minibot.core.channels import ChannelResponse, RenderableResponse
from minibot.core.events import MessageEvent


class _StubTurnService:
    def __init__(self) -> None:
        self.handle_calls: list[MessageEvent] = []
        self.repair_calls: list[dict[str, Any]] = []

    async def handle(self, event: MessageEvent) -> ChannelResponse:
        self.handle_calls.append(event)
        return ChannelResponse(
            channel="telegram",
            chat_id=1,
            text=f"ok:{event.message.text}",
            render=RenderableResponse(kind="text", text=f"ok:{event.message.text}"),
            metadata={"should_reply": True},
        )

    async def repair_format_response(self, **kwargs: Any) -> ChannelResponse:
        self.repair_calls.append(kwargs)
        return ChannelResponse(
            channel=kwargs["channel"],
            chat_id=kwargs["chat_id"],
            text="fixed",
            render=RenderableResponse(kind="markdown", text="fixed"),
            metadata={"should_reply": True},
        )


def _message_event(text: str = "hi") -> MessageEvent:
    from minibot.core.channels import ChannelMessage

    return MessageEvent(
        message=ChannelMessage(channel="telegram", user_id=1, chat_id=1, message_id=1, text=text),
    )


@pytest.mark.asyncio
async def test_handler_delegates_handle_to_turn_service() -> None:
    turn_service = _StubTurnService()
    handler = LLMMessageHandler(turn_service)

    response = await handler.handle(_message_event("ping"))

    assert response.text == "ok:ping"
    assert len(turn_service.handle_calls) == 1
    assert turn_service.handle_calls[0].message.text == "ping"


@pytest.mark.asyncio
async def test_handler_delegates_format_repair_to_turn_service() -> None:
    turn_service = _StubTurnService()
    handler = LLMMessageHandler(turn_service)

    response = await handler.repair_format_response(
        response=ChannelResponse(
            channel="telegram",
            chat_id=1,
            text="bad",
            render=RenderableResponse(kind="markdown", text="*bad"),
        ),
        parse_error="can't parse entities",
        channel="telegram",
        chat_id=1,
        user_id=1,
        attempt=2,
    )

    assert response.text == "fixed"
    assert len(turn_service.repair_calls) == 1
    assert turn_service.repair_calls[0]["attempt"] == 2

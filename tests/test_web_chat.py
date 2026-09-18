from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosonic
import pytest
from starlette.datastructures import URL, Headers, QueryParams
from websockets.asyncio.client import connect

from minibot.adapters.config.schema import HTTPServerConfig
from minibot.adapters.http import HttpServer, build_chat_route, build_chat_socket
from minibot.adapters.messaging.web import WebChannelService
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelResponse, RenderableResponse
from minibot.core.events import MessageEvent, OutboundEvent, ToolCallEvent
from tests.fixtures.memory import InMemoryMemoryStore

TOKEN = "s3cret"
SOCKET_TOKEN = "socket-secret"


@pytest.mark.asyncio
async def test_publish_user_message_uses_the_web_session() -> None:
    event_bus = EventBus()
    service = WebChannelService(event_bus)
    subscription = event_bus.subscribe(types=(MessageEvent,))
    try:
        await service.publish_user_message("hello")
        event = await asyncio.wait_for(anext(subscription.__aiter__()), timeout=1)
        assert event.message.channel == "web"
        assert event.message.chat_id == 1
        assert event.message.user_id == 1
        assert event.message.text == "hello"
    finally:
        await subscription.close()
        await service.stop()


@pytest.mark.asyncio
async def test_outbound_event_reaches_web_subscribers_only() -> None:
    event_bus = EventBus()
    service = WebChannelService(event_bus)
    queue = service.subscribe()
    await service.start()
    try:
        await event_bus.publish(
            OutboundEvent(
                response=ChannelResponse(
                    channel="telegram",
                    chat_id=1,
                    text="ignore me",
                )
            )
        )
        await event_bus.publish(
            OutboundEvent(
                response=ChannelResponse(
                    channel="web",
                    chat_id=1,
                    text="hello from web",
                    render=RenderableResponse(kind="markdown", text="hello from web"),
                )
            )
        )
        assert await asyncio.wait_for(queue.get(), timeout=1) == {"role": "assistant", "text": "hello from web"}
        queue.task_done()
    finally:
        service.unsubscribe(queue)
        await service.stop()


@pytest.mark.asyncio
async def test_tool_call_event_reaches_web_subscribers_without_detail() -> None:
    event_bus = EventBus()
    service = WebChannelService(event_bus)
    queue = service.subscribe()
    await service.start()
    try:
        await event_bus.publish(
            ToolCallEvent(
                phase="started",
                call_id="call-1",
                tool_name="read_file",
                turn_id="turn-1",
                channel="web",
                detail="/private/path",
                error="private error",
            )
        )
        assert await asyncio.wait_for(queue.get(), timeout=1) == {
            "kind": "tool",
            "turn_id": "turn-1",
            "call_id": "call-1",
            "tool_name": "read_file",
            "phase": "started",
        }
        queue.task_done()
    finally:
        service.unsubscribe(queue)
        await service.stop()


@pytest.mark.asyncio
async def test_chat_page_requires_http_auth_and_embeds_socket_token() -> None:
    server = HttpServer(
        HTTPServerConfig(enabled=True, host="127.0.0.1", port=0, auth_token=TOKEN),
        [build_chat_route(SOCKET_TOKEN)],
    )
    await server.start()
    try:
        async with aiosonic.HTTPClient() as client:
            assert (await client.get(f"http://127.0.0.1:{server.port}/chat")).status_code == 401
            response = await client.get(
                f"http://127.0.0.1:{server.port}/chat",
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            assert response.status_code == 200
            assert SOCKET_TOKEN in await response.text()
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_chat_socket_rejects_an_invalid_token() -> None:
    service = WebChannelService(EventBus())
    websocket = SimpleNamespace(
        query_params=QueryParams("token=wrong"),
        headers=Headers(),
        url=URL("ws://testserver/chat/ws"),
        close=AsyncMock(),
    )
    handler = build_chat_socket(service, InMemoryMemoryStore(), SOCKET_TOKEN)[1]

    await handler(websocket)

    websocket.close.assert_awaited_once_with(code=1008)
    await service.stop()


@pytest.mark.asyncio
async def test_chat_socket_echoes_user_message_and_publishes_it() -> None:
    event_bus = EventBus()
    service = WebChannelService(event_bus)
    subscription = event_bus.subscribe(types=(MessageEvent,))
    server = HttpServer(
        HTTPServerConfig(enabled=True, host="127.0.0.1", port=0),
        websockets=[build_chat_socket(service, InMemoryMemoryStore(), SOCKET_TOKEN)],
    )
    await service.start()
    await server.start()
    try:
        async with connect(f"ws://127.0.0.1:{server.port}/chat/ws?token={SOCKET_TOKEN}") as websocket:
            await websocket.send(json.dumps({"text": "hello <world>"}))
            assert json.loads(await asyncio.wait_for(websocket.recv(), timeout=1)) == {
                "role": "user",
                "html": "hello &lt;world&gt;",
            }
            event = await asyncio.wait_for(anext(subscription.__aiter__()), timeout=1)
            assert event.message.text == "hello <world>"
    finally:
        await subscription.close()
        await server.stop()
        await service.stop()


@pytest.mark.asyncio
async def test_chat_socket_sends_tool_activity() -> None:
    event_bus = EventBus()
    service = WebChannelService(event_bus)
    server = HttpServer(
        HTTPServerConfig(enabled=True, host="127.0.0.1", port=0),
        websockets=[build_chat_socket(service, InMemoryMemoryStore(), SOCKET_TOKEN)],
    )
    await service.start()
    await server.start()
    try:
        async with connect(f"ws://127.0.0.1:{server.port}/chat/ws?token={SOCKET_TOKEN}") as websocket:
            await event_bus.publish(
                ToolCallEvent(
                    phase="completed",
                    call_id="call-1",
                    tool_name="read_file",
                    turn_id="turn-1",
                    channel="web",
                )
            )
            assert json.loads(await asyncio.wait_for(websocket.recv(), timeout=1)) == {
                "kind": "tool",
                "turn_id": "turn-1",
                "call_id": "call-1",
                "tool_name": "read_file",
                "phase": "completed",
            }
    finally:
        await server.stop()
        await service.stop()

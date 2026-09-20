from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosonic
import pytest
from starlette.datastructures import URL, Headers
from websockets.asyncio.client import connect

from minibot.adapters.config.schema import HTTPServerConfig
from minibot.adapters.http import HttpServer, build_chat_route, build_chat_socket
from minibot.adapters.http.chat import _render_message, _socket_is_authorized
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
    subscription = service.subscribe()
    await service.start()
    try:
        initial_state = await asyncio.wait_for(subscription.state.get(), timeout=1)
        assert initial_state == {"busy": False}
        subscription.state.task_done()
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
        assert await asyncio.wait_for(subscription.events.get(), timeout=1) == {
            "role": "assistant",
            "text": "hello from web",
        }
        subscription.events.task_done()
    finally:
        service.unsubscribe(subscription)
        await service.stop()


@pytest.mark.asyncio
async def test_tool_call_event_reaches_web_subscribers_without_detail() -> None:
    event_bus = EventBus()
    service = WebChannelService(event_bus)
    subscription = service.subscribe()
    await service.start()
    try:
        initial_state = await asyncio.wait_for(subscription.state.get(), timeout=1)
        assert initial_state == {"busy": False}
        subscription.state.task_done()
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
        assert await asyncio.wait_for(subscription.events.get(), timeout=1) == {
            "kind": "tool",
            "turn_id": "turn-1",
            "call_id": "call-1",
            "tool_name": "read_file",
            "phase": "started",
        }
        subscription.events.task_done()
    finally:
        service.unsubscribe(subscription)
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
            body = await response.text()
            assert SOCKET_TOKEN in body
            assert "/static/chat.js" in body
            assert "cdn.jsdelivr.net" not in body
            assert "script-src 'self' 'unsafe-eval'" in response.headers["Content-Security-Policy"]
            for asset in ("chat.js", "vendor/alpine-3.15.2.module.esm.js", "vendor/lucide-1.46.0.min.js"):
                static_response = await client.get(
                    f"http://127.0.0.1:{server.port}/static/{asset}",
                    headers={"Authorization": f"Bearer {TOKEN}"},
                )
                assert static_response.status_code == 200
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_chat_socket_rejects_a_missing_or_invalid_token() -> None:
    service = WebChannelService(EventBus())
    websocket = SimpleNamespace(
        scope={"subprotocols": ["wrong"]},
        headers=Headers(),
        url=URL("ws://testserver/chat/ws"),
        close=AsyncMock(),
    )
    handler = build_chat_socket(service, InMemoryMemoryStore(), SOCKET_TOKEN)[1]

    await handler(websocket)

    websocket.close.assert_awaited_once_with(code=1008)
    await service.stop()


def test_chat_socket_accepts_the_public_origin_from_a_proxy() -> None:
    websocket = SimpleNamespace(
        scope={"subprotocols": [SOCKET_TOKEN]},
        headers=Headers(
            {
                "origin": "https://minibot.example",
                "x-forwarded-proto": "https",
                "x-forwarded-host": "minibot.example",
            }
        ),
        url=URL("ws://127.0.0.1:8080/chat/ws"),
    )

    assert _socket_is_authorized(websocket, SOCKET_TOKEN)


def test_chat_socket_rejects_a_mismatched_origin() -> None:
    websocket = SimpleNamespace(
        scope={"subprotocols": [SOCKET_TOKEN]},
        headers=Headers({"origin": "https://attacker.example", "host": "minibot.example"}),
        url=URL("wss://minibot.example/chat/ws"),
    )

    assert not _socket_is_authorized(websocket, SOCKET_TOKEN)


def test_render_message_does_not_allow_executable_html() -> None:
    user = _render_message("user", '<img src=x onerror="alert(1)"><script>alert(1)</script>')
    assistant = _render_message(
        "assistant",
        '<img src=x onerror="alert(1)"><script>alert(1)</script>[click](javascript:alert(1))',
    )

    assert "<script>" not in user["html"]
    assert "<img" not in user["html"]
    assert "<script>" not in assistant["html"]
    assert "<img" not in assistant["html"]
    assert 'href="javascript:' not in assistant["html"]


@pytest.mark.asyncio
async def test_web_subscribers_receive_busy_state_after_connecting() -> None:
    from minibot.core.events import TurnCompletedEvent, TurnFailedEvent, TurnStartedEvent

    event_bus = EventBus()
    service = WebChannelService(event_bus)
    subscription = service.subscribe()
    await service.start()
    try:
        assert await asyncio.wait_for(subscription.state.get(), timeout=1) == {"busy": False}
        subscription.state.task_done()
        await event_bus.publish(TurnStartedEvent(turn_id="turn-1", channel="web", chat_id=1, user_id=1))
        assert await asyncio.wait_for(subscription.state.get(), timeout=1) == {"busy": True}
        subscription.state.task_done()
        await event_bus.publish(TurnCompletedEvent(turn_id="turn-1", channel="web", chat_id=1))
        assert await asyncio.wait_for(subscription.state.get(), timeout=1) == {"busy": False}
        subscription.state.task_done()
        await event_bus.publish(TurnFailedEvent(turn_id="turn-2", channel="web", chat_id=1, error="failed"))
        assert await asyncio.wait_for(subscription.state.get(), timeout=1) == {"busy": False, "error": "failed"}
        subscription.state.task_done()
    finally:
        service.unsubscribe(subscription)
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
        async with connect(f"ws://127.0.0.1:{server.port}/chat/ws", subprotocols=[SOCKET_TOKEN]) as websocket:
            assert websocket.subprotocol == SOCKET_TOKEN
            assert json.loads(await asyncio.wait_for(websocket.recv(), timeout=1)) == {"busy": False}
            await websocket.send("not-json")
            assert json.loads(await asyncio.wait_for(websocket.recv(), timeout=1)) == {"error": "invalid chat message"}
            await websocket.send(json.dumps({"text": "   "}))
            assert json.loads(await asyncio.wait_for(websocket.recv(), timeout=1)) == {
                "error": "chat message must not be blank"
            }
            await websocket.send(json.dumps({"text": "x" * 8_001}))
            assert json.loads(await asyncio.wait_for(websocket.recv(), timeout=1)) == {"error": "invalid chat message"}
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
async def test_chat_socket_uploads_media_and_releases_attachments(tmp_path) -> None:
    import logging

    from minibot.adapters.config.schema import AudioTranscriptionToolConfig, FileStorageToolConfig
    from minibot.adapters.files.local_storage import LocalFileStorage
    from minibot.adapters.http.uploads import WebUploadManager

    http_config = HTTPServerConfig(enabled=True, host="127.0.0.1", port=0, chat_upload_max_attachments=1)
    manager = WebUploadManager(
        storage=LocalFileStorage(str(tmp_path), max_write_bytes=64_000),
        http_config=http_config,
        file_storage_config=FileStorageToolConfig(enabled=True),
        audio_config=AudioTranscriptionToolConfig(enabled=False),
        supports_media_inputs=True,
        logger=logging.getLogger("test.web_chat_uploads"),
    )
    event_bus = EventBus()
    service = WebChannelService(event_bus)
    subscription = event_bus.subscribe(types=(MessageEvent,))
    server = HttpServer(
        http_config,
        websockets=[build_chat_socket(service, InMemoryMemoryStore(), SOCKET_TOKEN, manager)],
    )
    await service.start()
    await server.start()
    png = b"\x89PNG\r\n\x1a\nbody"
    try:
        async with connect(f"ws://127.0.0.1:{server.port}/chat/ws", subprotocols=[SOCKET_TOKEN]) as websocket:
            assert json.loads(await asyncio.wait_for(websocket.recv(), timeout=1)) == {"busy": False}
            await websocket.send(
                json.dumps(
                    {
                        "kind": "upload_start",
                        "upload_id": "img-1",
                        "filename": "photo.png",
                        "mime": "image/png",
                        "size_bytes": len(png),
                        "media_kind": "image",
                    }
                )
            )
            assert json.loads(await asyncio.wait_for(websocket.recv(), timeout=1)) == {
                "kind": "upload_ready",
                "upload_id": "img-1",
            }
            await websocket.send(png)
            await websocket.send(json.dumps({"kind": "upload_complete", "upload_id": "img-1"}))
            complete = json.loads(await asyncio.wait_for(websocket.recv(), timeout=1))
            assert complete["kind"] == "upload_complete"
            assert complete["attachment"]["kind"] == "image"
            await websocket.send(json.dumps({"upload_ids": ["img-1"]}))
            user_event = json.loads(await asyncio.wait_for(websocket.recv(), timeout=1))
            assert user_event["role"] == "user"
            assert user_event["attachments"][0]["id"] == "img-1"
            event = await asyncio.wait_for(anext(subscription.__aiter__()), timeout=1)
            assert event.message.attachments[0]["type"] == "input_image"
            # The upload is released after a successful send, so a new one is accepted at the cap.
            await websocket.send(
                json.dumps(
                    {
                        "kind": "upload_start",
                        "upload_id": "img-2",
                        "filename": "photo2.png",
                        "mime": "image/png",
                        "size_bytes": len(png),
                        "media_kind": "image",
                    }
                )
            )
            assert json.loads(await asyncio.wait_for(websocket.recv(), timeout=1)) == {
                "kind": "upload_ready",
                "upload_id": "img-2",
            }
    finally:
        await subscription.close()
        await server.stop()
        await service.stop()

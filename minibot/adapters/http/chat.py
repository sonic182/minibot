from __future__ import annotations

import asyncio
import contextlib
import hmac
from html import escape
from typing import Any

from markdown_it import MarkdownIt
from pydantic import BaseModel, Field, ValidationError
from starlette.requests import Request
from starlette.websockets import WebSocket, WebSocketDisconnect

from minibot.adapters.http.server import RouteSpec, WebSocketSpec, render
from minibot.adapters.messaging.web import WebChannelService
from minibot.adapters.messaging.web.service import WebChatSubscription
from minibot.core.memory import MemoryBackend
from minibot.shared.utils import session_identifier

_HISTORY_LIMIT = 200
_MAX_MESSAGE_CHARS = 8_000
_MARKDOWN = MarkdownIt("commonmark", {"html": False})


class _IncomingChatMessage(BaseModel):
    text: str = Field(min_length=1, max_length=_MAX_MESSAGE_CHARS)


def build_chat_route(socket_token: str) -> RouteSpec:
    """Build the authenticated chat page that bootstraps a same-origin WebSocket."""

    async def _chat(request: Request) -> Any:
        return render(request, "chat.html", {"socket_token": socket_token})

    return ("/chat", _chat, ("GET",))


def build_chat_socket(service: WebChannelService, memory: MemoryBackend, socket_token: str) -> WebSocketSpec:
    """Build the WebSocket endpoint for the one shared web chat session."""

    async def _chat_socket(websocket: WebSocket) -> None:
        if not _socket_is_authorized(websocket, socket_token):
            await websocket.close(code=1008)
            return
        await websocket.accept(subprotocol=socket_token)
        queue = service.subscribe()
        try:
            for entry in await memory.get_history(session_identifier("web", 1), limit=_HISTORY_LIMIT):
                await websocket.send_json(_render_message(entry.role, entry.content))
            receive_task = asyncio.create_task(_receive_messages(websocket, service))
            send_task = asyncio.create_task(_send_events(websocket, queue))
            done, pending = await asyncio.wait((receive_task, send_task), return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                with contextlib.suppress(WebSocketDisconnect):
                    task.result()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            service.unsubscribe(queue)

    return ("/chat/ws", _chat_socket)


def _socket_is_authorized(websocket: WebSocket, socket_token: str) -> bool:
    subprotocols = websocket.scope.get("subprotocols", [])
    if not any(hmac.compare_digest(str(protocol), socket_token) for protocol in subprotocols):
        return False
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    expected_origin = _expected_origin(websocket)
    return hmac.compare_digest(origin, expected_origin)


def _expected_origin(websocket: WebSocket) -> str:
    forwarded_proto = websocket.headers.get("x-forwarded-proto", "").split(",", maxsplit=1)[0].strip()
    forwarded_host = websocket.headers.get("x-forwarded-host", "").split(",", maxsplit=1)[0].strip()
    scheme = forwarded_proto or ("https" if websocket.url.scheme == "wss" else "http")
    host = forwarded_host or websocket.headers.get("host", "")
    return f"{scheme}://{host}"


async def _receive_messages(websocket: WebSocket, service: WebChannelService) -> None:
    while True:
        try:
            payload = await websocket.receive_json()
        except WebSocketDisconnect:
            return
        except (RuntimeError, ValueError):
            await websocket.send_json({"error": "invalid chat message"})
            continue
        try:
            message = _IncomingChatMessage.model_validate(payload)
        except ValidationError:
            await websocket.send_json({"error": "invalid chat message"})
            continue
        text = message.text.strip()
        if not text:
            await websocket.send_json({"error": "chat message must not be blank"})
            continue
        await service.publish_user_message(text)


async def _send_events(websocket: WebSocket, subscription: WebChatSubscription) -> None:
    while True:
        state_task = asyncio.create_task(subscription.state.get())
        event_task = asyncio.create_task(subscription.events.get())
        done, pending = await asyncio.wait((state_task, event_task), return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task, queue in ((state_task, subscription.state), (event_task, subscription.events)):
            if task not in done:
                continue
            event = task.result()
            queue.task_done()
            if "text" in event:
                await websocket.send_json(_render_message(event["role"], event["text"]))
            else:
                await websocket.send_json(event)


def _render_message(role: str, text: str) -> dict[str, str]:
    rendered = _MARKDOWN.render(text) if role == "assistant" else escape(text)
    return {"role": role, "html": rendered}

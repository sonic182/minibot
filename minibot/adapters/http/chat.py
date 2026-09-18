from __future__ import annotations

import asyncio
import contextlib
import hmac
from html import escape
from typing import Any

from markdown_it import MarkdownIt
from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.websockets import WebSocket, WebSocketDisconnect

from minibot.adapters.http.server import RouteSpec, WebSocketSpec, render
from minibot.adapters.messaging.web import WebChannelService
from minibot.core.memory import MemoryBackend
from minibot.shared.utils import session_identifier

_HISTORY_LIMIT = 50
_MARKDOWN = MarkdownIt("commonmark", {"html": False})


class _IncomingChatMessage(BaseModel):
    text: str


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
        await websocket.accept()
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
    supplied_token = websocket.query_params.get("token", "")
    if not hmac.compare_digest(supplied_token, socket_token):
        return False
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    scheme = "https" if websocket.url.scheme == "wss" else "http"
    expected_origin = f"{scheme}://{websocket.headers.get('host', '')}"
    return hmac.compare_digest(origin, expected_origin)


async def _receive_messages(websocket: WebSocket, service: WebChannelService) -> None:
    while True:
        payload = await websocket.receive_json()
        try:
            message = _IncomingChatMessage.model_validate(payload)
        except ValidationError:
            await websocket.send_json({"error": "invalid chat message"})
            continue
        await service.publish_user_message(message.text)


async def _send_events(websocket: WebSocket, queue: asyncio.Queue[dict[str, bool | str]]) -> None:
    while True:
        event = await queue.get()
        try:
            if "text" in event:
                await websocket.send_json(_render_message(str(event["role"]), str(event["text"])))
            else:
                await websocket.send_json(event)
        finally:
            queue.task_done()


def _render_message(role: str, text: str) -> dict[str, str]:
    rendered = _MARKDOWN.render(text) if role == "assistant" else escape(text)
    return {"role": role, "html": rendered}

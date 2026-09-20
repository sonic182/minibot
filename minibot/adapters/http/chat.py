from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
from html import escape
from typing import Any

from markdown_it import MarkdownIt
from pydantic import BaseModel, Field, ValidationError
from starlette.requests import Request
from starlette.websockets import WebSocket, WebSocketDisconnect

from minibot.adapters.http.server import RouteSpec, WebSocketSpec, render
from minibot.adapters.http.uploads import ChatCapabilities, UploadError, WebUploadManager, WebUploadSession
from minibot.adapters.messaging.web import WebChannelService
from minibot.core.memory import MemoryBackend
from minibot.shared.utils import session_identifier

_HISTORY_LIMIT = 50
_MARKDOWN = MarkdownIt("commonmark", {"html": False})


class _IncomingChatMessage(BaseModel):
    text: str = ""
    upload_ids: list[str] = Field(default_factory=list)


class _UploadStart(BaseModel):
    upload_id: str
    filename: str
    mime: str
    size_bytes: int
    media_kind: str


class _UploadComplete(BaseModel):
    upload_id: str


class _UploadCancel(BaseModel):
    upload_id: str | None = None


def build_chat_route(socket_token: str, capabilities: ChatCapabilities | None = None) -> RouteSpec:
    """Build the authenticated chat page that bootstraps a same-origin WebSocket."""

    async def _chat(request: Request) -> Any:
        return render(
            request,
            "chat.html",
            {
                "socket_token": socket_token,
                "chat_capabilities": (capabilities or _disabled_capabilities()).as_context(),
            },
        )

    return ("/chat", _chat, ("GET",))


def build_chat_socket(
    service: WebChannelService,
    memory: MemoryBackend,
    socket_token: str,
    upload_manager: WebUploadManager | None = None,
) -> WebSocketSpec:
    """Build the WebSocket endpoint for the one shared web chat session."""

    async def _chat_socket(websocket: WebSocket) -> None:
        if not _socket_is_authorized(websocket, socket_token):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        queue = service.subscribe()
        session = upload_manager.new_session() if upload_manager is not None else None
        send_lock = asyncio.Lock()
        try:
            for entry in await memory.get_history(session_identifier("web", 1), limit=_HISTORY_LIMIT):
                await _send_json(websocket, _render_message(entry.role, entry.content), send_lock)
            receive_task = asyncio.create_task(_receive_messages(websocket, service, session, send_lock))
            send_task = asyncio.create_task(_send_events(websocket, queue, send_lock))
            done, pending = await asyncio.wait((receive_task, send_task), return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                with contextlib.suppress(WebSocketDisconnect):
                    task.result()
            await asyncio.gather(*pending, return_exceptions=True)
        finally:
            if session is not None:
                await session.close()
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
    return hmac.compare_digest(origin, f"{scheme}://{websocket.headers.get('host', '')}")


async def _receive_messages(
    websocket: WebSocket, service: WebChannelService, session: WebUploadSession | None, send_lock: asyncio.Lock
) -> None:
    while True:
        received = await websocket.receive()
        if received["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(received.get("code", 1000))
        payload_bytes = received.get("bytes")
        if payload_bytes is not None:
            await _receive_upload_chunk(websocket, session, payload_bytes, send_lock)
            continue
        payload_text = received.get("text")
        if payload_text is None:
            continue
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            await _send_error(websocket, "invalid chat message", send_lock)
            continue
        if not isinstance(payload, dict):
            await _send_error(websocket, "invalid chat message", send_lock)
            continue
        kind = payload.get("kind")
        if kind == "upload_start":
            await _receive_upload_start(websocket, session, payload, send_lock)
        elif kind == "upload_complete":
            await _receive_upload_complete(websocket, session, payload, send_lock)
        elif kind == "upload_cancel":
            await _receive_upload_cancel(websocket, session, payload, send_lock)
        elif kind in {None, "message"}:
            await _receive_chat_message(websocket, service, session, payload, send_lock)
        else:
            await _send_error(websocket, "unknown chat message", send_lock)


async def _receive_chat_message(
    websocket: WebSocket,
    service: WebChannelService,
    session: WebUploadSession | None,
    payload: dict[str, Any],
    send_lock: asyncio.Lock,
) -> None:
    try:
        message = _IncomingChatMessage.model_validate(payload)
        if not message.text.strip() and not message.upload_ids:
            raise ValueError("message requires text or attachments")
        attachments, incoming_files, display = await _message_parts(session, message.upload_ids)
    except (ValidationError, UploadError, ValueError) as exc:
        await _send_error(websocket, str(exc), send_lock)
        return
    await service.publish_user_message(
        message.text, attachments=attachments, incoming_files=incoming_files, attachment_display=display
    )


async def _message_parts(
    session: WebUploadSession | None, upload_ids: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    if not upload_ids:
        return [], [], []
    if session is None:
        raise UploadError("uploads are unavailable")
    return await session.message_parts(upload_ids)


async def _receive_upload_start(
    websocket: WebSocket, session: WebUploadSession | None, payload: dict[str, Any], send_lock: asyncio.Lock
) -> None:
    if session is None:
        await _send_error(websocket, "uploads are unavailable", send_lock)
        return
    try:
        upload = await session.start(_UploadStart.model_validate(payload).model_dump())
    except (ValidationError, UploadError) as exc:
        await _send_error(websocket, str(exc), send_lock)
        return
    await _send_json(websocket, {"kind": "upload_ready", "upload_id": upload.upload_id}, send_lock)


async def _receive_upload_chunk(
    websocket: WebSocket, session: WebUploadSession | None, payload: bytes, send_lock: asyncio.Lock
) -> None:
    if session is None:
        await _send_error(websocket, "uploads are unavailable", send_lock)
        return
    try:
        await session.append(payload)
    except UploadError as exc:
        await _send_error(websocket, str(exc), send_lock)


async def _receive_upload_complete(
    websocket: WebSocket, session: WebUploadSession | None, payload: dict[str, Any], send_lock: asyncio.Lock
) -> None:
    if session is None:
        await _send_error(websocket, "uploads are unavailable", send_lock)
        return
    try:
        complete = _UploadComplete.model_validate(payload)
        upload = await session.complete(complete.upload_id)
    except (ValidationError, UploadError) as exc:
        await _send_error(websocket, str(exc), send_lock)
        return
    await _send_json(websocket, {"kind": "upload_complete", "attachment": upload.display()}, send_lock)


async def _receive_upload_cancel(
    websocket: WebSocket, session: WebUploadSession | None, payload: dict[str, Any], send_lock: asyncio.Lock
) -> None:
    if session is None:
        return
    try:
        cancel = _UploadCancel.model_validate(payload)
    except ValidationError as exc:
        await _send_error(websocket, str(exc), send_lock)
        return
    await session.cancel(cancel.upload_id)


async def _send_events(websocket: WebSocket, queue: asyncio.Queue[dict[str, Any]], send_lock: asyncio.Lock) -> None:
    while True:
        event = await queue.get()
        try:
            if "text" in event:
                payload = _render_message(str(event["role"]), str(event["text"]), event.get("attachments"))
            else:
                payload = event
            await _send_json(websocket, payload, send_lock)
        finally:
            queue.task_done()


async def _send_error(websocket: WebSocket, error: str, send_lock: asyncio.Lock) -> None:
    await _send_json(websocket, {"error": error}, send_lock)


async def _send_json(websocket: WebSocket, payload: dict[str, Any], send_lock: asyncio.Lock) -> None:
    async with send_lock:
        await websocket.send_json(payload)


def _render_message(role: str, text: str, attachments: object = None) -> dict[str, Any]:
    rendered = _MARKDOWN.render(text) if role == "assistant" else escape(text)
    response: dict[str, Any] = {"role": role, "html": rendered}
    if isinstance(attachments, list):
        response["attachments"] = attachments
    return response


def _disabled_capabilities() -> ChatCapabilities:
    return ChatCapabilities(False, False, False, 3, 5_000_000, 10_000_000, 12_000_000, 45)

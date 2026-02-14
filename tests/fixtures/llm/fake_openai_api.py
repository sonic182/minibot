from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


@dataclass
class FakeLLMState:
    chat_payloads: list[dict[str, Any]] = field(default_factory=list)
    responses_payloads: list[dict[str, Any]] = field(default_factory=list)
    chat_requests: list[dict[str, Any]] = field(default_factory=list)
    responses_requests: list[dict[str, Any]] = field(default_factory=list)


def build_app(state: FakeLLMState) -> Starlette:
    async def chat_completions(request: Request) -> JSONResponse:
        payload = await request.json()
        state.chat_requests.append(payload)
        if state.chat_payloads:
            return JSONResponse(state.chat_payloads.pop(0))
        return JSONResponse(_default_chat_payload("ok from fake chat"))

    async def responses(request: Request) -> JSONResponse:
        payload = await request.json()
        state.responses_requests.append(payload)
        if state.responses_payloads:
            return JSONResponse(state.responses_payloads.pop(0))
        return JSONResponse(_default_responses_payload("ok from fake responses"))

    return Starlette(
        routes=[
            Route("/v1/chat/completions", chat_completions, methods=["POST"]),
            Route("/v1/responses", responses, methods=["POST"]),
        ]
    )


def _default_chat_payload(text: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-fake-1",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 4, "completion_tokens": 7, "total_tokens": 11},
    }


def _default_responses_payload(text: str) -> dict[str, Any]:
    return {
        "id": "resp-fake-1",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "usage": {"input_tokens": 6, "output_tokens": 5},
    }


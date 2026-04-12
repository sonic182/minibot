from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from minibot.core.agent_runtime import AgentState
from minibot.core.channels import RenderableResponse


@dataclass(frozen=True)
class ParsedAnswer:
    render: RenderableResponse | None

    @property
    def has_visible_answer(self) -> bool:
        return self.render is not None and bool(self.render.text.strip())


def plain_render(text: str) -> RenderableResponse:
    return RenderableResponse(kind="text", text=text)


def extract_answer(payload: Any, *, pre_response_meta: dict[str, Any] | None = None) -> ParsedAnswer:
    text = payload if isinstance(payload, str) else str(payload) if payload is not None else ""
    kind = "markdown"
    meta: dict[str, Any] = {}
    if pre_response_meta is not None:
        raw_kind = pre_response_meta.get("kind")
        if raw_kind in {"text", "html", "markdown"}:
            kind = raw_kind
        raw_meta = pre_response_meta.get("meta")
        if isinstance(raw_meta, dict):
            meta = raw_meta
    structured_render = _extract_legacy_structured_render(text)
    if structured_render is not None:
        if pre_response_meta is None:
            kind = structured_render.kind
        text = structured_render.text
    return ParsedAnswer(render=RenderableResponse(kind=kind, text=text, meta=meta))


def extract_pre_response_meta(state: AgentState) -> dict[str, Any] | None:
    for message in reversed(state.messages):
        if message.role == "tool" and message.name == "pre_response":
            if message.content:
                part = message.content[0]
                if part.type == "json" and isinstance(part.value, dict):
                    return part.value
    return None


def _extract_legacy_structured_render(text: str) -> RenderableResponse | None:
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    answer = payload.get("answer")
    if isinstance(answer, dict):
        content = answer.get("content")
        kind = answer.get("kind")
        if isinstance(content, str) and kind in {"text", "html", "markdown"}:
            return RenderableResponse(kind=kind, text=content)
    if isinstance(answer, str):
        return RenderableResponse(kind="text", text=answer)
    return None

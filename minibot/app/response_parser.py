from __future__ import annotations

import logging
from typing import Any

from minibot.core.channels import RenderableResponse
from minibot.shared.parse_utils import parse_json_with_fenced_fallback


def plain_render(text: str) -> RenderableResponse:
    return RenderableResponse(kind="text", text=text)


def extract_answer(payload: Any, *, logger: logging.Logger) -> tuple[RenderableResponse, bool]:
    payload_obj = payload_to_object(payload)
    if payload_obj is not None:
        answer = payload_obj.get("answer")
        should = payload_obj.get("should_answer_to_user")
        render = render_from_payload(answer)
        if render is not None and isinstance(should, bool):
            logger.debug("structured output extracted from dict payload", extra={"kind": render.kind})
            return render, should
        logger.debug(
            "structured output payload failed strict validation",
            extra={
                "parsed_keys": sorted(str(key) for key in payload_obj.keys()),
                "answer_type": type(answer).__name__,
                "should_type": type(should).__name__,
            },
        )
    if isinstance(payload, str):
        return plain_render(payload), True
    return plain_render(str(payload)), True


def render_from_payload(value: Any) -> RenderableResponse | None:
    if not isinstance(value, dict):
        return None
    meta_value = value.get("meta")
    if meta_value is None:
        meta_value = {}
    if not isinstance(meta_value, dict):
        return None
    kind = value.get("kind")
    content = value.get("content")
    if kind not in {"text", "html", "markdown"}:
        return None
    if not isinstance(content, str) or not content.strip():
        return None
    render = RenderableResponse(kind=kind, text=content, meta=meta_value)
    if not render.text.strip():
        return None
    return render


def payload_to_object(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, str):
        return None
    try:
        parsed = parse_json_with_fenced_fallback(payload)
    except Exception:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None

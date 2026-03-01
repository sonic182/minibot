from __future__ import annotations

import logging
from typing import Any, Literal

from minibot.core.channels import RenderableResponse
from minibot.shared.assistant_response import coerce_should_answer, payload_to_object


def plain_render(text: str) -> RenderableResponse:
    return RenderableResponse(kind="text", text=text)


def extract_answer(payload: Any, *, logger: logging.Logger) -> tuple[RenderableResponse, bool]:
    payload_obj = payload_to_object(payload)
    if payload_obj is not None:
        answer = payload_obj.get("answer")
        should = payload_obj.get("should_answer_to_user")
        render = render_from_payload(answer, logger=logger)
        should_flag = coerce_should_answer(should)
        if render is not None and should_flag is not None:
            logger.debug(
                "structured output extracted from dict payload",
                extra={"kind": render.kind, "has_answer_object": isinstance(answer, dict)},
            )
            return render, should_flag
        if render is not None and should is None:
            logger.debug(
                "structured output missing should_answer_to_user; defaulting to true",
                extra={"kind": render.kind},
            )
            return render, True
        result = payload_obj.get("result")
        if isinstance(result, str):
            return plain_render(result), True
        timestamp = payload_obj.get("timestamp")
        if isinstance(timestamp, str):
            return plain_render(timestamp), True
        logger.debug(
            "parsed payload looked structured but failed validation",
            extra={
                "parsed_keys": sorted(str(key) for key in payload_obj.keys()),
                "should_type": type(should).__name__,
            },
        )
    if isinstance(payload, str):
        return plain_render(payload), True
    return plain_render(str(payload)), True


def render_from_payload(value: Any, *, logger: logging.Logger) -> RenderableResponse | None:
    if isinstance(value, str):
        logger.debug("structured output answer is legacy string; forcing text kind")
        return plain_render(value)
    if not isinstance(value, dict):
        return None

    content_value = value.get("content")
    if not isinstance(content_value, str):
        legacy_text = value.get("text")
        if isinstance(legacy_text, str):
            content_value = legacy_text

    raw_kind = value.get("kind")
    normalized_kind = normalize_render_kind(raw_kind)
    meta_value = value.get("meta")
    normalized_meta = meta_value if isinstance(meta_value, dict) else {}

    if isinstance(content_value, str) and normalized_kind is not None:
        if not isinstance(meta_value, dict) and meta_value is not None:
            logger.debug(
                "structured output meta is not an object; coercing to empty object",
                extra={"meta_type": type(meta_value).__name__},
            )
        render = RenderableResponse(kind=normalized_kind, text=content_value, meta=normalized_meta)
        logger.debug(
            "structured output answer object normalized",
            extra={
                "kind": render.kind,
                "meta_keys": sorted(render.meta.keys()),
                "source_keys": sorted(str(key) for key in value.keys()),
            },
        )
        if not render.text.strip():
            return None
        return render

    try:
        render = RenderableResponse.model_validate(value)
    except Exception as exc:
        text = content_value
        if isinstance(text, str):
            logger.debug(
                "structured output answer object invalid; using plain text fallback",
                extra={
                    "available_keys": sorted(str(key) for key in value.keys()),
                    "validation_error": str(exc),
                    "raw_kind": raw_kind,
                },
            )
            return plain_render(text)
        return None
    if not render.text.strip():
        return None
    logger.debug(
        "structured output answer object validated",
        extra={
            "kind": render.kind,
            "meta_keys": sorted(render.meta.keys()),
        },
    )
    return render


def normalize_render_kind(value: Any) -> Literal["text", "html", "markdown"] | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"text", "plain", "plain_text", "plaintext"}:
        return "text"
    if normalized in {"html", "htm"}:
        return "html"
    if normalized in {"markdown_v2", "markdownv2", "markdown", "md"}:
        return "markdown"
    return None

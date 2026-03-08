from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Sequence

from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart


class RuntimeMessageRenderer:
    def __init__(
        self,
        *,
        media_input_mode: str,
        managed_files_root: str | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._media_input_mode = media_input_mode
        self._managed_files_root = Path(managed_files_root).resolve() if managed_files_root else None
        self._logger = logger or logging.getLogger("minibot.runtime_message_renderer")

    def from_provider_assistant_message(self, message: Any) -> AgentMessage:
        content = getattr(message, "content", "")
        reasoning = getattr(message, "reasoning", None)
        reasoning_details = getattr(message, "reasoning_details", None)
        metadata: dict[str, Any] = {}
        if reasoning:
            metadata["reasoning"] = reasoning
        if reasoning_details:
            metadata["reasoning_details"] = reasoning_details
        if isinstance(content, str):
            return AgentMessage(
                role="assistant",
                content=[MessagePart(type="text", text=content)],
                metadata=metadata or None,
            )
        if isinstance(content, list):
            parts: list[MessagePart] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type", "text"))
                if part_type in {"text", "input_text"}:
                    parts.append(MessagePart(type="text", text=str(part.get("text", ""))))
                    continue
                if part_type in {"json", "output_json"}:
                    parts.append(MessagePart(type="json", value=part.get("value")))
                    continue
                parts.append(MessagePart(type="json", value=part))
            return AgentMessage(
                role="assistant",
                content=parts or [MessagePart(type="text", text="")],
                metadata=metadata or None,
            )
        return AgentMessage(
            role="assistant",
            content=[MessagePart(type="text", text=str(content))],
            metadata=metadata or None,
        )

    def from_provider_assistant_tool_call_message(self, message: Any) -> AgentMessage:
        content = getattr(message, "content", "")
        text = content if isinstance(content, str) else ""
        tool_calls = getattr(message, "tool_calls", None)
        reasoning = getattr(message, "reasoning", None)
        reasoning_details = getattr(message, "reasoning_details", None)
        metadata: dict[str, Any] = {}
        if tool_calls:
            metadata["tool_calls"] = [
                {
                    "id": call.id,
                    "type": call.type,
                    "function": call.function,
                    "name": call.name,
                    "input": call.input,
                }
                for call in tool_calls
            ]
        if reasoning:
            metadata["reasoning"] = reasoning
        if reasoning_details:
            metadata["reasoning_details"] = reasoning_details
        return AgentMessage(role="assistant", content=[MessagePart(type="text", text=text)], metadata=metadata)

    def render_messages(self, state: AgentState) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for message in state.messages:
            if message.role == "tool":
                messages.append(
                    {
                        "role": "tool",
                        "name": message.name or "tool",
                        "tool_call_id": message.tool_call_id,
                        "content": self._stringify_parts(message.content),
                    }
                )
                continue

            payload: dict[str, Any] = {
                "role": message.role,
                "content": (
                    message.raw_content
                    if message.raw_content is not None
                    else self._render_non_tool_content(message.content)
                ),
            }
            reasoning = message.metadata.get("reasoning") if message.metadata else None
            reasoning_details = message.metadata.get("reasoning_details") if message.metadata else None
            if reasoning:
                payload["reasoning"] = reasoning
            if reasoning_details:
                payload["reasoning_details"] = reasoning_details
            tool_calls = message.metadata.get("tool_calls") if message.metadata else None
            if tool_calls:
                payload["tool_calls"] = tool_calls
            messages.append(payload)
        return messages

    def _render_non_tool_content(self, parts: Sequence[MessagePart]) -> str | list[dict[str, Any]]:
        if len(parts) == 1 and parts[0].type == "text" and parts[0].text is not None:
            return parts[0].text
        rendered: list[dict[str, Any]] = []
        for part in parts:
            if part.type == "text":
                text_value = part.text or ""
                if self._media_input_mode == "responses":
                    rendered.append({"type": "input_text", "text": text_value})
                else:
                    rendered.append({"type": "text", "text": text_value})
                continue
            if part.type == "json":
                rendered.append({"type": "json", "value": part.value})
                continue
            if part.type in {"image", "file"}:
                resolved = self._render_managed_file_part(part)
                if resolved is not None:
                    rendered.append(resolved)
                    continue
                rendered.append(part.to_dict())
                continue
            rendered.append(part.to_dict())
        return rendered

    def _render_managed_file_part(self, part: MessagePart) -> dict[str, Any] | None:
        source = part.source or {}
        if source.get("type") != "managed_file":
            return None
        relative_path = source.get("path")
        if not isinstance(relative_path, str) or not relative_path.strip():
            return None
        if self._managed_files_root is None:
            self._logger.warning("managed file root not configured for runtime injection")
            return None
        candidate = Path(relative_path)
        if candidate.is_absolute():
            self._logger.warning("managed file injection rejected absolute path", extra={"path": relative_path})
            return None
        path = (self._managed_files_root / candidate).resolve()
        if not path.is_relative_to(self._managed_files_root):
            self._logger.warning("managed file injection rejected path escape", extra={"path": relative_path})
            return None
        if not path.exists() or not path.is_file():
            self._logger.warning(
                "managed file missing on disk for injection",
                extra={"path": relative_path, "resolved_path": str(path)},
            )
            return None
        payload = path.read_bytes()
        encoded = base64.b64encode(payload).decode("ascii")
        mime = part.mime or "application/octet-stream"
        data_url = f"data:{mime};base64,{encoded}"
        self._logger.debug(
            "rendered managed file for provider payload",
            extra={
                "path": relative_path,
                "resolved_path": str(path),
                "mime": mime,
                "size": len(payload),
                "provider_mode": self._media_input_mode,
                "part_type": part.type,
            },
        )

        if self._media_input_mode == "responses":
            if part.type == "image":
                return {"type": "input_image", "image_url": data_url}
            return {
                "type": "input_file",
                "filename": part.filename or path.name,
                "file_data": data_url,
            }

        if self._media_input_mode == "chat_completions":
            if part.type == "image":
                return {"type": "image_url", "image_url": {"url": data_url}}
            return {
                "type": "file",
                "file": {
                    "filename": part.filename or path.name,
                    "file_data": data_url,
                },
            }

        return None

    @staticmethod
    def _stringify_parts(parts: Sequence[MessagePart]) -> str:
        if len(parts) == 1 and parts[0].type == "text" and parts[0].text is not None:
            return parts[0].text
        if len(parts) == 1 and parts[0].type == "json":
            return json.dumps(parts[0].value, ensure_ascii=True, default=str)
        return json.dumps([part.to_dict() for part in parts], ensure_ascii=True, default=str)

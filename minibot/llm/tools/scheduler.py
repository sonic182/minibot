from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from llm_async.models import Tool

from minibot.app.scheduler_service import ScheduledPromptService
from minibot.core.jobs import PromptRole
from minibot.llm.tools.base import ToolBinding, ToolContext


class SchedulePromptTool:
    def __init__(self, service: ScheduledPromptService) -> None:
        self._service = service

    def bindings(self) -> list[ToolBinding]:
        return [ToolBinding(tool=self._schema(), handler=self._handle)]

    def _schema(self) -> Tool:
        return Tool(
            name="schedule_prompt",
            description="Schedule a future message to be sent to this chat on behalf of the user.",
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Message text to inject when the schedule is due.",
                    },
                    "run_at": {
                        "type": ["string", "null"],
                        "description": "ISO 8601 timestamp (UTC preferred) when the prompt should run.",
                    },
                    "delay_seconds": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "description": "Alternative to run_at: delay from now in seconds.",
                    },
                    "role": {
                        "type": ["string", "null"],
                        "enum": [role.value for role in PromptRole],
                        "description": (
                            "Optional role for the injected prompt."
                            " Use 'assistant' (default) to send the content directly to the user,"
                            " or 'user' to treat it as a new prompt that the bot should answer."
                        ),
                    },
                    "metadata": {
                        "type": ["object", "null"],
                        "description": "Optional metadata stored with the scheduled job.",
                        "additionalProperties": False,
                    },
                },
                "required": ["content", "run_at", "delay_seconds", "role", "metadata"],
                "additionalProperties": False,
            },
        )

    async def _handle(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        owner_id = _require_owner(context)
        channel = _require_channel(context)
        content = _require_string(payload.get("content"), "content")
        run_at = _resolve_run_at(payload)
        role = _resolve_role(payload.get("role"))
        metadata = _coerce_metadata(payload.get("metadata"))
        job = await self._service.schedule_prompt(
            owner_id=owner_id,
            channel=channel,
            text=content,
            run_at=run_at,
            chat_id=context.chat_id,
            user_id=context.user_id,
            role=role,
            metadata=metadata,
        )
        return {
            "job_id": job.id,
            "status": job.status.value,
            "run_at": job.run_at.isoformat(),
            "channel": job.channel,
        }


def _require_owner(context: ToolContext) -> str:
    if not context.owner_id:
        raise ValueError("owner context is required")
    return context.owner_id


def _require_channel(context: ToolContext) -> str:
    if not context.channel:
        raise ValueError("channel context is required for scheduling")
    return context.channel


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    return normalized


def _resolve_run_at(payload: dict[str, Any]) -> datetime:
    run_at_raw = payload.get("run_at")
    delay_value = payload.get("delay_seconds")
    if run_at_raw is None and delay_value is None:
        raise ValueError("run_at or delay_seconds is required")
    if run_at_raw is not None:
        if not isinstance(run_at_raw, str):
            raise ValueError("run_at must be an ISO 8601 string")
        parsed = datetime.fromisoformat(run_at_raw)
        return _ensure_timezone(parsed)
    if isinstance(delay_value, (int, float)):
        delay_seconds = max(int(delay_value), 0)
    elif isinstance(delay_value, str):
        delay_seconds = int(delay_value.strip())
    else:
        raise ValueError("delay_seconds must be numeric")
    return datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)


def _resolve_role(role: Any) -> PromptRole:
    if not role:
        return PromptRole.USER
    if isinstance(role, PromptRole):
        return role
    if isinstance(role, str):
        normalized = role.strip().lower()
        for candidate in PromptRole:
            if candidate.value == normalized:
                return candidate
    raise ValueError("invalid role for scheduled prompt")


def _coerce_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    raise ValueError("metadata must be an object")


def _ensure_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from llm_async.models import Tool

from minibot.app.scheduler_service import ScheduledPromptService
from minibot.core.jobs import PromptRecurrence, PromptRole, ScheduledPromptStatus
from minibot.llm.tools.base import ToolBinding, ToolContext


class SchedulePromptTool:
    def __init__(self, service: ScheduledPromptService, min_recurrence_interval_seconds: int = 60) -> None:
        self._service = service
        self._min_recurrence_interval_seconds = max(1, min_recurrence_interval_seconds)

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._schedule_schema(), handler=self._handle_schedule),
            ToolBinding(tool=self._cancel_schema(), handler=self._handle_cancel),
            ToolBinding(tool=self._delete_schema(), handler=self._handle_delete),
            ToolBinding(tool=self._list_schema(), handler=self._handle_list),
        ]

    def _schedule_schema(self) -> Tool:
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
                    "recurrence_type": {
                        "type": ["string", "null"],
                        "enum": [recurrence.value for recurrence in PromptRecurrence],
                        "description": "Optional recurrence mode. Use 'interval' for repeated execution.",
                    },
                    "recurrence_interval_seconds": {
                        "type": ["integer", "null"],
                        "minimum": self._min_recurrence_interval_seconds,
                        "description": (
                            "Interval in seconds between recurring executions."
                            f" Minimum: {self._min_recurrence_interval_seconds}."
                        ),
                    },
                    "recurrence_end_at": {
                        "type": ["string", "null"],
                        "description": "Optional ISO 8601 timestamp after which recurrence stops.",
                    },
                },
                "required": [
                    "content",
                    "run_at",
                    "delay_seconds",
                    "role",
                    "metadata",
                    "recurrence_type",
                    "recurrence_interval_seconds",
                    "recurrence_end_at",
                ],
                "additionalProperties": False,
            },
        )

    def _cancel_schema(self) -> Tool:
        return Tool(
            name="cancel_scheduled_prompt",
            description="Cancel a scheduled prompt job by id for this owner/chat context.",
            parameters={
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Identifier returned by schedule_prompt.",
                    }
                },
                "required": ["job_id"],
                "additionalProperties": False,
            },
        )

    def _list_schema(self) -> Tool:
        return Tool(
            name="list_scheduled_prompts",
            description="List scheduled prompt jobs for this owner/chat context.",
            parameters={
                "type": "object",
                "properties": {
                    "active_only": {
                        "type": ["boolean", "null"],
                        "description": "When true, only pending/leased jobs are returned.",
                    },
                    "limit": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                    },
                    "offset": {
                        "type": ["integer", "null"],
                        "minimum": 0,
                    },
                },
                "required": ["active_only", "limit", "offset"],
                "additionalProperties": False,
            },
        )

    def _delete_schema(self) -> Tool:
        return Tool(
            name="delete_scheduled_prompt",
            description=(
                "Delete a scheduled prompt job by id for this owner/chat context."
                " Active jobs are cancelled first and then deleted."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Identifier returned by schedule_prompt.",
                    }
                },
                "required": ["job_id"],
                "additionalProperties": False,
            },
        )

    async def _handle_schedule(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        owner_id = _require_owner(context)
        channel = _require_channel(context)
        content = _require_string(payload.get("content"), "content")
        run_at = _resolve_run_at(payload)
        role = _resolve_role(payload.get("role"))
        metadata = _coerce_metadata(payload.get("metadata"))
        recurrence_type = _resolve_recurrence(
            payload.get("recurrence_type"), payload.get("recurrence_interval_seconds")
        )
        recurrence_interval = _optional_int(
            payload.get("recurrence_interval_seconds"), field="recurrence_interval_seconds"
        )
        recurrence_end_at = _optional_datetime(payload.get("recurrence_end_at"))
        if recurrence_type == PromptRecurrence.INTERVAL:
            if recurrence_interval is None:
                return {
                    "scheduled": False,
                    "error": "recurrence_interval_seconds is required for interval recurrence",
                    "min_recurrence_interval_seconds": self._min_recurrence_interval_seconds,
                }
            if recurrence_interval < self._min_recurrence_interval_seconds:
                return {
                    "scheduled": False,
                    "error": (f"recurrence_interval_seconds must be >= {self._min_recurrence_interval_seconds}"),
                    "min_recurrence_interval_seconds": self._min_recurrence_interval_seconds,
                }
        try:
            job = await self._service.schedule_prompt(
                owner_id=owner_id,
                channel=channel,
                text=content,
                run_at=run_at,
                chat_id=context.chat_id,
                user_id=context.user_id,
                role=role,
                metadata=metadata,
                recurrence=recurrence_type,
                recurrence_interval_seconds=recurrence_interval,
                recurrence_end_at=recurrence_end_at,
            )
        except ValueError as exc:
            return {
                "scheduled": False,
                "error": str(exc),
                "min_recurrence_interval_seconds": self._min_recurrence_interval_seconds,
            }
        return {
            "scheduled": True,
            "job_id": job.id,
            "status": job.status.value,
            "run_at": job.run_at.isoformat(),
            "channel": job.channel,
        }

    async def _handle_cancel(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        owner_id = _require_owner(context)
        channel = _require_channel(context)
        job_id = _require_string(payload.get("job_id"), "job_id")
        job = await self._service.cancel_prompt(
            job_id=job_id,
            owner_id=owner_id,
            channel=channel,
            chat_id=context.chat_id,
            user_id=context.user_id,
        )
        if job is None:
            return {
                "job_id": job_id,
                "cancelled": False,
                "message": "Job not found for this owner/chat context",
            }
        cancelled = job.status == ScheduledPromptStatus.CANCELLED
        return {
            "job_id": job.id,
            "cancelled": cancelled,
            "status": job.status.value,
            "run_at": job.run_at.isoformat(),
        }

    async def _handle_list(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        owner_id = _require_owner(context)
        channel = _require_channel(context)
        active_only = _optional_bool(payload.get("active_only"), default=True)
        limit = _optional_int(payload.get("limit"), field="limit") or 20
        offset = _optional_int(payload.get("offset"), field="offset") or 0
        jobs = await self._service.list_prompts(
            owner_id=owner_id,
            channel=channel,
            chat_id=context.chat_id,
            user_id=context.user_id,
            active_only=active_only,
            limit=limit,
            offset=offset,
        )
        return {
            "active_only": active_only,
            "count": len(jobs),
            "jobs": [
                {
                    "job_id": job.id,
                    "status": job.status.value,
                    "run_at": job.run_at.isoformat(),
                    "recurrence": job.recurrence.value,
                    "recurrence_interval_seconds": job.recurrence_interval_seconds,
                    "recurrence_end_at": job.recurrence_end_at.isoformat() if job.recurrence_end_at else None,
                    "text": job.text,
                }
                for job in jobs
            ],
        }

    async def _handle_delete(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        owner_id = _require_owner(context)
        channel = _require_channel(context)
        job_id = _require_string(payload.get("job_id"), "job_id")
        result = await self._service.delete_prompt(
            job_id=job_id,
            owner_id=owner_id,
            channel=channel,
            chat_id=context.chat_id,
            user_id=context.user_id,
        )
        job = result["job"]
        if job is None:
            return {
                "job_id": job_id,
                "deleted": False,
                "stopped_before_delete": False,
                "message": "Job not found for this owner/chat context",
            }
        return {
            "job_id": job.id,
            "deleted": bool(result["deleted"]),
            "stopped_before_delete": bool(result["stopped_before_delete"]),
            "status_before_delete": job.status.value,
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


def _resolve_recurrence(value: Any, interval_value: Any) -> PromptRecurrence:
    if value is None:
        return PromptRecurrence.INTERVAL if interval_value is not None else PromptRecurrence.NONE
    if isinstance(value, PromptRecurrence):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        for recurrence in PromptRecurrence:
            if recurrence.value == normalized:
                return recurrence
    raise ValueError("invalid recurrence_type")


def _optional_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        return int(stripped)
    raise ValueError(f"{field} must be numeric")


def _optional_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError("active_only must be a boolean")


def _optional_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("recurrence_end_at must be an ISO 8601 string")
    parsed = datetime.fromisoformat(value)
    return _ensure_timezone(parsed)

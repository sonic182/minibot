from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from llm_async.models import Tool

from minibot.adapters.config.schema import TasksConfig
from minibot.adapters.tasks.manager import TaskManager
from minibot.app.agent_registry import AgentRegistry
from minibot.core.tasks import TaskLimits, TaskProducer, TaskRecord, TaskRepository, TaskRequest, TaskStatus
from minibot.llm.tools.arg_utils import (
    optional_int,
    optional_str,
    require_channel,
    require_non_empty_str,
    require_owner,
)
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import strict_object


class TaskTools:
    def __init__(
        self,
        producer: TaskProducer,
        task_manager: TaskManager,
        task_repository: TaskRepository | AgentRegistry | None = None,
        config: TasksConfig | None = None,
        agent_registry: AgentRegistry | None = None,
    ) -> None:
        if isinstance(task_repository, AgentRegistry) and agent_registry is None:
            agent_registry = task_repository
            task_repository = None
        self._producer = producer
        self._task_manager = task_manager
        self._task_repository = task_repository
        self._config = config or TasksConfig()
        self._agent_registry = agent_registry

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._spawn_schema(), handler=self._spawn_task),
            ToolBinding(tool=self._cancel_schema(), handler=self._cancel_task),
            ToolBinding(tool=self._list_schema(), handler=self._list_tasks),
            ToolBinding(tool=self._get_schema(), handler=self._get_task),
        ]

    def _spawn_schema(self) -> Tool:
        limit_property = {
            "anyOf": [
                {"type": "integer", "minimum": 1},
                {"type": "string", "enum": ["unlimited"]},
                {"type": "null"},
            ]
        }
        return Tool(
            name="spawn_task",
            description=load_tool_description("spawn_task"),
            parameters=strict_object(
                properties={
                    "prompt": {"type": "string", "description": "Task prompt for the worker agent."},
                    "agent_name": {
                        "type": ["string", "null"],
                        "description": "Optional exact specialist agent name to run asynchronously.",
                    },
                    "context_json": {
                        "type": ["string", "null"],
                        "description": "Optional JSON object string with structured context for the worker task.",
                    },
                    "timeout_seconds": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "description": "Optional timeout no greater than the configured task-worker timeout.",
                    },
                    "max_steps": {**limit_property, "description": "Optional execution-step limit or unlimited."},
                    "max_tool_calls": {**limit_property, "description": "Optional tool-call limit or unlimited."},
                },
                required=["prompt"],
            ),
        )

    def _cancel_schema(self) -> Tool:
        return Tool(
            name="cancel_task",
            description=load_tool_description("cancel_task"),
            parameters=strict_object(
                properties={"task_id": {"type": "string", "description": "Task identifier returned by spawn_task."}},
                required=["task_id"],
            ),
        )

    def _list_schema(self) -> Tool:
        return Tool(
            name="list_tasks",
            description=load_tool_description("list_tasks"),
            parameters=strict_object(
                properties={
                    "status": {"type": ["string", "null"], "description": "Optional task status filter."},
                    "limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 100},
                },
                required=[],
            ),
        )

    def _get_schema(self) -> Tool:
        return Tool(
            name="get_task",
            description=load_tool_description("get_task"),
            parameters=strict_object(
                properties={
                    "task_id": {"type": "string", "description": "Task identifier to retrieve."},
                    "include_events": {
                        "type": ["boolean", "null"],
                        "description": "Include compact execution history; defaults to true.",
                    },
                },
                required=["task_id"],
            ),
        )

    async def _spawn_task(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        task_id = str(uuid4())
        channel = require_channel(context, message="channel context is required for task spawning")
        owner_id = self._owner_id(context)
        prompt = require_non_empty_str(payload, "prompt")
        agent_name = optional_str(payload.get("agent_name"), error_message="agent_name must be a string or null")
        registry = self._agent_registry
        if agent_name is not None and registry is not None and registry.get(agent_name) is None:
            available = ", ".join(registry.names()) or "none registered"
            raise ValueError(f"agent_name '{agent_name}' is not a registered agent. Available: {available}")
        task_context = _coerce_task_context(payload)
        limits = _resolve_limits(payload, self._config)
        await self._producer.enqueue(
            TaskRequest(
                task_id=task_id,
                channel=channel,
                prompt=prompt,
                agent_name=agent_name,
                context=task_context,
                chat_id=context.chat_id,
                user_id=context.user_id,
                owner_id=owner_id,
                limits=limits,
            )
        )
        if (
            self._task_repository is not None
            and context.task_handoff_callback is not None
            and context.turn_id is not None
        ):
            await context.task_handoff_callback(context.turn_id)
        return {
            "task_id": task_id,
            "status": "queued",
            "task_status": TaskStatus.PENDING.value,
            "channel": channel,
            "chat_id": context.chat_id,
            "user_id": context.user_id,
            "agent_name": agent_name,
            "limits": _limits_payload(limits),
        }

    async def _cancel_task(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        task_id = require_non_empty_str(payload, "task_id")
        if self._task_repository is not None:
            task = await self._task_repository.get(task_id, self._owner_id(context))
            if task is None:
                return {"task_id": task_id, "cancelled": False, "reason": "not found"}
        cancelled = await self._task_manager.cancel(task_id)
        if not cancelled and self._task_repository is not None:
            cancelled = await self._task_repository.mark_cancelled(task_id)
        return {"task_id": task_id, "cancelled": cancelled}

    async def _list_tasks(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        status = _status_filter(payload.get("status"))
        limit = optional_int(payload.get("limit"), field="limit", min_value=1) or 20
        if self._task_repository is None:
            records = self._active_records()
        else:
            records = await self._task_repository.list(
                owner_id=self._owner_id(context),
                statuses=status,
                limit=min(limit, 100),
            )
        return {"tasks": [_record_summary(record) for record in records], "count": len(records)}

    async def _get_task(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        task_id = require_non_empty_str(payload, "task_id")
        if self._task_repository is None:
            return {"task_id": task_id, "found": False}
        record = await self._task_repository.get(task_id, self._owner_id(context))
        if record is None:
            return {"task_id": task_id, "found": False}
        include_events = payload.get("include_events") is not False
        response = {"task": _record_detail(record), "found": True}
        if include_events:
            response["events"] = await self._task_repository.events(task_id)
        return response

    def _owner_id(self, context: ToolContext) -> str:
        if self._task_repository is None:
            return context.owner_id or "primary"
        return require_owner(context)

    def _active_records(self) -> list[TaskRecord]:
        records: list[TaskRecord] = []
        for task in self._task_manager.active():
            records.append(
                TaskRecord(
                    request=TaskRequest(task_id=task.task_id, channel=task.channel, prompt=""),
                    status=TaskStatus.RUNNING,
                    started_at=task.started_at,
                )
            )
        return records


def _resolve_limits(payload: dict[str, Any], config: TasksConfig) -> TaskLimits:
    timeout_seconds = optional_int(payload.get("timeout_seconds"), field="timeout_seconds", min_value=1)
    effective_timeout = config.worker_timeout_seconds if timeout_seconds is None else timeout_seconds
    if effective_timeout > config.worker_timeout_seconds:
        raise ValueError("timeout_seconds may not exceed tasks.worker_timeout_seconds")
    return TaskLimits(
        timeout_seconds=effective_timeout,
        max_steps=_resolve_limit(payload.get("max_steps"), config.worker_max_steps, "max_steps"),
        max_tool_calls=_resolve_limit(payload.get("max_tool_calls"), config.worker_max_tool_calls, "max_tool_calls"),
    )


def _resolve_limit(value: Any, ceiling: int | str, field: str) -> int | None:
    if value is None:
        return None if ceiling == "unlimited" else int(ceiling)
    if value == "unlimited":
        if ceiling != "unlimited":
            raise ValueError(f"{field} may not be unlimited for this task system")
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer, unlimited, or null")
    if ceiling != "unlimited" and value > ceiling:
        raise ValueError(f"{field} may not exceed the configured task-worker limit")
    return value


def _status_filter(value: Any) -> list[TaskStatus] | None:
    status = optional_str(value, error_message="status must be a string or null")
    if status is None:
        return None
    try:
        return [TaskStatus(status)]
    except ValueError as exc:
        choices = ", ".join(item.value for item in TaskStatus)
        raise ValueError(f"status must be one of: {choices}") from exc


def _coerce_task_context(payload: dict[str, Any]) -> dict[str, Any]:
    legacy_context = payload.get("context")
    if legacy_context is not None:
        if not isinstance(legacy_context, dict):
            raise ValueError("task context must be an object")
        return dict(legacy_context)
    raw_context = optional_str(payload.get("context_json"), error_message="context_json must be a string or null")
    if raw_context is None:
        return {}
    try:
        value = json.loads(raw_context)
    except json.JSONDecodeError as exc:
        raise ValueError("context_json must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("task context must decode to an object")
    return value


def _limits_payload(limits: TaskLimits) -> dict[str, int | str]:
    return {
        "timeout_seconds": limits.timeout_seconds,
        "max_steps": limits.max_steps if limits.max_steps is not None else "unlimited",
        "max_tool_calls": limits.max_tool_calls if limits.max_tool_calls is not None else "unlimited",
    }


def _record_summary(record: TaskRecord) -> dict[str, Any]:
    return {
        "task_id": record.request.task_id,
        "status": record.status.value,
        "channel": record.request.channel,
        "agent_name": record.request.agent_name,
        "stop_reason": record.stop_reason.value if record.stop_reason else None,
        "progress": record.progress,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "started_at": record.started_at.isoformat() if record.started_at else None,
    }


def _record_detail(record: TaskRecord) -> dict[str, Any]:
    result = record.result
    return {
        **_record_summary(record),
        "limits": _limits_payload(record.request.limits),
        "last_error": record.last_error,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "result": {
            "text": result.text,
            "attachments": result.attachments,
            "metadata": result.metadata,
        }
        if result is not None
        else None,
    }

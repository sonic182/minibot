from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from llm_async.models import Tool

from minibot.config.schema import TasksConfig, task_limit
from minibot.core.agents import AgentCatalog, normalize_model_overrides
from minibot.core.tasks import (
    MAX_TASK_CONTINUATIONS,
    AmbiguousTaskIdError,
    TaskLimits,
    TaskManager,
    TaskProducer,
    TaskRecord,
    TaskRepository,
    TaskRequest,
    TaskStatus,
)
from minibot.llm.provider_options import ProviderOption, find_provider
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
from minibot.shared.errors import ToolInputError

_RESULT_PREVIEW_CHARS = 300
_SESSION_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_AMBIGUOUS_PREFIX_REASON = "ambiguous task id prefix: more than one task matches, use more characters of the id"


class TaskTools:
    def __init__(
        self,
        producer: TaskProducer,
        task_manager: TaskManager,
        task_repository: TaskRepository | AgentCatalog | None = None,
        config: TasksConfig | None = None,
        agent_registry: AgentCatalog | None = None,
        specialists_enabled: bool = True,
        providers: Sequence[ProviderOption] = (),
    ) -> None:
        if isinstance(task_repository, AgentCatalog) and agent_registry is None:
            agent_registry = task_repository
            task_repository = None
        self._producer = producer
        self._task_manager = task_manager
        self._task_repository = task_repository
        self._config = config or TasksConfig()
        self._agent_registry = agent_registry
        self._specialists_enabled = specialists_enabled
        self._providers = list(providers)

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._spawn_schema(), handler=self._spawn_task),
            ToolBinding(tool=self._cancel_schema(), handler=self._cancel_task),
            ToolBinding(tool=self._list_schema(), handler=self._list_tasks),
            ToolBinding(tool=self._get_schema(), handler=self._get_task),
        ]

    def _spawn_schema(self) -> Tool:
        properties: dict[str, Any] = {
            "prompt": {
                "type": "string",
                "description": (
                    "Task prompt for the worker agent. The worker cannot see this conversation, so include every "
                    "fact it needs."
                ),
            },
            "agent_name": {
                "type": ["string", "null"],
                "description": "Optional exact specialist agent name to run asynchronously.",
            },
            "context_json": {
                "type": ["string", "null"],
                "description": (
                    "Optional JSON object string with structured context for the worker task. The worker cannot "
                    "see this conversation: pass the texts, ids and data it needs here or in prompt."
                ),
            },
            "timeout_seconds": {
                "type": ["integer", "null"],
                "minimum": 1,
                "description": "Optional timeout no greater than the configured task-worker timeout.",
            },
            "model_provider": {
                "type": ["string", "null"],
                "description": "Optional provider name with configured credentials to run this task on.",
            },
            "model": {
                "type": ["string", "null"],
                "description": "Optional model id served by that provider.",
            },
            "reasoning_effort": {
                "type": ["string", "null"],
                "description": "Optional reasoning budget for this task (provider-specific).",
            },
            "continue_turn": {
                "type": ["boolean", "null"],
                "description": (
                    "True to get the result back as a new turn so you can keep working on it; "
                    "false to deliver the worker's answer straight to the user; null uses the "
                    "configured default."
                ),
            },
        }
        if self._config.history:
            properties["fresh"] = {
                "type": ["boolean", "null"],
                "description": "True clears this worker's history before the task so it starts with no memory.",
            }
            properties["session"] = {
                "type": ["string", "null"],
                "description": (
                    "Optional conversation name (letters, digits, '_', '-', '.'; max 64) to keep a separate history "
                    "with the same agent. Tasks with the same session run one at a time."
                ),
            }
        return Tool(
            name="spawn_task",
            description=load_tool_description("spawn_task_history" if self._config.history else "spawn_task"),
            parameters=strict_object(properties=properties, required=["prompt"]),
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
        if agent_name is not None and not self._specialists_enabled:
            raise ValueError(
                "agent_name is unavailable: specialist agents are disabled by "
                "[orchestration.specialists].enabled = false; omit agent_name to run a generic task"
            )
        registry = self._agent_registry
        spec = None if agent_name is None or registry is None else registry.get(agent_name)
        if agent_name is not None and registry is not None and spec is None:
            available = ", ".join(registry.names()) or "none registered"
            raise ValueError(f"agent_name '{agent_name}' is not a registered agent. Available: {available}")
        model_overrides = normalize_model_overrides(payload)
        requested_provider = model_overrides.get("model_provider")
        if requested_provider is not None and find_provider(requested_provider, self._providers) is None:
            available = ", ".join(option.name for option in self._providers) or "none configured"
            raise ValueError(
                f"model_provider '{requested_provider}' has no configured credentials. Available: {available}"
            )
        task_context = _coerce_task_context(payload)
        limits = _resolve_limits(payload, self._config, spec_timeout_seconds=spec.timeout_seconds if spec else None)
        history_session = self._history_session(payload, channel, context.chat_id, agent_name)
        continuation_depth = _resolve_continuation_depth(
            payload,
            context,
            default=self._config.continue_turn_default,
            mode=self._config.continue_turn_mode,
        )
        try:
            await self._producer.enqueue(
                TaskRequest(
                    task_id=task_id,
                    channel=channel,
                    prompt=prompt,
                    agent_name=agent_name,
                    context=task_context,
                    model_overrides=model_overrides,
                    chat_id=context.chat_id,
                    user_id=context.user_id,
                    owner_id=owner_id,
                    limits=limits,
                    continuation_depth=continuation_depth,
                    fresh=history_session is not None and payload.get("fresh") is True,
                    history_session=history_session,
                )
            )
        except Exception:
            if continuation_depth is not None and context.release_task_continuation is not None:
                context.release_task_continuation()
            raise
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
            "model_overrides": model_overrides,
            "limits": _limits_payload(limits),
            "continue_turn": continuation_depth is not None,
        }

    def _history_session(
        self, payload: dict[str, Any], channel: str, chat_id: int | None, agent_name: str | None
    ) -> str | None:
        if not self._config.history:
            return None
        session = optional_str(payload.get("session"))
        if session is not None and not _SESSION_NAME.fullmatch(session):
            raise ValueError("session must be 1-64 letters, digits, '_', '-' or '.'")
        if chat_id is None or (agent_name is None and session is None):
            return None
        key = f"task:{channel}:{chat_id}:{agent_name or 'task_worker'}"
        return f"{key}:{session}" if session else key

    async def _cancel_task(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        task_id = require_non_empty_str(payload, "task_id")
        if self._task_repository is not None:
            try:
                task = await self._task_repository.get(task_id, self._owner_id(context))
            except AmbiguousTaskIdError:
                return {"task_id": task_id, "cancelled": False, "reason": _AMBIGUOUS_PREFIX_REASON}
            if task is None:
                return {"task_id": task_id, "cancelled": False, "reason": "not found"}
            task_id = task.request.task_id
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
        tasks = [
            {
                **_record_summary(record),
                "result_preview": record.result.text[:_RESULT_PREVIEW_CHARS] if record.result else None,
            }
            for record in records
        ]
        return {"tasks": tasks, "count": len(records)}

    async def _get_task(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        task_id = require_non_empty_str(payload, "task_id")
        if self._task_repository is None:
            return {"task_id": task_id, "found": False}
        try:
            record = await self._task_repository.get(task_id, self._owner_id(context))
        except AmbiguousTaskIdError:
            return {"task_id": task_id, "found": False, "reason": _AMBIGUOUS_PREFIX_REASON}
        if record is None:
            return {"task_id": task_id, "found": False}
        include_events = payload.get("include_events") is not False
        response = {"task": _record_detail(record), "found": True}
        if include_events:
            response["events"] = await self._task_repository.events(record.request.task_id)
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


def _resolve_limits(
    payload: dict[str, Any], config: TasksConfig, *, spec_timeout_seconds: int | None = None
) -> TaskLimits:
    """Resolve this task's budget.

    The agent's own ``timeout_seconds`` is the default when the call names none. It is resolved
    here and not in the worker because the daemon-side supervisor derives its deadline from the
    same ``TaskLimits`` (``app/tasks/manager.py``); deciding it in the subprocess would let
    the two disagree.
    """
    timeout_seconds = optional_int(payload.get("timeout_seconds"), field="timeout_seconds", min_value=1)
    if timeout_seconds is None and spec_timeout_seconds:
        timeout_seconds = min(spec_timeout_seconds, config.worker_timeout_seconds)
    effective_timeout = config.worker_timeout_seconds if timeout_seconds is None else timeout_seconds
    if effective_timeout > config.worker_timeout_seconds:
        raise ValueError("timeout_seconds may not exceed tasks.worker_timeout_seconds")
    return TaskLimits(
        timeout_seconds=effective_timeout,
        max_steps=task_limit(config.worker_max_steps),
        max_tool_calls=task_limit(config.worker_max_tool_calls),
    )


def _resolve_continuation_depth(
    payload: dict[str, Any], context: ToolContext, *, default: bool, mode: str = "auto"
) -> int | None:
    if mode == "always":
        continue_turn = None
        default = True
    else:
        continue_turn = payload.get("continue_turn")
    if continue_turn is not None and not isinstance(continue_turn, bool):
        raise ToolInputError("continue_turn must be a boolean or null", error_code="invalid_tool_arguments")
    if continue_turn is None:
        if not default:
            return None
        if context.task_chain_depth >= MAX_TASK_CONTINUATIONS:
            return None
        if context.claim_task_continuation is not None and not context.claim_task_continuation():
            return None
        return context.task_chain_depth + 1
    if not continue_turn:
        return None
    if context.task_chain_depth >= MAX_TASK_CONTINUATIONS:
        raise ToolInputError(
            f"This turn already continues from {context.task_chain_depth} chained tasks, which is the limit. "
            "Answer the user now, or spawn the task with continue_turn false.",
            error_code="task:continuation_limit",
        )
    if context.claim_task_continuation is not None and not context.claim_task_continuation():
        raise ToolInputError(
            f"This turn already started {MAX_TASK_CONTINUATIONS} tasks with continue_turn, which is the limit. "
            "Wait for their results, or spawn this task with continue_turn false.",
            error_code="task:continuation_limit",
        )
    return context.task_chain_depth + 1


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

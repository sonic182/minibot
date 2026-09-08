from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from llm_async.models import Tool

from minibot.adapters.tasks.manager import TaskManager
from minibot.app.agent_registry import AgentRegistry
from minibot.core.tasks import TaskProducer, TaskRequest
from minibot.llm.tools.arg_utils import optional_str, require_channel, require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import empty_object_schema, strict_object


class TaskTools:
    def __init__(
        self,
        producer: TaskProducer,
        task_manager: TaskManager,
        agent_registry: AgentRegistry | None = None,
    ) -> None:
        self._producer = producer
        self._task_manager = task_manager
        self._agent_registry = agent_registry

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._spawn_schema(), handler=self._spawn_task),
            ToolBinding(tool=self._cancel_schema(), handler=self._cancel_task),
            ToolBinding(tool=self._list_schema(), handler=self._list_tasks),
        ]

    def _spawn_schema(self) -> Tool:
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
            parameters=empty_object_schema(),
        )

    async def _spawn_task(self, payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        task_id = str(uuid4())
        channel = require_channel(context, message="channel context is required for task spawning")
        prompt = require_non_empty_str(payload, "prompt")
        agent_name = optional_str(payload.get("agent_name"), error_message="agent_name must be a string or null")
        registry = self._agent_registry
        if agent_name is not None and registry is not None and registry.get(agent_name) is None:
            available = ", ".join(registry.names()) or "none registered"
            raise ValueError(f"agent_name '{agent_name}' is not a registered agent. Available: {available}")
        task_context = _coerce_task_context(payload)

        await self._producer.enqueue(
            TaskRequest(
                task_id=task_id,
                channel=channel,
                prompt=prompt,
                agent_name=agent_name,
                context=task_context,
                chat_id=context.chat_id,
                user_id=context.user_id,
            )
        )

        return {
            "task_id": task_id,
            "status": "queued",
            "channel": channel,
            "chat_id": context.chat_id,
            "user_id": context.user_id,
            "agent_name": agent_name,
        }

    async def _cancel_task(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        task_id = require_non_empty_str(payload, "task_id")
        cancelled = await self._task_manager.cancel(task_id)
        if not cancelled:
            return {"task_id": task_id, "cancelled": False, "reason": "not found"}
        return {"task_id": task_id, "cancelled": True}

    async def _list_tasks(self, _: dict[str, Any], __: ToolContext) -> dict[str, Any]:
        now = datetime.now(UTC)
        tasks = [
            {
                "task_id": task.task_id,
                "channel": task.channel,
                "started_at": task.started_at.isoformat(),
                "elapsed_seconds": round(max(0.0, (now - task.started_at).total_seconds()), 3),
            }
            for task in self._task_manager.active()
        ]
        return {"tasks": tasks, "count": len(tasks)}


def _coerce_task_context(payload: dict[str, Any]) -> dict[str, Any]:
    if "context_json" in payload:
        raw_context = optional_str(payload.get("context_json"), error_message="context_json must be a string or null")
        if raw_context is None or not raw_context.strip():
            return {}
        try:
            value = json.loads(raw_context)
        except json.JSONDecodeError as exc:
            raise ValueError("context_json must be valid JSON") from exc
    else:
        value = payload.get("context")
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("task context must decode to an object")
    return value

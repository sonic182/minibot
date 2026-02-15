from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from llm_async.models import Tool

from minibot.app.agent_policies import filter_tools_for_agent
from minibot.app.agent_registry import AgentRegistry
from minibot.app.agent_runtime import AgentRuntime
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart, RuntimeLimits
from minibot.core.agents import AgentSpec
from minibot.llm.tools.arg_utils import optional_str, require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.schema_utils import empty_object_schema, strict_object, string_field


@dataclass(frozen=True)
class _DelegationOutcome:
    text: str
    payload: Any


class AgentDelegateTool:
    def __init__(
        self,
        *,
        registry: AgentRegistry,
        llm_factory: LLMClientFactory,
        tools: Sequence[ToolBinding],
        default_timeout_seconds: int,
    ) -> None:
        self._registry = registry
        self._llm_factory = llm_factory
        self._tools = list(tools)
        self._default_timeout_seconds = default_timeout_seconds

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._list_agents_schema(), handler=self._list_agents),
            ToolBinding(tool=self._invoke_agent_schema(), handler=self._invoke_agent),
        ]

    def _list_agents_schema(self) -> Tool:
        return Tool(
            name="list_agents",
            description=(
                "List available specialist agents that can be invoked by name. "
                "Use this before invoking a specialist when uncertain about agent names."
            ),
            parameters=empty_object_schema(),
        )

    def _invoke_agent_schema(self) -> Tool:
        return Tool(
            name="invoke_agent",
            description=(
                "Invoke a specialist agent to handle a delegated task. "
                "Wait for its result and then continue with your own final answer."
            ),
            parameters=strict_object(
                properties={
                    "agent_name": string_field("Exact name returned by list_agents."),
                    "task": string_field("Concrete delegated task for the specialist."),
                    "context": string_field("Optional supporting context for the specialist."),
                },
                required=["agent_name", "task"],
            ),
        )

    async def _list_agents(self, _: dict[str, object], __: ToolContext) -> dict[str, object]:
        names = sorted(spec.name for spec in self._registry.all())
        return {"agents": names, "count": len(names)}

    async def _invoke_agent(self, payload: dict[str, object], context: ToolContext) -> dict[str, object]:
        agent_name = require_non_empty_str(payload, "agent_name")
        task = require_non_empty_str(payload, "task")
        details = optional_str(payload.get("context"), error_message="context must be a string")
        spec = self._registry.get(agent_name)
        if spec is None:
            return {
                "ok": False,
                "agent": agent_name,
                "error_code": "agent_not_found",
                "error": f"agent '{agent_name}' is not available",
            }

        llm_client = self._llm_factory.create_for_agent(spec)
        scoped_tools = self._scoped_tools(spec)
        max_tool_iterations = _max_tool_iterations(llm_client)
        runtime = AgentRuntime(
            llm_client=llm_client,
            tools=scoped_tools,
            limits=RuntimeLimits(
                max_steps=max(1, max_tool_iterations),
                max_tool_calls=max(12, max_tool_iterations * 2),
                timeout_seconds=max(30, int(self._default_timeout_seconds)),
            ),
            allowed_append_message_tools=["self_insert_artifact"],
            allow_system_inserts=False,
            managed_files_root=None,
        )
        state = self._build_state(spec=spec, task=task, details=details)
        try:
            generation = await runtime.run(
                state=state,
                tool_context=context,
                response_schema=_agent_response_schema(),
                prompt_cache_key=None,
            )
            outcome = _extract_outcome(generation.payload)
            return {
                "ok": True,
                "agent": spec.name,
                "result": outcome.text,
                "payload": outcome.payload,
                "tool_count": len(scoped_tools),
                "total_tokens": int(generation.total_tokens or 0),
            }
        except Exception as exc:
            return {
                "ok": False,
                "agent": spec.name,
                "error_code": "delegated_agent_failed",
                "error": str(exc),
            }

    def _scoped_tools(self, spec: AgentSpec) -> list[ToolBinding]:
        scoped = filter_tools_for_agent(self._tools, spec)
        return [binding for binding in scoped if binding.tool.name != "invoke_agent"]

    @staticmethod
    def _build_state(*, spec: AgentSpec, task: str, details: str | None) -> AgentState:
        user_text = task
        if details:
            user_text = f"Task:\n{task}\n\nContext:\n{details}"
        return AgentState(
            messages=[
                AgentMessage(role="system", content=[MessagePart(type="text", text=spec.system_prompt)]),
                AgentMessage(role="user", content=[MessagePart(type="text", text=user_text)]),
            ]
        )


def _max_tool_iterations(llm_client: Any) -> int:
    max_tool_iterations_getter = getattr(llm_client, "max_tool_iterations", None)
    if callable(max_tool_iterations_getter):
        maybe_iterations = max_tool_iterations_getter()
        if isinstance(maybe_iterations, int) and maybe_iterations > 0:
            return maybe_iterations
    return 8


def _agent_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "answer": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["text", "html", "markdown", "json"]},
                    "content": {"type": "string"},
                },
                "required": ["kind", "content"],
                "additionalProperties": False,
            },
            "should_answer_to_user": {"type": "boolean"},
        },
        "required": ["answer", "should_answer_to_user"],
        "additionalProperties": False,
    }


def _extract_outcome(payload: Any) -> _DelegationOutcome:
    if isinstance(payload, dict):
        answer = payload.get("answer")
        if isinstance(answer, dict):
            content = answer.get("content")
            if isinstance(content, str) and content.strip():
                return _DelegationOutcome(text=content, payload=payload)
        return _DelegationOutcome(text=str(payload), payload=payload)
    if isinstance(payload, str):
        return _DelegationOutcome(text=payload, payload=payload)
    return _DelegationOutcome(text=str(payload), payload=payload)

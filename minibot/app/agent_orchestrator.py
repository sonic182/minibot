from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any, Sequence, cast

from minibot.adapters.config.schema import Settings
from minibot.app.agent_policies import filter_tools_for_agent
from minibot.app.agent_registry import AgentRegistry
from minibot.app.agent_runtime import AgentRuntime
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart, MessageRole, RuntimeLimits
from minibot.core.agents import AgentSpec, DelegationDecision
from minibot.llm.tools.base import ToolBinding, ToolContext


@dataclass(frozen=True)
class OrchestratedRunResult:
    payload: Any
    total_tokens: int
    primary_agent: str
    agent_trace: list[dict[str, Any]]
    fallback_used: bool


class AgentOrchestrator:
    def __init__(
        self,
        *,
        settings: Settings,
        llm_factory: LLMClientFactory,
        registry: AgentRegistry,
        tools: Sequence[ToolBinding],
    ) -> None:
        self._settings = settings
        self._llm_factory = llm_factory
        self._registry = registry
        self._tools = list(tools)
        self._logger = logging.getLogger("minibot.agent_orchestrator")

    async def maybe_delegate(
        self,
        *,
        history: Sequence[Any],
        user_text: str,
        user_content: str | list[dict[str, Any]] | None,
        tool_context: ToolContext,
        response_schema: dict[str, Any],
        prompt_cache_key: str | None,
    ) -> OrchestratedRunResult | None:
        if not self._settings.agents.enabled or self._registry.is_empty():
            return None
        decision = await self._decide_agent(history=history, user_text=user_text, prompt_cache_key=prompt_cache_key)
        if not decision.should_delegate or not decision.agent_name:
            return None
        spec = self._registry.get(decision.agent_name)
        if spec is None:
            return None
        llm_client = self._llm_factory.create_for_agent(spec)
        scoped_tools = filter_tools_for_agent(self._tools, spec)
        max_tool_iterations_getter = getattr(llm_client, "max_tool_iterations", None)
        max_tool_iterations = 8
        if callable(max_tool_iterations_getter):
            maybe_iterations = max_tool_iterations_getter()
            if isinstance(maybe_iterations, int) and maybe_iterations > 0:
                max_tool_iterations = maybe_iterations
        runtime_limits = RuntimeLimits(
            max_steps=max(1, max_tool_iterations),
            max_tool_calls=max(12, max_tool_iterations * 2),
            timeout_seconds=max(30, int(self._settings.agents.default_timeout_seconds)),
        )
        runtime = AgentRuntime(
            llm_client=llm_client,
            tools=scoped_tools,
            limits=runtime_limits,
            allowed_append_message_tools=["self_insert_artifact"],
            allow_system_inserts=False,
            managed_files_root=None,
        )
        state = self._build_state(
            history=history,
            user_text=user_text,
            user_content=user_content,
            system_prompt=spec.system_prompt,
        )
        try:
            generation = await runtime.run(
                state=state,
                tool_context=tool_context,
                response_schema=response_schema,
                prompt_cache_key=f"{prompt_cache_key}:agent:{spec.name}" if prompt_cache_key else None,
            )
            return OrchestratedRunResult(
                payload=generation.payload,
                total_tokens=max(0, int(generation.total_tokens or 0)),
                primary_agent=spec.name,
                agent_trace=[
                    {
                        "agent": "supervisor",
                        "decision": "delegated",
                        "target": spec.name,
                        "reason": decision.reason,
                    },
                    {
                        "agent": spec.name,
                        "tool_count": len(scoped_tools),
                    },
                ],
                fallback_used=False,
            )
        except Exception as exc:
            self._logger.exception("delegated agent failed", exc_info=exc, extra={"agent": spec.name})
            return OrchestratedRunResult(
                payload=None,
                total_tokens=0,
                primary_agent="supervisor",
                agent_trace=[
                    {
                        "agent": "supervisor",
                        "decision": "delegated",
                        "target": spec.name,
                        "reason": decision.reason,
                    },
                    {
                        "agent": spec.name,
                        "error": str(exc),
                    },
                ],
                fallback_used=True,
            )

    async def _decide_agent(
        self,
        *,
        history: Sequence[Any],
        user_text: str,
        prompt_cache_key: str | None,
    ) -> DelegationDecision:
        default_client = self._llm_factory.create_default()
        candidates = self._eligible_candidates()
        if not candidates:
            return DelegationDecision(False)
        names = [item.name for item in candidates]
        prompt = (
            "Decide whether to delegate this request to one specialist agent. "
            "Return JSON object with fields should_delegate:boolean, agent_name:string|null, reason:string. "
            "If no delegation is needed, set should_delegate=false and agent_name=null.\n\n"
            f"User request:\n{user_text}\n\n"
            "Agents:\n"
            + "\n".join([f"- {item.name}: {item.description}" for item in candidates])
        )
        try:
            generation = await default_client.generate(
                history,
                prompt,
                user_content=None,
                tools=[],
                tool_context=None,
                response_schema={
                    "type": "object",
                    "properties": {
                        "should_delegate": {"type": "boolean"},
                        "agent_name": {"type": ["string", "null"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["should_delegate", "agent_name", "reason"],
                    "additionalProperties": False,
                },
                prompt_cache_key=f"{prompt_cache_key}:agent-router" if prompt_cache_key else None,
                previous_response_id=None,
                system_prompt_override="You are a strict routing classifier.",
            )
            payload = generation.payload
            parsed = payload if isinstance(payload, dict) else json.loads(str(payload))
            if not isinstance(parsed, dict):
                return DelegationDecision(False)
            should_delegate = bool(parsed.get("should_delegate", False))
            agent_name = parsed.get("agent_name")
            if not should_delegate:
                return DelegationDecision(False, reason=str(parsed.get("reason", "")))
            if not isinstance(agent_name, str) or agent_name not in names:
                return DelegationDecision(False)
            reason = str(parsed.get("reason", "")).strip()
            return DelegationDecision(True, agent_name=agent_name, reason=reason)
        except Exception:
            lowered = user_text.lower().strip()
            if "playwright" in lowered or "browse" in lowered or "browser" in lowered:
                preferred = next((name for name in names if "playwright" in name), None)
                if preferred is not None:
                    return DelegationDecision(True, preferred, "heuristic: browsing intent")
            if any(token in lowered for token in {"file", "workspace", "folder", "delete", "move", "rename"}):
                preferred = next((name for name in names if "workspace" in name), None)
                if preferred is not None:
                    return DelegationDecision(True, preferred, "heuristic: workspace intent")
            return DelegationDecision(False)

    def _eligible_candidates(self) -> list[AgentSpec]:
        candidates = list(self._registry.all())
        allowed = [name.strip() for name in self._settings.agents.supervisor.allowed_delegate_agents if name.strip()]
        if not allowed:
            return candidates
        allowed_set = set(allowed)
        return [candidate for candidate in candidates if candidate.name in allowed_set]

    @staticmethod
    def _build_state(
        *,
        history: Sequence[Any],
        user_text: str,
        user_content: str | list[dict[str, Any]] | None,
        system_prompt: str,
    ) -> AgentState:
        messages: list[AgentMessage] = [
            AgentMessage(role="system", content=[MessagePart(type="text", text=system_prompt)])
        ]
        for entry in history:
            role = str(getattr(entry, "role", "user"))
            if role not in {"system", "user", "assistant", "tool"}:
                continue
            content = getattr(entry, "content", "")
            messages.append(
                AgentMessage(
                    role=cast(MessageRole, role),
                    content=[MessagePart(type="text", text=str(content))],
                )
            )
        if user_content is None:
            messages.append(AgentMessage(role="user", content=[MessagePart(type="text", text=user_text)]))
        elif isinstance(user_content, str):
            messages.append(AgentMessage(role="user", content=[MessagePart(type="text", text=user_content)]))
        else:
            messages.append(
                AgentMessage(
                    role="user",
                    content=[MessagePart(type="text", text=user_text)],
                    raw_content=user_content,
                )
            )
        return AgentState(messages=messages)

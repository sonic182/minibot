from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Sequence, cast

from minibot.app.agent_runtime import AgentRuntime
from minibot.app.delegation_trace import count_tool_messages, extract_delegation_trace
from minibot.app.response_parser import ParsedAnswer, extract_answer, plain_render
from minibot.core.channels import RenderableResponse
from minibot.app.tool_use_guardrail import ToolUseGuardrail
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart, MessageRole
from minibot.llm.provider_factory import LLMClient
from minibot.llm.tools.base import ToolContext

from minibot.app.handlers.services.session_state_service import SessionStateService


@dataclass(frozen=True)
class AgentRuntimeResult:
    render: Any
    should_reply: bool
    response_updates: list[RenderableResponse]
    response_id: str | None
    runtime_state: AgentState | None
    agent_trace: list[dict[str, Any]]
    delegation_fallback_used: bool
    tokens_used: int
    provider_tool_calls: int


class RuntimeOrchestrationService:
    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        llm_client: LLMClient,
        guardrail: ToolUseGuardrail,
        session_state: SessionStateService,
        logger: logging.Logger,
    ) -> None:
        self._runtime = runtime
        self._llm_client = llm_client
        self._guardrail = guardrail
        self._session_state = session_state
        self._logger = logger

    async def run_with_agent_runtime(
        self,
        *,
        session_id: str,
        history: list[Any],
        model_text: str,
        model_user_content: str | list[dict[str, Any]] | None,
        system_prompt: str,
        tool_context: ToolContext,
        prompt_cache_key: str | None,
        previous_response_id: str | None,
        chat_id: int | None,
        channel: str | None,
    ) -> AgentRuntimeResult:
        tokens_used = 0
        trace_result = None
        response_updates: list[RenderableResponse] = []

        state = self._build_agent_state(
            history=history,
            user_text=model_text,
            user_content=model_user_content,
            system_prompt=system_prompt,
        )
        generation = await self._runtime.run(
            state=state,
            tool_context=tool_context,
            prompt_cache_key=prompt_cache_key,
            initial_previous_response_id=previous_response_id,
        )
        tokens_used += self._session_state.track_tokens(session_id, getattr(generation, "total_tokens", None))
        tool_messages_count = count_tool_messages(generation.state)
        provider_tool_calls = int(getattr(generation, "provider_tool_calls", 0) or 0)
        trace_result = extract_delegation_trace(generation.state)

        guardrail = None
        if tool_messages_count == 0 and provider_tool_calls == 0:
            guardrail = await self._guardrail.apply(
                session_id=session_id,
                user_text=model_text,
                tool_context=tool_context,
                state=generation.state,
                system_prompt=system_prompt,
                prompt_cache_key=prompt_cache_key,
            )
            tokens_used += self._session_state.track_tokens(session_id, guardrail.tokens_used)
        else:
            self._logger.debug(
                "tool use guardrail skipped because tools already executed",
                extra={
                    "session_id": session_id,
                    "chat_id": chat_id,
                    "tool_messages_count": tool_messages_count,
                    "provider_tool_calls": provider_tool_calls,
                },
            )

        if guardrail is not None and guardrail.resolved_render_text is not None:
            return AgentRuntimeResult(
                render=plain_render(guardrail.resolved_render_text),
                should_reply=True,
                response_updates=[],
                response_id=generation.response_id,
                runtime_state=generation.state,
                agent_trace=trace_result.trace,
                delegation_fallback_used=trace_result.fallback_used,
                tokens_used=tokens_used,
                provider_tool_calls=provider_tool_calls,
            )

        if guardrail is not None and guardrail.requires_retry and tool_messages_count == 0:
            retry_system_prompt = system_prompt
            if guardrail.retry_system_prompt_suffix:
                retry_system_prompt = f"{system_prompt}\n\n{guardrail.retry_system_prompt_suffix}"
            retry_state = self._build_agent_state(
                history=history,
                user_text=model_text,
                user_content=model_user_content,
                system_prompt=retry_system_prompt,
            )
            generation = await self._runtime.run(
                state=retry_state,
                tool_context=tool_context,
                prompt_cache_key=prompt_cache_key,
                initial_previous_response_id=None,
            )
            tokens_used += self._session_state.track_tokens(session_id, getattr(generation, "total_tokens", None))
            trace_result = extract_delegation_trace(generation.state)
            provider_tool_calls = int(getattr(generation, "provider_tool_calls", 0) or 0)
            if count_tool_messages(generation.state) == 0 and provider_tool_calls == 0:
                return AgentRuntimeResult(
                    render=plain_render(
                        "I could not verify or execute that action with tools in this attempt. "
                        "Please try again, or ask me to run a specific tool."
                    ),
                    should_reply=True,
                    response_updates=[],
                    response_id=generation.response_id,
                    runtime_state=generation.state,
                    agent_trace=trace_result.trace,
                    delegation_fallback_used=trace_result.fallback_used,
                    tokens_used=tokens_used,
                    provider_tool_calls=provider_tool_calls,
                )

        parsed = extract_answer(generation.payload, pre_response_meta=generation.pre_response_meta)
        render = parsed.render
        should_reply = parsed.has_visible_answer

        assert trace_result is not None
        return AgentRuntimeResult(
            render=render,
            should_reply=should_reply,
            response_updates=response_updates,
            response_id=generation.response_id,
            runtime_state=generation.state,
            agent_trace=trace_result.trace,
            delegation_fallback_used=trace_result.fallback_used,
            tokens_used=tokens_used,
            provider_tool_calls=provider_tool_calls,
        )

    @staticmethod
    def _build_agent_state(
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
            if role not in {"system", "user", "assistant"}:
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

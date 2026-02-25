from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Sequence, cast

from minibot.app.agent_runtime import AgentRuntime
from minibot.app.delegation_trace import count_tool_messages, extract_delegation_trace
from minibot.app.response_parser import extract_answer, plain_render
from minibot.app.tool_use_guardrail import ToolUseGuardrail
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart, MessageRole
from minibot.llm.provider_factory import LLMClient
from minibot.llm.tools.base import ToolContext

from minibot.app.handlers.services.session_state_service import SessionStateService


@dataclass(frozen=True)
class AgentRuntimeResult:
    render: Any
    should_reply: bool
    response_id: str | None
    agent_trace: list[dict[str, Any]]
    delegation_fallback_used: bool
    tokens_used: int


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
        response_schema: dict[str, Any],
    ) -> AgentRuntimeResult:
        tokens_used = 0
        trace_result = None

        state = self._build_agent_state(
            history=history,
            user_text=model_text,
            user_content=model_user_content,
            system_prompt=system_prompt,
        )
        generation = await self._runtime.run(
            state=state,
            tool_context=tool_context,
            response_schema=response_schema,
            prompt_cache_key=prompt_cache_key,
            initial_previous_response_id=previous_response_id,
        )
        tokens_used += self._session_state.track_tokens(session_id, getattr(generation, "total_tokens", None))
        tool_messages_count = count_tool_messages(generation.state)
        trace_result = extract_delegation_trace(generation.state)
        delegation_unresolved = trace_result.unresolved

        guardrail = await self._guardrail.apply(
            session_id=session_id,
            user_text=model_text,
            tool_context=tool_context,
            state=generation.state,
            system_prompt=system_prompt,
            prompt_cache_key=prompt_cache_key,
        )
        tokens_used += guardrail.tokens_used
        self._session_state.track_tokens(session_id, guardrail.tokens_used)

        if guardrail.resolved_render_text is not None:
            return AgentRuntimeResult(
                render=plain_render(guardrail.resolved_render_text),
                should_reply=True,
                response_id=generation.response_id,
                agent_trace=trace_result.trace,
                delegation_fallback_used=trace_result.fallback_used,
                tokens_used=tokens_used,
            )

        if guardrail.requires_retry and tool_messages_count == 0:
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
                response_schema=response_schema,
                prompt_cache_key=prompt_cache_key,
                initial_previous_response_id=generation.response_id,
            )
            tokens_used += self._session_state.track_tokens(session_id, getattr(generation, "total_tokens", None))
            trace_result = extract_delegation_trace(generation.state)
            delegation_unresolved = trace_result.unresolved
            render, should_reply = extract_answer(generation.payload, logger=self._logger)
            if count_tool_messages(generation.state) == 0:
                return AgentRuntimeResult(
                    render=plain_render(
                        "I could not verify or execute that action with tools in this attempt. "
                        "Please try again, or ask me to run a specific tool."
                    ),
                    should_reply=True,
                    response_id=generation.response_id,
                    agent_trace=trace_result.trace,
                    delegation_fallback_used=trace_result.fallback_used,
                    tokens_used=tokens_used,
                )
        else:
            render, should_reply = extract_answer(generation.payload, logger=self._logger)

        if delegation_unresolved:
            retry_state = self._build_agent_state(
                history=history,
                user_text=model_text,
                user_content=model_user_content,
                system_prompt=(
                    f"{system_prompt}\n\n"
                    "Delegation policy reminder: If invoke_agent result has should_answer_to_user=false "
                    "or result_status other than success, you must resolve it in this turn. "
                    "Do one additional concrete tool call or return an explicit failure message to user "
                    "with should_answer_to_user=true."
                ),
            )
            generation = await self._runtime.run(
                state=retry_state,
                tool_context=tool_context,
                response_schema=response_schema,
                prompt_cache_key=prompt_cache_key,
                initial_previous_response_id=generation.response_id,
            )
            tokens_used += self._session_state.track_tokens(session_id, getattr(generation, "total_tokens", None))
            trace_result = extract_delegation_trace(generation.state)
            delegation_unresolved = trace_result.unresolved
            render, should_reply = extract_answer(generation.payload, logger=self._logger)
            if delegation_unresolved:
                self._logger.warning(
                    "delegation unresolved after bounded retry; returning explicit failure",
                    extra={"chat_id": chat_id, "channel": channel},
                )
                render = plain_render(
                    "I could not complete that delegated action reliably in this attempt. "
                    "Please retry, or ask me to run a specific tool step-by-step."
                )
                should_reply = True

        assert trace_result is not None
        return AgentRuntimeResult(
            render=render,
            should_reply=should_reply,
            response_id=generation.response_id,
            agent_trace=trace_result.trace,
            delegation_fallback_used=trace_result.fallback_used,
            tokens_used=tokens_used,
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

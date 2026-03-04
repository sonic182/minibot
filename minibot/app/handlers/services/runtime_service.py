from __future__ import annotations

from dataclasses import dataclass
import logging
import re
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
    runtime_state: AgentState | None
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
        apply_patch_required = _requires_apply_patch_for_edit_request(model_text)

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

        if apply_patch_required and not _state_has_tool_call(generation.state, "apply_patch"):
            self._logger.debug(
                "apply_patch policy enforcing retry for existing-file edit request",
                extra={"session_id": session_id, "chat_id": chat_id},
            )
            retry_state = self._build_agent_state(
                history=history,
                user_text=model_text,
                user_content=model_user_content,
                system_prompt=f"{system_prompt}\n\n{_apply_patch_enforcement_suffix()}",
            )
            generation = await self._runtime.run(
                state=retry_state,
                tool_context=tool_context,
                response_schema=response_schema,
                prompt_cache_key=prompt_cache_key,
                initial_previous_response_id=generation.response_id,
            )
            tokens_used += self._session_state.track_tokens(session_id, getattr(generation, "total_tokens", None))
            tool_messages_count = count_tool_messages(generation.state)
            trace_result = extract_delegation_trace(generation.state)
            delegation_unresolved = trace_result.unresolved
            if not _state_has_tool_call(generation.state, "apply_patch"):
                return AgentRuntimeResult(
                    render=plain_render(
                        "I could not apply changes to the existing file with apply_patch in this attempt. "
                        "Please ask me again with the exact target path and I will patch it in place."
                    ),
                    should_reply=True,
                    response_id=generation.response_id,
                    runtime_state=generation.state,
                    agent_trace=trace_result.trace,
                    delegation_fallback_used=trace_result.fallback_used,
                    tokens_used=tokens_used,
                )

        guardrail = None
        if tool_messages_count == 0:
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
        else:
            self._logger.debug(
                "tool use guardrail skipped because tools already executed",
                extra={
                    "session_id": session_id,
                    "chat_id": chat_id,
                    "tool_messages_count": tool_messages_count,
                },
            )

        if guardrail is not None and guardrail.resolved_render_text is not None:
            return AgentRuntimeResult(
                render=plain_render(guardrail.resolved_render_text),
                should_reply=True,
                response_id=generation.response_id,
                runtime_state=generation.state,
                agent_trace=trace_result.trace,
                delegation_fallback_used=trace_result.fallback_used,
                tokens_used=tokens_used,
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
                    runtime_state=generation.state,
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
            runtime_state=generation.state,
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


def _state_has_tool_call(state: AgentState, tool_name: str) -> bool:
    for message in state.messages:
        if message.role == "tool" and message.name == tool_name:
            return True
    return False


def _requires_apply_patch_for_edit_request(text: str) -> bool:
    lowered = text.lower()
    force_terms = ("apply patch", "apply_patch", "usa apply patch", "use apply patch")
    if any(term in lowered for term in force_terms):
        return True
    create_terms = ("create ", "crear ", "nuevo archivo", "new file", "from scratch", "guardar como")
    if any(term in lowered for term in create_terms):
        return False
    edit_terms = (
        "refactor",
        "modifica",
        "editar",
        "edit ",
        "fix ",
        "arregla",
        "update ",
        "actualiza",
        "replace ",
        "cambia",
    )
    if not any(term in lowered for term in edit_terms):
        return False
    return bool(re.search(r"[a-zA-Z0-9_.\-/]+\.[a-zA-Z0-9]{1,10}", text))


def _apply_patch_enforcement_suffix() -> str:
    return (
        "Editing policy reminder: for existing files, you must use apply_patch. "
        "Do not use filesystem action=write to rewrite an existing file. "
        "Use read_file only to gather context and then call apply_patch."
    )

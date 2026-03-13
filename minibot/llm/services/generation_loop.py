from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Sequence

from pydantic import BaseModel
from ratchet_sm import FailAction, ToolCallMissingAction, ValidAction
from ratchet_sm.normalizers.extract_pseudo_tool_call import has_pseudo_tool_call_tag

from minibot.core.memory import MemoryEntry
from minibot.llm.services.compaction import continue_incomplete_response
from minibot.llm.services.debug_logging import log_provider_response
from minibot.llm.services.models import LLMGeneration
from minibot.llm.services.ratchet_support import StructuredOutputValidator
from minibot.llm.services.request_builder import (
    RequestContext,
    build_generate_extra_kwargs,
    build_generate_step_call_kwargs,
    build_messages,
)
from minibot.llm.services.schema_policy import normalize_response_schema, prepare_tool_specs
from minibot.llm.services.structured_output_policy import (
    augment_system_prompt_with_structured_output,
    normalize_structured_output_mode,
    should_send_response_schema,
)
from minibot.llm.services.tool_executor import execute_tool_calls, tool_name_from_call
from minibot.llm.services.tool_loop_guard import (
    MAX_REPEATED_TOOL_ITERATIONS,
    any_tool_call_truncated,
    assistant_message_for_followup,
    tool_iteration_signature,
    tool_loop_fallback_payload,
)
from minibot.llm.services.usage_parser import (
    UsageAccumulator,
    extract_response_id,
    extract_total_tokens,
    extract_usage_from_response,
    should_auto_continue_incomplete,
)
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.shared.utils import humanize_token_count


_TRUNCATED_PATCH = (
    "Your previous response was truncated. "
    "Please resend your complete tool call with all required arguments."
)
_PSEUDO_TOOL_PATCH = "Please use the tool calling interface instead of embedding tool calls in text."


async def generate_with_tools(
    *,
    history: Sequence[MemoryEntry],
    user_message: str,
    user_content: str | list[dict[str, Any]] | None,
    tools: Sequence[ToolBinding] | None,
    tool_context: ToolContext | None,
    response_schema: dict[str, Any] | None,
    local_response_model: type[BaseModel] | None,
    prompt_cache_key: str | None,
    previous_response_id: str | None,
    system_prompt: str,
    model: str,
    request_ctx: RequestContext,
    is_responses_provider: bool,
    max_new_tokens: int,
    max_tool_iterations: int,
    provider_name: str,
    structured_output_mode: str | None,
    logger: logging.Logger,
    complete_with_schema_fallback: Callable[[dict[str, Any]], Awaitable[Any]],
) -> LLMGeneration:
    mode = normalize_structured_output_mode(structured_output_mode)
    effective_system_prompt = system_prompt
    if response_schema and not should_send_response_schema(mode):
        effective_system_prompt = augment_system_prompt_with_structured_output(system_prompt, response_schema)
    messages = build_messages(
        history=history,
        user_message=user_message,
        user_content=user_content,
        system_prompt=effective_system_prompt,
    )
    conversation = list(messages)
    tool_bindings = list(tools or [])
    tool_specs = prepare_tool_specs(tool_bindings, model)
    context = tool_context or ToolContext()
    iterations = 0
    last_tool_messages: list[dict[str, Any]] = []
    recent_tool_names: list[str] = []
    last_iteration_signature: str | None = None
    repeated_iteration_count = 0
    strict_response_schema = (
        normalize_response_schema(response_schema, model)
        if response_schema and should_send_response_schema(mode)
        else None
    )
    extra_kwargs = build_generate_extra_kwargs(
        ctx=request_ctx,
        prompt_cache_key=prompt_cache_key,
        previous_response_id=previous_response_id,
        system_prompt=effective_system_prompt,
    )
    usage_accumulator = UsageAccumulator()
    truncated_count = 0
    structured_validator = (
        StructuredOutputValidator(
            max_attempts=max_tool_iterations,
            schema=local_response_model or response_schema,
        )
        if response_schema
        else None
    )

    while True:
        call_kwargs = build_generate_step_call_kwargs(
            ctx=request_ctx,
            conversation=conversation,
            tool_specs=tool_specs,
            strict_response_schema=strict_response_schema,
            extra_kwargs=extra_kwargs,
        )
        if response_schema:
            call_kwargs["_structured_output_prompt_schema"] = response_schema
        response = await complete_with_schema_fallback(call_kwargs)
        log_provider_response(
            logger=logger,
            response=response,
            context="generate_with_tools",
            provider_name=provider_name,
        )
        usage = extract_usage_from_response(response)
        usage_tokens = extract_total_tokens(response)
        usage_accumulator.add_step(usage, usage_tokens)

        message = response.main_response
        if not message:
            raise RuntimeError("LLM did not return a completion")
        logger.debug(
            "llm completion received",
            extra={
                "tool_calls": len(getattr(message, "tool_calls", None) or []),
                "step_tokens": humanize_token_count(usage_tokens) if isinstance(usage_tokens, int) else "0",
                "cumulative_tokens": humanize_token_count(usage_accumulator.total_tokens_used),
                "provider": provider_name,
                "response_status": usage.status,
                "incomplete_reason": usage.incomplete_reason,
                "applied_max_output_tokens": max_new_tokens if is_responses_provider else None,
            },
        )
        message_tool_calls = list(message.tool_calls or [])
        effective_tool_calls = message_tool_calls
        if tool_bindings:
            raw_content = message.content if isinstance(message.content, str) else ""
            if message_tool_calls and any_tool_call_truncated(message_tool_calls):
                truncated_count += 1
                if truncated_count >= max_tool_iterations:
                    logger.warning(
                        "truncated tool call exceeded maximum attempts; returning fallback",
                        extra={"tool_names": recent_tool_names[-10:]},
                    )
                    response_id = extract_response_id(response)
                    attempted_tool_names = [
                        *recent_tool_names,
                        *(tool_name_from_call(call) for call in message_tool_calls),
                    ]
                    payload = tool_loop_fallback_payload(last_tool_messages, attempted_tool_names, response_schema)
                    return LLMGeneration(
                        payload,
                        response_id,
                        total_tokens=usage_accumulator.total_tokens_used or None,
                    )
                conversation.append(assistant_message_for_followup(message))
                conversation.append({"role": "user", "content": _TRUNCATED_PATCH})
                continue
            if not message_tool_calls and has_pseudo_tool_call_tag(raw_content):
                conversation.append(assistant_message_for_followup(message))
                conversation.append({"role": "user", "content": _PSEUDO_TOOL_PATCH})
                continue
        if not effective_tool_calls or not tool_bindings:
            payload = message.content
            response_id = extract_response_id(response)
            status = usage.status
            incomplete_reason = usage.incomplete_reason
            if is_responses_provider and response_id and should_auto_continue_incomplete(usage):
                continuation = await continue_incomplete_response(
                    complete_with_schema_fallback=complete_with_schema_fallback,
                    ctx=request_ctx,
                    previous_response_id=response_id,
                    prompt_cache_key=prompt_cache_key,
                    system_prompt=effective_system_prompt,
                    response_schema=strict_response_schema,
                    prompt_response_schema=response_schema,
                    logger=logger,
                )
                continuation_payload = (
                    continuation.payload if isinstance(continuation.payload, str) else str(continuation.payload)
                )
                payload = f"{payload}{continuation_payload}"
                usage_accumulator.add_generation(continuation)
                response_id = continuation.response_id or response_id
                status = continuation.status
                incomplete_reason = continuation.incomplete_reason
            if response_schema and structured_validator is not None:
                action = structured_validator.receive(payload)
                if isinstance(action, ValidAction):
                    parsed_payload = structured_validator.valid_payload(action)
                    return usage_accumulator.build_generation(
                        payload=parsed_payload,
                        response_id=response_id,
                        status=status,
                        incomplete_reason=incomplete_reason,
                    )
                if isinstance(action, ToolCallMissingAction):
                    logger.warning("unexpected structured validator action", extra={"action": type(action).__name__})
                if isinstance(action, FailAction):
                    logger.warning("structured response validation failed; returning raw fallback")
                    fallback_payload = payload if isinstance(payload, dict) else {"raw_response": payload}
                    return usage_accumulator.build_generation(
                        payload=fallback_payload,
                        response_id=response_id,
                        status=status,
                        incomplete_reason=incomplete_reason,
                    )
                conversation.append(assistant_message_for_followup(message))
                if action.prompt_patch:
                    conversation.append({"role": "user", "content": action.prompt_patch})
                continue
            return usage_accumulator.build_generation(
                payload=payload,
                response_id=response_id,
                status=status,
                incomplete_reason=incomplete_reason,
            )

        tool_messages = await execute_tool_calls(
            effective_tool_calls,
            tool_bindings,
            context,
            responses_mode=is_responses_provider,
            logger=logger,
        )
        iteration_signature = tool_iteration_signature(effective_tool_calls, tool_messages)
        if iteration_signature and iteration_signature == last_iteration_signature:
            repeated_iteration_count += 1
        else:
            repeated_iteration_count = 1
        last_iteration_signature = iteration_signature
        last_tool_messages = tool_messages
        recent_tool_names.extend(tool_name_from_call(call) for call in effective_tool_calls)
        if repeated_iteration_count >= MAX_REPEATED_TOOL_ITERATIONS:
            logger.warning(
                "tool loop repeated identical outputs; returning fallback",
                extra={
                    "tool_names": recent_tool_names[-10:],
                    "repeated_count": repeated_iteration_count,
                },
            )
            response_id = extract_response_id(response)
            payload = tool_loop_fallback_payload(last_tool_messages, recent_tool_names, response_schema)
            return LLMGeneration(
                payload,
                response_id,
                total_tokens=usage_accumulator.total_tokens_used or None,
            )
        if is_responses_provider:
            response_id = extract_response_id(response)
            if response_id:
                extra_kwargs["previous_response_id"] = response_id
            conversation = tool_messages
        else:
            conversation.append(assistant_message_for_followup(message))
            conversation.extend(tool_messages)
        iterations += 1
        if iterations >= max_tool_iterations:
            logger.warning(
                "tool call loop exceeded maximum iterations; returning fallback",
                extra={"tool_names": recent_tool_names[-10:]},
            )
            response_id = extract_response_id(response)
            payload = tool_loop_fallback_payload(last_tool_messages, recent_tool_names, response_schema)
            return LLMGeneration(
                payload,
                response_id,
                total_tokens=usage_accumulator.total_tokens_used or None,
            )

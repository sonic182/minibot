from __future__ import annotations

import logging
import json
from typing import Any, Awaitable, Callable, Sequence

from ratchet_sm import FailAction, ToolCallMissingAction, ValidAction

from minibot.core.memory import MemoryEntry
from minibot.llm.services.continue_loop import CONTINUE_LOOP_RETRY_PATCH, should_continue_tool_loop
from minibot.llm.services.compaction import continue_incomplete_response
from minibot.llm.services.debug_logging import log_provider_response
from minibot.llm.services.models import LLMGeneration
from minibot.llm.services.ratchet_support import (
    StructuredOutputValidator,
    build_tool_call_recovery_machine,
    recovered_tool_call_from_payload,
)
from minibot.llm.services.request_builder import (
    RequestContext,
    build_generate_extra_kwargs,
    build_generate_step_call_kwargs,
    build_messages,
)
from minibot.llm.services.schema_policy import normalize_response_schema, prepare_tool_specs
from minibot.llm.services.tool_executor import execute_tool_calls, tool_name_from_call
from minibot.llm.services.tool_loop_guard import (
    MAX_REPEATED_TOOL_ITERATIONS,
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


async def generate_with_tools(
    *,
    history: Sequence[MemoryEntry],
    user_message: str,
    user_content: str | list[dict[str, Any]] | None,
    tools: Sequence[ToolBinding] | None,
    tool_context: ToolContext | None,
    response_schema: dict[str, Any] | None,
    prompt_cache_key: str | None,
    previous_response_id: str | None,
    system_prompt: str,
    model: str,
    request_ctx: RequestContext,
    is_responses_provider: bool,
    max_new_tokens: int,
    max_tool_iterations: int,
    provider_name: str,
    logger: logging.Logger,
    complete_with_schema_fallback: Callable[[dict[str, Any]], Awaitable[Any]],
) -> LLMGeneration:
    messages = build_messages(
        history=history,
        user_message=user_message,
        user_content=user_content,
        system_prompt=system_prompt,
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
    strict_response_schema = normalize_response_schema(response_schema, model)
    extra_kwargs = build_generate_extra_kwargs(
        ctx=request_ctx,
        prompt_cache_key=prompt_cache_key,
        previous_response_id=previous_response_id,
        system_prompt=system_prompt,
    )
    usage_accumulator = UsageAccumulator()
    repeated_continue_loop_count = 0
    last_continue_loop_signature: str | None = None
    tool_recovery_machine = (
        build_tool_call_recovery_machine(max_attempts=max_tool_iterations) if tool_bindings else None
    )
    structured_validator = (
        StructuredOutputValidator(max_attempts=max_tool_iterations, schema=response_schema)
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
        if tool_bindings and tool_recovery_machine is not None:
            raw_message_content = message.content if isinstance(message.content, str) else ""
            tool_action = tool_recovery_machine.receive(raw_message_content, tool_calls=message_tool_calls)
            if isinstance(tool_action, ValidAction):
                if tool_action.format_detected == "native_tool_call":
                    effective_tool_calls = message_tool_calls
                else:
                    effective_tool_calls = [recovered_tool_call_from_payload(tool_action.parsed)]
                tool_recovery_machine.reset()
            elif isinstance(tool_action, ToolCallMissingAction):
                if tool_action.reason == "pseudo_tool_call_in_text":
                    conversation.append(assistant_message_for_followup(message))
                    if tool_action.prompt_patch:
                        conversation.append({"role": "user", "content": tool_action.prompt_patch})
                    continue
                tool_recovery_machine.reset()
                effective_tool_calls = []
            elif isinstance(tool_action, FailAction):
                logger.warning(
                    "tool call recovery exceeded maximum attempts; returning fallback",
                    extra={"tool_names": recent_tool_names[-10:]},
                )
                response_id = extract_response_id(response)
                payload = tool_loop_fallback_payload(last_tool_messages, recent_tool_names, response_schema)
                return LLMGeneration(
                    payload,
                    response_id,
                    total_tokens=usage_accumulator.total_tokens_used or None,
                )
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
                    system_prompt=system_prompt,
                    response_schema=strict_response_schema,
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
                    if tool_bindings and should_continue_tool_loop(parsed_payload):
                        continue_signature = json.dumps(
                            parsed_payload,
                            sort_keys=True,
                            ensure_ascii=True,
                            separators=(",", ":"),
                            default=str,
                        )
                        if continue_signature == last_continue_loop_signature:
                            repeated_continue_loop_count += 1
                        else:
                            repeated_continue_loop_count = 1
                        last_continue_loop_signature = continue_signature
                        structured_validator.reset()
                        if repeated_continue_loop_count >= 2:
                            logger.warning("repeated identical continue_loop payload; returning fallback")
                            return usage_accumulator.build_generation(
                                payload=tool_loop_fallback_payload(
                                    last_tool_messages,
                                    [*recent_tool_names, "continue_loop"],
                                    response_schema,
                                ),
                                response_id=response_id,
                                status=status,
                                incomplete_reason=incomplete_reason,
                            )
                        conversation.append(assistant_message_for_followup(message))
                        conversation.append({"role": "user", "content": CONTINUE_LOOP_RETRY_PATCH})
                        continue
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

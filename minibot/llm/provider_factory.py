from __future__ import annotations

import logging
from typing import Any, Sequence

from llm_async.models.tool_call import ToolCall

from minibot.adapters.config.schema import LLMMConfig
from minibot.core.memory import MemoryEntry
from minibot.llm.services.client_bootstrap import (
    build_openrouter_provider_payload,
    create_provider,
    load_system_prompt,
    resolve_openrouter_reasoning_enabled,
)
from minibot.llm.services.compaction import compact_response as compact_response_via_service
from minibot.llm.services.compaction import continue_incomplete_response
from minibot.llm.services.models import LLMCompaction, LLMCompletionStep, LLMGeneration, ToolExecutionRecord
from minibot.llm.services.provider_registry import is_responses_provider_instance
from minibot.llm.services.request_builder import (
    RequestContext,
    build_complete_once_call_kwargs,
    build_generate_extra_kwargs,
    build_generate_step_call_kwargs,
    build_messages,
)
from minibot.llm.services.schema_fallback import complete_with_schema_fallback
from minibot.llm.services.schema_policy import normalize_response_schema, prepare_tool_specs
from minibot.llm.services.tool_executor import execute_tool_calls, execute_tool_calls_for_runtime, tool_name_from_call
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
    parse_structured_payload,
    should_auto_continue_incomplete,
)
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.shared.retries import AsyncRetriesService
from minibot.shared.utils import humanize_token_count


class LLMClient:
    def __init__(self, config: LLMMConfig) -> None:
        self._provider, self._provider_name = create_provider(config)
        self._model = config.model
        self._temperature = config.temperature
        self._max_new_tokens = config.max_new_tokens
        self._max_tool_iterations = config.max_tool_iterations
        self._system_prompt = load_system_prompt(config)
        self._prompts_dir = getattr(config, "prompts_dir", "./prompts")
        self._reasoning_effort = getattr(config, "reasoning_effort", "medium")
        self._responses_state_mode = getattr(config, "responses_state_mode", "full_messages")
        self._prompt_cache_enabled = bool(getattr(config, "prompt_cache_enabled", True))
        self._prompt_cache_retention = getattr(config, "prompt_cache_retention", None)
        self._compaction_retry_attempts = 3
        self._compaction_retry_base_delay_seconds = float(config.retry_delay_seconds)
        self._compaction_retry_max_delay_seconds = min(self._compaction_retry_base_delay_seconds * 4, 10.0)
        self._retries_service = AsyncRetriesService()
        self._openrouter_models = tuple(getattr(getattr(config, "openrouter", None), "models", []) or [])
        self._openrouter_provider = build_openrouter_provider_payload(config)
        self._openrouter_reasoning_enabled = resolve_openrouter_reasoning_enabled(config)
        self._openrouter_plugins = tuple(getattr(getattr(config, "openrouter", None), "plugins", []) or [])
        self._is_responses_provider = is_responses_provider_instance(self._provider)
        self._logger = logging.getLogger("minibot.llm")

    async def generate(
        self,
        history: Sequence[MemoryEntry],
        user_message: str,
        user_content: str | list[dict[str, Any]] | None = None,
        tools: Sequence[ToolBinding] | None = None,
        tool_context: ToolContext | None = None,
        response_schema: dict[str, Any] | None = None,
        prompt_cache_key: str | None = None,
        previous_response_id: str | None = None,
        system_prompt_override: str | None = None,
    ) -> LLMGeneration:
        system_prompt = system_prompt_override or self._system_prompt
        messages = build_messages(
            history=history,
            user_message=user_message,
            user_content=user_content,
            system_prompt=system_prompt,
        )

        if not self._provider.api_key:
            self._logger.warning("LLM provider key missing, falling back to echo", extra={"component": "llm"})
            return LLMGeneration(f"Echo: {user_message}")

        conversation = list(messages)
        tool_bindings = list(tools or [])
        tool_specs = prepare_tool_specs(tool_bindings, self._model)
        context = tool_context or ToolContext()
        iterations = 0
        last_tool_messages: list[dict[str, Any]] = []
        recent_tool_names: list[str] = []
        last_iteration_signature: str | None = None
        repeated_iteration_count = 0
        strict_response_schema = normalize_response_schema(response_schema, self._model)
        request_ctx = self._request_context()
        extra_kwargs = build_generate_extra_kwargs(
            ctx=request_ctx,
            prompt_cache_key=prompt_cache_key,
            previous_response_id=previous_response_id,
            system_prompt=system_prompt,
        )
        usage_accumulator = UsageAccumulator()

        while True:
            call_kwargs = build_generate_step_call_kwargs(
                ctx=request_ctx,
                conversation=conversation,
                tool_specs=tool_specs,
                strict_response_schema=strict_response_schema,
                extra_kwargs=extra_kwargs,
            )
            response = await self._complete_with_schema_fallback(call_kwargs)
            usage = extract_usage_from_response(response)
            usage_tokens = extract_total_tokens(response)
            usage_accumulator.add_step(usage, usage_tokens)

            message = response.main_response
            if not message:
                raise RuntimeError("LLM did not return a completion")
            self._logger.debug(
                "llm completion received",
                extra={
                    "tool_calls": len(getattr(message, "tool_calls", None) or []),
                    "step_tokens": humanize_token_count(usage_tokens) if isinstance(usage_tokens, int) else "0",
                    "cumulative_tokens": humanize_token_count(usage_accumulator.total_tokens_used),
                    "provider": self.provider_name(),
                    "response_status": usage.status,
                    "incomplete_reason": usage.incomplete_reason,
                    "applied_max_output_tokens": self._max_new_tokens if self._is_responses_provider else None,
                },
            )
            if not message.tool_calls or not tool_bindings:
                payload = message.content
                response_id = extract_response_id(response)
                status = usage.status
                incomplete_reason = usage.incomplete_reason
                if self._is_responses_provider and response_id and should_auto_continue_incomplete(usage):
                    continuation = await continue_incomplete_response(
                        complete_with_schema_fallback=self._complete_with_schema_fallback,
                        ctx=request_ctx,
                        previous_response_id=response_id,
                        prompt_cache_key=prompt_cache_key,
                        system_prompt=system_prompt,
                        response_schema=strict_response_schema,
                        logger=self._logger,
                    )
                    continuation_payload = (
                        continuation.payload if isinstance(continuation.payload, str) else str(continuation.payload)
                    )
                    payload = f"{payload}{continuation_payload}"
                    usage_accumulator.add_generation(continuation)
                    response_id = continuation.response_id or response_id
                    status = continuation.status
                    incomplete_reason = continuation.incomplete_reason
                if response_schema and isinstance(payload, str):
                    try:
                        parsed = parse_structured_payload(payload)
                        return usage_accumulator.build_generation(
                            payload=parsed,
                            response_id=response_id,
                            status=status,
                            incomplete_reason=incomplete_reason,
                        )
                    except Exception:
                        self._logger.warning("failed to parse structured response; falling back to text")
                return usage_accumulator.build_generation(
                    payload=payload,
                    response_id=response_id,
                    status=status,
                    incomplete_reason=incomplete_reason,
                )

            tool_messages = await execute_tool_calls(
                message.tool_calls,
                tool_bindings,
                context,
                responses_mode=self._is_responses_provider,
                logger=self._logger,
            )
            iteration_signature = tool_iteration_signature(message.tool_calls, tool_messages)
            if iteration_signature and iteration_signature == last_iteration_signature:
                repeated_iteration_count += 1
            else:
                repeated_iteration_count = 1
            last_iteration_signature = iteration_signature
            last_tool_messages = tool_messages
            recent_tool_names.extend(tool_name_from_call(call) for call in message.tool_calls)
            if repeated_iteration_count >= MAX_REPEATED_TOOL_ITERATIONS:
                self._logger.warning(
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
            if self._is_responses_provider:
                response_id = extract_response_id(response)
                if response_id:
                    extra_kwargs["previous_response_id"] = response_id
                conversation = tool_messages
            else:
                conversation.append(assistant_message_for_followup(message))
                conversation.extend(tool_messages)
            iterations += 1
            if iterations >= self._max_tool_iterations:
                self._logger.warning(
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

    def is_responses_provider(self) -> bool:
        return self._is_responses_provider

    def responses_state_mode(self) -> str:
        if self._responses_state_mode in {"full_messages", "previous_response_id"}:
            return self._responses_state_mode
        return "full_messages"

    def prompt_cache_enabled(self) -> bool:
        return self._prompt_cache_enabled

    async def compact_response(
        self,
        *,
        previous_response_id: str,
        prompt_cache_key: str | None = None,
    ) -> LLMCompaction:
        return await compact_response_via_service(
            provider=self._provider,
            is_responses_provider=self._is_responses_provider,
            model=self._model,
            prompt_cache_enabled=self._prompt_cache_enabled,
            previous_response_id=previous_response_id,
            prompt_cache_key=prompt_cache_key,
            retries_service=self._retries_service,
            retry_attempts=self._compaction_retry_attempts,
            retry_base_delay_seconds=self._compaction_retry_base_delay_seconds,
            retry_max_delay_seconds=self._compaction_retry_max_delay_seconds,
            logger=self._logger,
        )

    async def complete_once(
        self,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[ToolBinding] | None = None,
        response_schema: dict[str, Any] | None = None,
        prompt_cache_key: str | None = None,
        previous_response_id: str | None = None,
    ) -> LLMCompletionStep:
        strict_response_schema = normalize_response_schema(response_schema, self._model)
        tool_specs = prepare_tool_specs(tools or [], self._model)
        request_ctx = self._request_context()
        call_kwargs = build_complete_once_call_kwargs(
            ctx=request_ctx,
            messages=messages,
            tool_specs=tool_specs,
            strict_response_schema=strict_response_schema,
            prompt_cache_key=prompt_cache_key,
            previous_response_id=previous_response_id,
        )

        response = await self._complete_with_schema_fallback(call_kwargs)
        message = response.main_response
        if not message:
            raise RuntimeError("LLM did not return a completion")
        usage_tokens = extract_total_tokens(response)
        self._logger.debug(
            "llm runtime completion step received",
            extra={
                "tool_calls": len(getattr(message, "tool_calls", None) or []),
                "step_tokens": humanize_token_count(usage_tokens) if isinstance(usage_tokens, int) else "0",
                "provider": self.provider_name(),
            },
        )
        return LLMCompletionStep(
            message=message,
            response_id=extract_response_id(response),
            total_tokens=usage_tokens,
        )

    async def execute_tool_calls_for_runtime(
        self,
        tool_calls: Sequence[ToolCall],
        tools: Sequence[ToolBinding],
        context: ToolContext,
        responses_mode: bool = False,
    ) -> list[ToolExecutionRecord]:
        return await execute_tool_calls_for_runtime(
            tool_calls,
            tools,
            context,
            responses_mode=responses_mode,
            logger=self._logger,
        )

    def provider_name(self) -> str:
        return self._provider_name

    def model_name(self) -> str:
        return self._model

    def system_prompt(self) -> str:
        return self._system_prompt

    def prompts_dir(self) -> str:
        return self._prompts_dir

    def max_tool_iterations(self) -> int:
        return self._max_tool_iterations

    def supports_media_inputs(self) -> bool:
        return self._provider_name in {"openai_responses", "openai", "openrouter"}

    def media_input_mode(self) -> str:
        if self._provider_name == "openai_responses":
            return "responses"
        if self._provider_name in {"openai", "openrouter"}:
            return "chat_completions"
        return "none"

    async def _complete_with_schema_fallback(self, call_kwargs: dict[str, Any]) -> Any:
        return await complete_with_schema_fallback(
            provider=self._provider,
            call_kwargs=call_kwargs,
            provider_name=self._provider_name,
            model=self._model,
            provider_display_name=self.provider_name(),
            logger=self._logger,
        )

    def _request_context(self) -> RequestContext:
        return RequestContext(
            model=self._model,
            provider_name=self._provider_name,
            is_responses_provider=self._is_responses_provider,
            temperature=self._temperature,
            max_new_tokens=self._max_new_tokens,
            prompt_cache_enabled=self._prompt_cache_enabled,
            prompt_cache_retention=self._prompt_cache_retention,
            reasoning_effort=self._reasoning_effort,
            openrouter_models=self._openrouter_models,
            openrouter_provider=self._openrouter_provider,
            openrouter_reasoning_enabled=self._openrouter_reasoning_enabled,
            openrouter_plugins=self._openrouter_plugins,
        )

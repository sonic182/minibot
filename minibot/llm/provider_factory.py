from __future__ import annotations

import logging
from typing import Any, Sequence

from llm_async.models.tool_call import ToolCall
from pydantic import BaseModel

from minibot.adapters.config.schema import LLMMConfig
from minibot.core.memory import MemoryEntry
from minibot.llm.services.client_bootstrap import (
    build_openrouter_provider_payload,
    create_provider,
    load_system_prompt,
    resolve_openrouter_reasoning_enabled,
)
from minibot.llm.services.provider_capabilities import build_provider_capability_hints, build_provider_native_tools
from minibot.llm.services.compaction import compact_response as compact_response_via_service
from minibot.llm.services.debug_logging import log_provider_response
from minibot.llm.services.generation_loop import generate_with_tools
from minibot.llm.services.models import (
    LLMCompaction,
    LLMCompletionStep,
    LLMExecutionProfile,
    LLMGeneration,
    ToolExecutionRecord,
)
from minibot.llm.services.provider_registry import is_responses_provider_instance
from minibot.llm.services.request_builder import (
    RequestContext,
    build_complete_once_call_kwargs,
)
from minibot.llm.services.schema_fallback import complete_with_schema_fallback
from minibot.llm.services.schema_policy import normalize_response_schema, prepare_tool_specs
from minibot.llm.services.structured_output_policy import (
    apply_structured_output_prompt,
    normalize_structured_output_mode,
    should_send_response_schema,
)
from minibot.llm.services.tool_executor import execute_tool_calls_for_runtime
from minibot.llm.services.usage_parser import (
    extract_response_id,
    extract_total_tokens,
    extract_usage_from_response,
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
        self._structured_output_mode = normalize_structured_output_mode(
            getattr(config, "structured_output_mode", "provider_with_fallback")
        )
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
        self._provider_native_tools = build_provider_native_tools(config)
        self._provider_capability_hints = build_provider_capability_hints(config)
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
        local_response_model: type[BaseModel] | None = None,
        prompt_cache_key: str | None = None,
        previous_response_id: str | None = None,
        system_prompt_override: str | None = None,
    ) -> LLMGeneration:
        system_prompt = system_prompt_override or self._system_prompt
        if not self._provider.api_key:
            self._logger.warning("LLM provider key missing, falling back to echo", extra={"component": "llm"})
            return LLMGeneration(f"Echo: {user_message}")

        return await generate_with_tools(
            history=history,
            user_message=user_message,
            user_content=user_content,
            system_prompt=system_prompt,
            tools=tools,
            tool_context=tool_context,
            response_schema=response_schema,
            local_response_model=local_response_model,
            prompt_cache_key=prompt_cache_key,
            previous_response_id=previous_response_id,
            model=self._model,
            request_ctx=self._request_context(),
            is_responses_provider=self._is_responses_provider,
            max_new_tokens=self._max_new_tokens,
            max_tool_iterations=self._max_tool_iterations,
            provider_name=self.provider_name(),
            structured_output_mode=self._structured_output_mode,
            logger=self._logger,
            complete_with_schema_fallback=self._complete_with_schema_fallback,
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
        strict_response_schema = (
            normalize_response_schema(response_schema, self._model)
            if response_schema and should_send_response_schema(self._structured_output_mode)
            else None
        )
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
        if response_schema:
            call_kwargs["_structured_output_prompt_schema"] = response_schema
        if response_schema and not should_send_response_schema(self._structured_output_mode):
            call_kwargs = apply_structured_output_prompt(call_kwargs, response_schema)

        response = await self._complete_with_schema_fallback(call_kwargs)
        # Keep raw provider response logging centralized here so every runtime completion step
        # emits the same debug payload for response content and tool calls.
        log_provider_response(
            logger=self._logger,
            response=response,
            context="complete_once",
            provider_name=self.provider_name(),
        )
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
            provider_tool_calls=extract_usage_from_response(response).provider_tool_calls,
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

    def features(self) -> LLMExecutionProfile:
        return LLMExecutionProfile(
            provider_name=self.provider_name(),
            model_name=self.model_name(),
            system_prompt=self.system_prompt(),
            prompts_dir=self.prompts_dir(),
            responses_state_mode=self.responses_state_mode(),
            prompt_cache_enabled=self.prompt_cache_enabled(),
            media_input_mode=self.media_input_mode(),
            supports_media_inputs=self.supports_media_inputs(),
            supports_agent_runtime=True,
            is_responses_provider=self.is_responses_provider(),
            provider_capability_hints=self.provider_capability_hints(),
        )

    def provider_capability_hints(self) -> list[str]:
        return list(self._provider_capability_hints)

    async def _complete_with_schema_fallback(self, call_kwargs: dict[str, Any]) -> Any:
        return await complete_with_schema_fallback(
            provider=self._provider,
            call_kwargs=call_kwargs,
            model=self._model,
            provider_display_name=self.provider_name(),
            logger=self._logger,
            structured_output_mode=self._structured_output_mode,
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
            provider_native_tools=self._provider_native_tools,
        )

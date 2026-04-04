from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from minibot.core.agent_runtime import ToolResult


@dataclass
class LLMGeneration:
    payload: Any
    response_id: str | None = None
    total_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    provider_tool_calls: int | None = None
    status: str | None = None
    incomplete_reason: str | None = None


@dataclass
class LLMCompletionStep:
    message: Any
    response_id: str | None
    total_tokens: int | None = None
    provider_tool_calls: int | None = None


@dataclass
class ToolExecutionRecord:
    tool_name: str
    call_id: str
    message_payload: dict[str, Any]
    result: ToolResult


@dataclass
class LLMCompaction:
    response_id: str
    output: list[dict[str, Any]]
    total_tokens: int | None = None


@dataclass(frozen=True)
class UsageSnapshot:
    total_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    provider_tool_calls: int | None = None
    status: str | None = None
    incomplete_reason: str | None = None


@dataclass(frozen=True)
class LLMExecutionProfile:
    provider_name: str | None = None
    model_name: str | None = None
    system_prompt: str = "You are Minibot, a helpful assistant."
    prompts_dir: str = "./prompts"
    responses_state_mode: str = "full_messages"
    prompt_cache_enabled: bool = True
    media_input_mode: str = "none"
    supports_media_inputs: bool = False
    supports_agent_runtime: bool = False
    is_responses_provider: bool = False
    provider_capability_hints: tuple[str, ...] = ()

    @classmethod
    def from_client(cls, client: Any) -> "LLMExecutionProfile":
        profile_getter = getattr(client, "features", None)
        if callable(profile_getter):
            maybe_profile = profile_getter()
            if isinstance(maybe_profile, cls):
                return maybe_profile

        provider_name = cls._call_str(client, "provider_name")
        model_name = cls._call_str(client, "model_name")
        system_prompt = cls._call_str(client, "system_prompt") or "You are Minibot, a helpful assistant."
        prompts_dir = cls._call_str(client, "prompts_dir") or "./prompts"
        responses_state_mode = cls._call_str(client, "responses_state_mode")
        if responses_state_mode not in {"full_messages", "previous_response_id"}:
            responses_state_mode = "full_messages"
        prompt_cache_enabled = cls._call_bool(client, "prompt_cache_enabled", default=True)
        media_input_mode = cls._call_str(client, "media_input_mode")
        if media_input_mode not in {"responses", "chat_completions", "none"}:
            media_input_mode = "none"
        supports_media_inputs = cls._call_bool(
            client,
            "supports_media_inputs",
            default=media_input_mode in {"responses", "chat_completions"},
        )
        is_responses_provider = cls._call_bool(client, "is_responses_provider", default=False)
        provider_capability_hints = cls._call_strs(client, "provider_capability_hints")
        supports_agent_runtime = callable(getattr(client, "complete_once", None)) and callable(
            getattr(client, "execute_tool_calls_for_runtime", None)
        )
        return cls(
            provider_name=provider_name,
            model_name=model_name,
            system_prompt=system_prompt,
            prompts_dir=prompts_dir,
            responses_state_mode=responses_state_mode,
            prompt_cache_enabled=prompt_cache_enabled,
            media_input_mode=media_input_mode,
            supports_media_inputs=supports_media_inputs,
            supports_agent_runtime=supports_agent_runtime,
            is_responses_provider=is_responses_provider,
            provider_capability_hints=provider_capability_hints,
        )

    @staticmethod
    def _call_str(client: Any, name: str) -> str | None:
        getter = getattr(client, name, None)
        if callable(getter):
            value = getter()
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _call_bool(client: Any, name: str, *, default: bool) -> bool:
        getter = getattr(client, name, None)
        if callable(getter):
            return bool(getter())
        return default

    @staticmethod
    def _call_strs(client: Any, name: str) -> tuple[str, ...]:
        getter = getattr(client, name, None)
        if not callable(getter):
            return ()
        value = getter()
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(item for item in value if isinstance(item, str) and item.strip())

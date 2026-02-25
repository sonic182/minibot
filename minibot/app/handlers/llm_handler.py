from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Sequence

from minibot.app.agent_runtime import AgentRuntime
from minibot.app.incoming_files_context import (
    build_history_user_entry,
)
from minibot.app.handlers.services import (
    AgentRuntimeResult,
    CompactionResult,
    HistoryCompactionService,
    PromptService,
    ResponseMetadataService,
    RuntimeOrchestrationService,
    SessionStateService,
    UserInputService,
)
from minibot.app.runtime_limits import build_runtime_limits
from minibot.app.response_parser import extract_answer, plain_render
from minibot.app.tool_use_guardrail import NoopToolUseGuardrail, ToolUseGuardrail
from minibot.core.channels import ChannelMessage, ChannelResponse
from minibot.core.events import MessageEvent
from minibot.core.memory import MemoryBackend
from minibot.llm.provider_factory import LLMClient
from minibot.shared.assistant_response import assistant_response_schema
from minibot.shared.utils import session_id_for, session_id_from_parts
from minibot.llm.tools.base import ToolBinding, ToolContext


@dataclass(frozen=True)
class _CompactionResult(CompactionResult):
    updates: list[str]
    performed: bool
    tokens_used: int
    session_total_tokens_before_compaction: int | None
    session_total_tokens_after_compaction: int


@dataclass(frozen=True)
class _AgentRuntimeResult(AgentRuntimeResult):
    render: Any
    should_reply: bool
    response_id: str | None
    agent_trace: list[dict[str, Any]]
    delegation_fallback_used: bool
    tokens_used: int


class LLMMessageHandler:
    _COMPACTION_USER_REQUEST = "Please compact the current conversation memory."

    def __init__(
        self,
        memory: MemoryBackend,
        llm_client: LLMClient,
        tools: Sequence[ToolBinding] | None = None,
        default_owner_id: str | None = None,
        max_history_messages: int | None = None,
        max_history_tokens: int | None = None,
        notify_compaction_updates: bool = False,
        agent_timeout_seconds: int = 120,
        environment_prompt_fragment: str = "",
        tool_use_guardrail: ToolUseGuardrail | None = None,
        managed_files_root: str | None = None,
    ) -> None:
        self._memory = memory
        self._llm_client = llm_client
        self._tools = list(tools or [])
        self._default_owner_id = default_owner_id
        self._max_history_messages = max_history_messages
        self._max_history_tokens = max_history_tokens
        self._notify_compaction_updates = notify_compaction_updates
        self._tool_use_guardrail: ToolUseGuardrail = tool_use_guardrail or NoopToolUseGuardrail()
        self._logger = logging.getLogger("minibot.handler")
        self._session_state = SessionStateService()
        self._session_total_tokens = self._session_state.session_total_tokens
        self._session_previous_response_ids = self._session_state.session_previous_response_ids
        self._metadata_service = ResponseMetadataService(llm_client=self._llm_client)
        self._input_service = UserInputService(llm_client=self._llm_client)
        self._prompt_service = PromptService(
            llm_client=self._llm_client,
            tools=self._tools,
            environment_prompt_fragment=environment_prompt_fragment,
            logger=self._logger,
        )
        self._prompts_dir = self._prompt_service.prompts_dir
        runtime_limits = build_runtime_limits(
            llm_client=self._llm_client,
            timeout_seconds=agent_timeout_seconds,
            min_timeout_seconds=120,
        )
        if self._supports_agent_runtime():
            self._runtime = AgentRuntime(
                llm_client=self._llm_client,
                tools=self._tools,
                limits=runtime_limits,
                allowed_append_message_tools=["self_insert_artifact"],
                allow_system_inserts=False,
                managed_files_root=managed_files_root,
            )
            self._runtime_service = RuntimeOrchestrationService(
                runtime=self._runtime,
                llm_client=self._llm_client,
                guardrail=self._tool_use_guardrail,
                session_state=self._session_state,
                logger=self._logger,
            )
        else:
            self._runtime = None
            self._runtime_service = None
        self._compaction_service = HistoryCompactionService(
            memory=self._memory,
            llm_client=self._llm_client,
            session_state=self._session_state,
            prompt_service=self._prompt_service,
            logger=self._logger,
            max_history_tokens=self._max_history_tokens,
            compaction_user_request=self._COMPACTION_USER_REQUEST,
        )

    def _supports_agent_runtime(self) -> bool:
        return callable(getattr(self._llm_client, "complete_once", None)) and callable(
            getattr(self._llm_client, "execute_tool_calls_for_runtime", None)
        )

    def _llm_provider_name(self) -> str | None:
        return self._metadata_service.provider_name()

    def _llm_model_name(self) -> str | None:
        return self._metadata_service.model_name()

    def _response_metadata(self, should_reply: bool) -> dict[str, Any]:
        return self._metadata_service.response_metadata(should_reply)

    def _supports_media_inputs(self) -> bool:
        return self._input_service.supports_media_inputs()

    def _media_input_mode(self) -> str:
        return self._input_service.media_input_mode()

    def _llm_prompts_dir(self) -> str:
        return self._prompt_service.prompts_dir

    def _responses_state_mode(self) -> str:
        mode_getter = getattr(self._llm_client, "responses_state_mode", None)
        if callable(mode_getter):
            mode = mode_getter()
            if mode in {"full_messages", "previous_response_id"}:
                return mode
        return "full_messages"

    def _prompt_cache_enabled(self) -> bool:
        enabled_getter = getattr(self._llm_client, "prompt_cache_enabled", None)
        if callable(enabled_getter):
            return bool(enabled_getter())
        return True

    def _compose_system_prompt(self, channel: str | None) -> str:
        return self._prompt_service.compose_system_prompt(channel)

    async def handle(self, event: MessageEvent) -> ChannelResponse:
        message = event.message
        session_id = session_id_for(message)
        turn_total_tokens = 0
        model_text, model_user_content = self._build_model_user_input(message)
        if message.attachments:
            self._logger.debug(
                "prepared multimodal message",
                extra={
                    "channel": message.channel,
                    "chat_id": message.chat_id,
                    "user_id": message.user_id,
                    "attachment_count": len(message.attachments),
                    "attachment_types": [str(attachment.get("type", "unknown")) for attachment in message.attachments],
                    "responses_provider": self._llm_client.is_responses_provider(),
                    "media_input_mode": self._media_input_mode(),
                },
            )
        await self._memory.append_history(session_id, "user", build_history_user_entry(message, model_text))
        await self._enforce_history_limit(session_id)

        if message.attachments and not self._supports_media_inputs():
            answer = "Media inputs are supported for `openai_responses`, `openai`, and `openrouter`."
            await self._memory.append_history(session_id, "assistant", answer)
            await self._enforce_history_limit(session_id)
            chat_id = message.chat_id or message.user_id or 0
            metadata = self._response_metadata(True)
            metadata["token_trace"] = self._build_token_trace(
                turn_total_tokens=0,
                session_total_tokens_before_compaction=None,
                session_total_tokens_after_compaction=self._current_session_tokens(session_id),
                compaction_performed=False,
            )
            return ChannelResponse(
                channel=message.channel,
                chat_id=chat_id,
                text=answer,
                render=plain_render(answer),
                metadata=metadata,
            )

        history = list(await self._memory.get_history(session_id))
        owner_id = resolve_owner_id(message, self._default_owner_id)
        responses_state_mode = self._responses_state_mode()
        use_previous_response_id = (
            self._llm_client.is_responses_provider() and responses_state_mode == "previous_response_id"
        )
        previous_response_id = (
            self._session_previous_response_ids.get(session_id) if use_previous_response_id else None
        )
        tool_context = ToolContext(
            owner_id=owner_id,
            channel=message.channel,
            chat_id=message.chat_id,
            user_id=message.user_id,
        )
        prompt_cache_key = _prompt_cache_key(message) if self._prompt_cache_enabled() else None
        system_prompt = self._compose_system_prompt(message.channel)
        primary_agent = "minibot"
        agent_trace: list[dict[str, Any]] = []
        delegation_fallback_used = False
        try:
            if self._runtime is None:
                generation = await self._llm_client.generate(
                    history,
                    model_text,
                    user_content=model_user_content,
                    tools=self._tools,
                    tool_context=tool_context,
                    response_schema=self._response_schema(),
                    prompt_cache_key=prompt_cache_key,
                    previous_response_id=previous_response_id,
                    system_prompt_override=system_prompt,
                )
                self._session_state.track_usage(
                    session_id,
                    input_tokens=getattr(generation, "input_tokens", None),
                    output_tokens=getattr(generation, "output_tokens", None),
                    total_tokens=getattr(generation, "total_tokens", None),
                    cached_input_tokens=getattr(generation, "cached_input_tokens", None),
                    reasoning_output_tokens=getattr(generation, "reasoning_output_tokens", None),
                )
                turn_total_tokens += self._track_token_usage(session_id, getattr(generation, "total_tokens", None))
                render, should_reply = extract_answer(generation.payload, logger=self._logger)
                if use_previous_response_id and generation.response_id:
                    self._session_previous_response_ids[session_id] = generation.response_id
            else:
                runtime_result = await self._run_with_agent_runtime(
                    session_id=session_id,
                    history=history,
                    model_text=model_text,
                    model_user_content=model_user_content,
                    system_prompt=system_prompt,
                    tool_context=tool_context,
                    prompt_cache_key=prompt_cache_key,
                    previous_response_id=previous_response_id,
                    chat_id=message.chat_id,
                    channel=message.channel,
                )
                turn_total_tokens += runtime_result.tokens_used
                render = runtime_result.render
                should_reply = runtime_result.should_reply
                agent_trace = runtime_result.agent_trace
                delegation_fallback_used = runtime_result.delegation_fallback_used
                if use_previous_response_id and runtime_result.response_id:
                    self._session_previous_response_ids[session_id] = runtime_result.response_id

            if not use_previous_response_id:
                self._session_previous_response_ids.pop(session_id, None)

            self._logger.debug(
                "structured output parsed",
                extra={"kind": render.kind, "content_length": len(render.text), "should_reply": should_reply},
            )
        except Exception as exc:
            self._logger.exception("LLM call failed", exc_info=exc)
            render = plain_render(self._format_runtime_error_message(exc))
            should_reply = True
        answer = render.text
        await self._memory.append_history(session_id, "assistant", answer)
        await self._enforce_history_limit(session_id)
        compact_prompt_cache_key = prompt_cache_key or f"{session_id}:runtime"
        compaction_result = await self._compact_history_if_needed(
            session_id,
            prompt_cache_key=compact_prompt_cache_key,
            system_prompt=system_prompt,
            notify=self._notify_compaction_updates,
        )
        turn_total_tokens += compaction_result.tokens_used

        chat_id = message.chat_id or message.user_id or 0
        metadata = self._response_metadata(should_reply)
        metadata["primary_agent"] = primary_agent
        if agent_trace:
            metadata["agent_trace"] = agent_trace
        metadata["delegation_fallback_used"] = delegation_fallback_used
        if compaction_result.updates:
            metadata["compaction_updates"] = compaction_result.updates
        metadata["token_trace"] = self._build_token_trace(
            turn_total_tokens=turn_total_tokens,
            session_total_tokens_before_compaction=compaction_result.session_total_tokens_before_compaction,
            session_total_tokens_after_compaction=compaction_result.session_total_tokens_after_compaction,
            compaction_performed=compaction_result.performed,
        )
        usage_trace = self._session_state.latest_usage_trace(session_id)
        if any(value is not None for value in usage_trace.values()):
            metadata["usage_trace"] = usage_trace
        return ChannelResponse(
            channel=message.channel,
            chat_id=chat_id,
            text=answer,
            render=render,
            metadata=metadata,
        )

    async def repair_format_response(
        self,
        *,
        response: ChannelResponse,
        parse_error: str,
        channel: str,
        chat_id: int,
        user_id: int | None,
        attempt: int,
    ) -> ChannelResponse:
        session_id = session_id_from_parts(channel, chat_id, user_id)
        turn_total_tokens = 0
        history = list(await self._memory.get_history(session_id))
        system_prompt = self._compose_system_prompt(channel)
        original_kind = response.render.kind if response.render is not None else "text"
        original_content = response.render.text if response.render is not None else response.text
        repair_prompt = self._build_format_repair_prompt(
            channel=channel,
            original_kind=original_kind,
            parse_error=parse_error,
            original_content=original_content,
        )
        generation = await self._llm_client.generate(
            history,
            repair_prompt,
            user_content=None,
            tools=[],
            tool_context=None,
            response_schema=self._response_schema(),
            prompt_cache_key=f"{channel}:{chat_id}:format-repair",
            previous_response_id=None,
            system_prompt_override=system_prompt,
        )
        turn_total_tokens += self._track_token_usage(session_id, getattr(generation, "total_tokens", None))
        render, _ = extract_answer(generation.payload, logger=self._logger)
        await self._memory.append_history(session_id, "user", repair_prompt)
        await self._memory.append_history(session_id, "assistant", render.text)
        await self._enforce_history_limit(session_id)
        compaction_result = await self._compact_history_if_needed(
            session_id,
            prompt_cache_key=f"{channel}:{chat_id}:format-repair",
            system_prompt=system_prompt,
            notify=False,
        )
        turn_total_tokens += compaction_result.tokens_used
        metadata = self._response_metadata(True)
        metadata["format_repair_attempt"] = attempt
        metadata["format_repair_original_kind"] = original_kind
        metadata["token_trace"] = self._build_token_trace(
            turn_total_tokens=turn_total_tokens,
            session_total_tokens_before_compaction=compaction_result.session_total_tokens_before_compaction,
            session_total_tokens_after_compaction=compaction_result.session_total_tokens_after_compaction,
            compaction_performed=compaction_result.performed,
        )
        return ChannelResponse(
            channel=channel,
            chat_id=chat_id,
            text=render.text,
            render=render,
            metadata=metadata,
        )

    @staticmethod
    def _build_format_repair_prompt(
        *, channel: str, original_kind: str, parse_error: str, original_content: str
    ) -> str:
        return PromptService.build_format_repair_prompt(
            channel=channel,
            original_kind=original_kind,
            parse_error=parse_error,
            original_content=original_content,
        )

    async def _run_with_agent_runtime(
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
    ) -> _AgentRuntimeResult:
        if self._runtime is None:
            raise RuntimeError("agent runtime is not available")
        runtime_service_runtime = getattr(self._runtime_service, "_runtime", None)
        if self._runtime_service is None or runtime_service_runtime is not self._runtime:
            self._runtime_service = RuntimeOrchestrationService(
                runtime=self._runtime,
                llm_client=self._llm_client,
                guardrail=self._tool_use_guardrail,
                session_state=self._session_state,
                logger=self._logger,
            )
        assert self._runtime_service is not None
        result = await self._runtime_service.run_with_agent_runtime(
            session_id=session_id,
            history=history,
            model_text=model_text,
            model_user_content=model_user_content,
            system_prompt=system_prompt,
            tool_context=tool_context,
            prompt_cache_key=prompt_cache_key,
            previous_response_id=previous_response_id,
            chat_id=chat_id,
            channel=channel,
            response_schema=self._response_schema(),
        )
        return _AgentRuntimeResult(
            render=result.render,
            should_reply=result.should_reply,
            response_id=result.response_id,
            agent_trace=result.agent_trace,
            delegation_fallback_used=result.delegation_fallback_used,
            tokens_used=result.tokens_used,
        )

    def _build_model_user_input(self, message: ChannelMessage) -> tuple[str, str | list[dict[str, Any]] | None]:
        return self._input_service.build_model_user_input(message)

    def _response_schema(self) -> dict[str, Any]:
        return assistant_response_schema(kinds=["text", "html", "markdown_v2"], include_meta=True)

    async def _enforce_history_limit(self, session_id: str) -> None:
        if self._max_history_messages is None:
            return
        await self._memory.trim_history(session_id, self._max_history_messages)

    def _track_token_usage(self, session_id: str, tokens: int | None) -> int:
        return self._session_state.track_tokens(session_id, tokens)

    def _current_session_tokens(self, session_id: str) -> int:
        return self._session_state.current_tokens(session_id)

    @staticmethod
    def _build_token_trace(
        *,
        turn_total_tokens: int,
        session_total_tokens_before_compaction: int | None,
        session_total_tokens_after_compaction: int,
        compaction_performed: bool,
    ) -> dict[str, Any]:
        return SessionStateService.build_token_trace(
            turn_total_tokens=turn_total_tokens,
            session_total_tokens_before_compaction=session_total_tokens_before_compaction,
            session_total_tokens_after_compaction=session_total_tokens_after_compaction,
            compaction_performed=compaction_performed,
        )

    def _compact_system_prompt(self, system_prompt: str) -> str:
        return self._prompt_service.compact_system_prompt(system_prompt)

    async def _compact_history_if_needed(
        self,
        session_id: str,
        *,
        prompt_cache_key: str,
        system_prompt: str,
        notify: bool,
    ) -> _CompactionResult:
        result = await self._compaction_service.compact_history_if_needed(
            session_id,
            prompt_cache_key=prompt_cache_key,
            system_prompt=system_prompt,
            notify=notify,
            responses_state_mode=self._responses_state_mode(),
        )
        return _CompactionResult(
            updates=result.updates,
            performed=result.performed,
            tokens_used=result.tokens_used,
            session_total_tokens_before_compaction=result.session_total_tokens_before_compaction,
            session_total_tokens_after_compaction=result.session_total_tokens_after_compaction,
        )

    def _format_runtime_error_message(self, exc: Exception) -> str:
        if not self._logger.isEnabledFor(logging.DEBUG):
            return "Sorry, I couldn't answer right now."
        error_name = type(exc).__name__
        detail = str(exc).strip().replace("\n", " ")
        if detail:
            if len(detail) > 200:
                detail = f"{detail[:200]}..."
            return f"LLM error ({error_name}): {detail}"
        return f"LLM error ({error_name})."


def resolve_owner_id(message: ChannelMessage, default_owner_id: str | None) -> str:
    if default_owner_id:
        return default_owner_id
    if message.user_id is not None:
        return str(message.user_id)
    if message.chat_id is not None:
        return str(message.chat_id)
    return session_id_for(message)


def _prompt_cache_key(message: ChannelMessage) -> str | None:
    if message.channel and (message.user_id is not None or message.chat_id is not None):
        suffix = message.user_id if message.user_id is not None else message.chat_id
        return f"{message.channel}:{suffix}"
    session_key = session_id_for(message)
    if message.channel:
        return f"{message.channel}:{session_key}"
    return session_key

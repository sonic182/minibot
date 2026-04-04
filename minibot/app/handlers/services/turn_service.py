from __future__ import annotations

import logging
from typing import Any, Sequence

from minibot.app.agent_runtime import AgentRuntime
from minibot.app.handlers.services.audio_transcription_service import AudioAutoTranscriptionService
from minibot.app.handlers.services.compaction_service import HistoryCompactionService
from minibot.app.handlers.services.input_service import UserInputService
from minibot.app.handlers.services.metadata_service import ResponseMetadataService
from minibot.app.handlers.services.prompt_service import PromptService
from minibot.app.handlers.services.recent_file_tracking_service import RecentFileTrackingService
from minibot.app.handlers.services.runtime_service import RuntimeOrchestrationService
from minibot.app.handlers.services.session_state_service import SessionStateService
from minibot.app.incoming_files_context import build_history_user_entry
from minibot.app.agent_registry import AgentRegistry
from minibot.app.runtime_limits import build_runtime_limits
from minibot.app.skill_registry import SkillRegistry
from minibot.app.response_parser import extract_answer, plain_render
from minibot.app.tool_use_guardrail import ToolUseGuardrail
from minibot.core.channels import ChannelMessage, ChannelResponse
from minibot.core.events import MessageEvent
from minibot.core.memory import MemoryBackend
from minibot.llm.provider_factory import LLMClient
from minibot.llm.services import LLMExecutionProfile
from minibot.llm.services.response_schemas import main_assistant_response_model, main_assistant_response_schema
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.shared.utils import session_id_for, session_id_from_parts


class LLMTurnService:
    def __init__(
        self,
        *,
        memory: MemoryBackend,
        llm_client: LLMClient,
        tools: Sequence[ToolBinding],
        default_owner_id: str | None,
        max_history_messages: int | None,
        notify_compaction_updates: bool,
        tool_use_guardrail: ToolUseGuardrail,
        audio_auto_transcription_service: AudioAutoTranscriptionService | None,
        session_state: SessionStateService,
        metadata_service: ResponseMetadataService,
        input_service: UserInputService,
        prompt_service: PromptService,
        compaction_service: HistoryCompactionService,
        recent_file_tracking_service: RecentFileTrackingService,
        logger: logging.Logger,
        runtime: AgentRuntime | None = None,
    ) -> None:
        self._memory = memory
        self._llm_client = llm_client
        self._tools = list(tools)
        self._default_owner_id = default_owner_id
        self._max_history_messages = max_history_messages
        self._notify_compaction_updates = notify_compaction_updates
        self._tool_use_guardrail = tool_use_guardrail
        self._audio_auto_transcription_service = audio_auto_transcription_service
        self._session_state = session_state
        self._metadata_service = metadata_service
        self._input_service = input_service
        self._prompt_service = prompt_service
        self._compaction_service = compaction_service
        self._recent_file_tracking_service = recent_file_tracking_service
        self._logger = logger
        self._profile = LLMExecutionProfile.from_client(llm_client)
        self._runtime: AgentRuntime | None = None
        self._runtime_service: RuntimeOrchestrationService | None = None
        self.set_runtime(runtime)

    @property
    def session_state(self) -> SessionStateService:
        return self._session_state

    @property
    def prompts_dir(self) -> str:
        return self._prompt_service.prompts_dir

    @property
    def runtime(self) -> AgentRuntime | None:
        return self._runtime

    def set_runtime(self, runtime: AgentRuntime | None) -> None:
        self._runtime = runtime
        self._runtime_service = self._build_runtime_service(runtime) if runtime is not None else None

    async def handle(self, event: MessageEvent) -> ChannelResponse:
        message = event.message
        session_id = session_id_for(message)
        turn_total_tokens = 0
        owner_id = self._resolve_owner_id(message)
        model_text, model_user_content = self._input_service.build_model_user_input(message)
        tool_context = ToolContext(
            owner_id=owner_id,
            channel=message.channel,
            chat_id=message.chat_id,
            user_id=message.user_id,
        )
        if model_user_content is None and self._audio_auto_transcription_service is not None:
            auto_result = await self._audio_auto_transcription_service.transcribe_incoming_audio(
                message=message,
                context=tool_context,
            )
            model_text = self._audio_auto_transcription_service.apply_to_model_text(model_text, auto_result)
        if message.attachments:
            self._logger.debug(
                "prepared multimodal message",
                extra={
                    "channel": message.channel,
                    "chat_id": message.chat_id,
                    "user_id": message.user_id,
                    "attachment_count": len(message.attachments),
                    "attachment_types": [str(attachment.get("type", "unknown")) for attachment in message.attachments],
                    "responses_provider": self._profile.is_responses_provider,
                    "media_input_mode": self._input_service.media_input_mode(),
                },
            )
        await self._memory.append_history(session_id, "user", build_history_user_entry(message, model_text))
        await self._enforce_history_limit(session_id)
        model_text_for_generation = self._recent_file_tracking_service.augment_model_text_with_recent_files(
            session_id,
            model_text,
        )

        if message.attachments and not self._input_service.supports_media_inputs():
            answer = "Media inputs are supported for `openai_responses`, `openai`, and `openrouter`."
            await self._memory.append_history(session_id, "assistant", answer)
            await self._enforce_history_limit(session_id)
            chat_id = message.chat_id or message.user_id or 0
            metadata = self._metadata_service.response_metadata(True)
            metadata["token_trace"] = SessionStateService.build_token_trace(
                turn_total_tokens=0,
                session_total_tokens_before_compaction=None,
                session_total_tokens_after_compaction=self._session_state.current_tokens(session_id),
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
        system_prompt = self._prompt_service.compose_system_prompt(message.channel)
        use_previous_response_id = self._use_previous_response_id()
        previous_response_id = (
            self._session_state.get_previous_response_id(session_id, system_prompt=system_prompt)
            if use_previous_response_id
            else None
        )
        prompt_cache_key = _prompt_cache_key(message) if self._profile.prompt_cache_enabled else None
        agent_trace: list[dict[str, Any]] = []
        delegation_fallback_used = False
        runtime_result = None
        try:
            if self._runtime_service is None:
                generation = await self._llm_client.generate(
                    history,
                    model_text_for_generation,
                    user_content=model_user_content,
                    tools=self._tools,
                    tool_context=tool_context,
                    response_schema=main_assistant_response_schema(),
                    local_response_model=main_assistant_response_model(),
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
                    provider_tool_calls=getattr(generation, "provider_tool_calls", None),
                )
                turn_total_tokens += self._session_state.track_tokens(
                    session_id,
                    getattr(generation, "total_tokens", None),
                )
                parsed = extract_answer(generation.payload, logger=self._logger)
                render = parsed.render or plain_render("")
                should_reply = parsed.has_visible_answer
                if use_previous_response_id and generation.response_id:
                    self._session_state.set_previous_response_id(
                        session_id,
                        generation.response_id,
                        system_prompt=system_prompt,
                    )
            else:
                runtime_result = await self._runtime_service.run_with_agent_runtime(
                    session_id=session_id,
                    history=history,
                    model_text=model_text_for_generation,
                    model_user_content=model_user_content,
                    system_prompt=system_prompt,
                    tool_context=tool_context,
                    prompt_cache_key=prompt_cache_key,
                    previous_response_id=previous_response_id,
                    chat_id=message.chat_id,
                    channel=message.channel,
                    response_schema=main_assistant_response_schema(),
                )
                turn_total_tokens += runtime_result.tokens_used
                self._session_state.track_usage(
                    session_id,
                    input_tokens=None,
                    output_tokens=None,
                    total_tokens=None,
                    cached_input_tokens=None,
                    reasoning_output_tokens=None,
                    provider_tool_calls=runtime_result.provider_tool_calls,
                )
                render = runtime_result.render or plain_render("")
                should_reply = runtime_result.should_reply
                agent_trace = runtime_result.agent_trace
                delegation_fallback_used = runtime_result.delegation_fallback_used
                self._recent_file_tracking_service.track_from_runtime_state(session_id, runtime_result.runtime_state)
                if use_previous_response_id and runtime_result.response_id:
                    self._session_state.set_previous_response_id(
                        session_id,
                        runtime_result.response_id,
                        system_prompt=system_prompt,
                    )

            if not use_previous_response_id:
                self._session_state.clear_previous_response_id(session_id)

            self._logger.debug(
                "structured output parsed",
                extra={"kind": render.kind, "content_length": len(render.text), "should_reply": should_reply},
            )
        except Exception as exc:
            self._logger.exception("LLM call failed", exc_info=exc)
            render = plain_render(self._format_runtime_error_message(exc))
            should_reply = True
        visible_messages: list[str] = []
        response_updates_payload: list[dict[str, Any]] = []
        if runtime_result is not None and runtime_result.response_updates:
            for update in runtime_result.response_updates:
                visible_messages.append(update.text)
                response_updates_payload.append(_render_to_metadata(update))
        answer = render.text
        if should_reply and answer.strip():
            visible_messages.append(answer)
        for message_text in visible_messages:
            await self._memory.append_history(session_id, "assistant", message_text)
        if visible_messages:
            await self._enforce_history_limit(session_id)
        compact_prompt_cache_key = prompt_cache_key or f"{session_id}:runtime"
        compaction_result = await self._compaction_service.compact_history_if_needed(
            session_id,
            prompt_cache_key=compact_prompt_cache_key,
            system_prompt=system_prompt,
            notify=self._notify_compaction_updates,
            responses_state_mode=self._profile.responses_state_mode,
        )
        turn_total_tokens += compaction_result.tokens_used

        chat_id = message.chat_id or message.user_id or 0
        metadata = self._metadata_service.response_metadata(should_reply)
        metadata["primary_agent"] = "minibot"
        if agent_trace:
            metadata["agent_trace"] = agent_trace
        metadata["delegation_fallback_used"] = delegation_fallback_used
        if compaction_result.updates:
            metadata["compaction_updates"] = compaction_result.updates
        if response_updates_payload:
            metadata["response_updates"] = response_updates_payload
        metadata["token_trace"] = SessionStateService.build_token_trace(
            turn_total_tokens=turn_total_tokens,
            session_total_tokens_before_compaction=compaction_result.session_total_tokens_before_compaction,
            session_total_tokens_after_compaction=compaction_result.session_total_tokens_after_compaction,
            compaction_performed=compaction_result.performed,
        )
        usage_trace = self._session_state.latest_usage_trace(session_id)
        filtered_usage_trace = {key: value for key, value in usage_trace.items() if value is not None}
        if filtered_usage_trace:
            metadata["usage_trace"] = filtered_usage_trace
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
        system_prompt = self._prompt_service.compose_system_prompt(channel)
        use_previous_response_id = self._use_previous_response_id()
        previous_response_id = (
            self._session_state.get_previous_response_id(session_id, system_prompt=system_prompt)
            if use_previous_response_id
            else None
        )
        original_kind = response.render.kind if response.render is not None else "text"
        original_content = response.render.text if response.render is not None else response.text
        repair_prompt = PromptService.build_format_repair_prompt(
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
            response_schema=main_assistant_response_schema(),
            local_response_model=main_assistant_response_model(),
            prompt_cache_key=f"{channel}:{chat_id}:format-repair",
            previous_response_id=previous_response_id,
            system_prompt_override=system_prompt,
        )
        if use_previous_response_id and generation.response_id:
            self._session_state.set_previous_response_id(
                session_id,
                generation.response_id,
                system_prompt=system_prompt,
            )
        turn_total_tokens += self._session_state.track_tokens(session_id, getattr(generation, "total_tokens", None))
        parsed = extract_answer(generation.payload, logger=self._logger)
        render = parsed.render or plain_render(str(generation.payload))
        await self._memory.append_history(session_id, "user", repair_prompt)
        await self._memory.append_history(session_id, "assistant", render.text)
        await self._enforce_history_limit(session_id)
        compaction_result = await self._compaction_service.compact_history_if_needed(
            session_id,
            prompt_cache_key=f"{channel}:{chat_id}:format-repair",
            system_prompt=system_prompt,
            notify=False,
            responses_state_mode=self._profile.responses_state_mode,
        )
        turn_total_tokens += compaction_result.tokens_used
        metadata = self._metadata_service.response_metadata(True)
        metadata["format_repair_attempt"] = attempt
        metadata["format_repair_original_kind"] = original_kind
        metadata["token_trace"] = SessionStateService.build_token_trace(
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

    def _build_runtime_service(self, runtime: AgentRuntime) -> RuntimeOrchestrationService:
        return RuntimeOrchestrationService(
            runtime=runtime,
            llm_client=self._llm_client,
            guardrail=self._tool_use_guardrail,
            session_state=self._session_state,
            logger=self._logger,
        )

    def _use_previous_response_id(self) -> bool:
        return self._profile.is_responses_provider and self._profile.responses_state_mode == "previous_response_id"

    def _resolve_owner_id(self, message: ChannelMessage) -> str:
        if self._default_owner_id:
            return self._default_owner_id
        if message.user_id is not None:
            return str(message.user_id)
        if message.chat_id is not None:
            return str(message.chat_id)
        return session_id_for(message)

    async def _enforce_history_limit(self, session_id: str) -> None:
        if self._max_history_messages is None:
            return
        await self._memory.trim_history(session_id, self._max_history_messages)

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


def _prompt_cache_key(message: ChannelMessage) -> str | None:
    if message.channel and (message.user_id is not None or message.chat_id is not None):
        suffix = message.user_id if message.user_id is not None else message.chat_id
        return f"{message.channel}:{suffix}"
    session_key = session_id_for(message)
    if message.channel:
        return f"{message.channel}:{session_key}"
    return session_key


def _render_to_metadata(render: Any) -> dict[str, Any]:
    return {
        "kind": getattr(render, "kind", "text"),
        "text": getattr(render, "text", ""),
        "meta": dict(getattr(render, "meta", {}) or {}),
    }


def build_llm_turn_service(
    *,
    memory: MemoryBackend,
    llm_client: LLMClient,
    tools: Sequence[ToolBinding] | None = None,
    default_owner_id: str | None = None,
    max_history_messages: int | None = None,
    max_history_tokens: int | None = None,
    notify_compaction_updates: bool = False,
    agent_timeout_seconds: int = 120,
    environment_prompt_fragment: str = "",
    tool_use_guardrail: ToolUseGuardrail,
    managed_files_root: str | None = None,
    audio_auto_transcription_service: AudioAutoTranscriptionService | None = None,
    logger: logging.Logger | None = None,
    agent_registry: AgentRegistry | None = None,
    skill_registry: SkillRegistry | None = None,
) -> LLMTurnService:
    service_logger = logger or logging.getLogger("minibot.handler")
    tool_bindings = list(tools or [])
    session_state = SessionStateService()
    metadata_service = ResponseMetadataService(llm_client=llm_client)
    input_service = UserInputService(llm_client=llm_client)
    prompt_service = PromptService(
        llm_client=llm_client,
        tools=tool_bindings,
        environment_prompt_fragment=environment_prompt_fragment,
        logger=service_logger,
        agent_registry=agent_registry,
        skill_registry=skill_registry,
    )
    compaction_service = HistoryCompactionService(
        memory=memory,
        llm_client=llm_client,
        session_state=session_state,
        prompt_service=prompt_service,
        logger=service_logger,
        max_history_tokens=max_history_tokens,
        compaction_user_request="Please compact the current conversation memory.",
    )
    recent_file_tracking_service = RecentFileTrackingService(
        session_state=session_state,
        managed_files_root=managed_files_root,
    )
    profile = LLMExecutionProfile.from_client(llm_client)
    runtime = None
    if profile.supports_agent_runtime:
        runtime = AgentRuntime(
            llm_client=llm_client,
            tools=tool_bindings,
            limits=build_runtime_limits(
                llm_client=llm_client,
                timeout_seconds=agent_timeout_seconds,
                min_timeout_seconds=120,
            ),
            allowed_append_message_tools=["self_insert_artifact"],
            allow_system_inserts=False,
            managed_files_root=managed_files_root,
        )
    return LLMTurnService(
        memory=memory,
        llm_client=llm_client,
        tools=tool_bindings,
        default_owner_id=default_owner_id,
        max_history_messages=max_history_messages,
        notify_compaction_updates=notify_compaction_updates,
        tool_use_guardrail=tool_use_guardrail,
        audio_auto_transcription_service=audio_auto_transcription_service,
        session_state=session_state,
        metadata_service=metadata_service,
        input_service=input_service,
        prompt_service=prompt_service,
        compaction_service=compaction_service,
        recent_file_tracking_service=recent_file_tracking_service,
        logger=service_logger,
        runtime=runtime,
    )

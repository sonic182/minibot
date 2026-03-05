from __future__ import annotations

import logging
from typing import Sequence

from minibot.app.agent_runtime import AgentRuntime
from minibot.app.handlers.services import (
    AudioAutoTranscriptionService,
    HistoryCompactionService,
    LLMTurnService,
    PromptService,
    RecentFileTrackingService,
    ResponseMetadataService,
    SessionStateService,
    UserInputService,
)
from minibot.app.runtime_limits import build_runtime_limits
from minibot.app.tool_use_guardrail import NoopToolUseGuardrail, ToolUseGuardrail
from minibot.core.channels import ChannelMessage, ChannelResponse
from minibot.core.events import MessageEvent
from minibot.core.memory import MemoryBackend
from minibot.llm.provider_factory import LLMClient
from minibot.llm.services import LLMExecutionProfile
from minibot.llm.tools.base import ToolBinding
from minibot.shared.utils import session_id_for


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
        audio_auto_transcription_service: AudioAutoTranscriptionService | None = None,
        turn_service: LLMTurnService | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._tools = list(tools or [])
        self._logger = logging.getLogger("minibot.handler")
        self._tool_use_guardrail: ToolUseGuardrail = tool_use_guardrail or NoopToolUseGuardrail()
        profile = LLMExecutionProfile.from_client(llm_client)

        if turn_service is None:
            session_state = SessionStateService()
            metadata_service = ResponseMetadataService(llm_client=self._llm_client)
            input_service = UserInputService(llm_client=self._llm_client)
            prompt_service = PromptService(
                llm_client=self._llm_client,
                tools=self._tools,
                environment_prompt_fragment=environment_prompt_fragment,
                logger=self._logger,
            )
            compaction_service = HistoryCompactionService(
                memory=memory,
                llm_client=self._llm_client,
                session_state=session_state,
                prompt_service=prompt_service,
                logger=self._logger,
                max_history_tokens=max_history_tokens,
                compaction_user_request=self._COMPACTION_USER_REQUEST,
            )
            recent_file_tracking_service = RecentFileTrackingService(
                session_state=session_state,
                managed_files_root=managed_files_root,
            )
            runtime = None
            if profile.supports_agent_runtime:
                runtime = AgentRuntime(
                    llm_client=self._llm_client,
                    tools=self._tools,
                    limits=build_runtime_limits(
                        llm_client=self._llm_client,
                        timeout_seconds=agent_timeout_seconds,
                        min_timeout_seconds=120,
                    ),
                    allowed_append_message_tools=["self_insert_artifact"],
                    allow_system_inserts=False,
                    managed_files_root=managed_files_root,
                )
            turn_service = LLMTurnService(
                memory=memory,
                llm_client=self._llm_client,
                tools=self._tools,
                default_owner_id=default_owner_id,
                max_history_messages=max_history_messages,
                notify_compaction_updates=notify_compaction_updates,
                tool_use_guardrail=self._tool_use_guardrail,
                audio_auto_transcription_service=audio_auto_transcription_service,
                session_state=session_state,
                metadata_service=metadata_service,
                input_service=input_service,
                prompt_service=prompt_service,
                compaction_service=compaction_service,
                recent_file_tracking_service=recent_file_tracking_service,
                logger=self._logger,
                runtime=runtime,
            )
        self._turn_service = turn_service
        self._session_state = self._turn_service.session_state
        self._session_total_tokens = self._session_state.session_total_tokens
        self._session_previous_response_ids = self._session_state.session_previous_response_ids
        self._session_recent_files = self._session_state.session_recent_files
        self._prompts_dir = self._turn_service.prompts_dir

    @property
    def _runtime(self) -> AgentRuntime | None:
        return self._turn_service.runtime

    @_runtime.setter
    def _runtime(self, runtime: AgentRuntime | None) -> None:
        self._turn_service.set_runtime(runtime)

    async def handle(self, event: MessageEvent) -> ChannelResponse:
        return await self._turn_service.handle(event)

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
        return await self._turn_service.repair_format_response(
            response=response,
            parse_error=parse_error,
            channel=channel,
            chat_id=chat_id,
            user_id=user_id,
            attempt=attempt,
        )

    @staticmethod
    def _build_format_repair_prompt(
        *,
        channel: str,
        original_kind: str,
        parse_error: str,
        original_content: str,
    ) -> str:
        return PromptService.build_format_repair_prompt(
            channel=channel,
            original_kind=original_kind,
            parse_error=parse_error,
            original_content=original_content,
        )


def resolve_owner_id(message: ChannelMessage, default_owner_id: str | None) -> str:
    if default_owner_id:
        return default_owner_id
    if message.user_id is not None:
        return str(message.user_id)
    if message.chat_id is not None:
        return str(message.chat_id)
    return session_id_for(message)

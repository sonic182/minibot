from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from pathlib import Path

from minibot.app.agent_registry import AgentRegistry
from minibot.app.agent_roster import AgentRosterChange, reload_agent_roster
from minibot.app.environment_context import build_environment_prompt_fragment
from minibot.app.event_bus import EventBus
from minibot.app.extensions import ExtensionRegistry
from minibot.app.handlers import LLMMessageHandler
from minibot.app.handlers.services import (
    AudioAutoTranscribePolicy,
    AudioAutoTranscriptionService,
    ToolBindingAudioTranscriptionExecutor,
    build_llm_turn_service,
)
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.app.skill_registry import SkillRegistry
from minibot.app.tool_capabilities import MainAgentToolView, main_agent_tool_view
from minibot.app.tool_factory import build_enabled_tools
from minibot.app.tool_use_guardrail import LLMClassifierToolUseGuardrail, NoopToolUseGuardrail
from minibot.app.turn_decision import NoopTurnDecision, ShadowTurnDecision
from minibot.config.schema import Settings
from minibot.core.agents import AgentDefinitionReader
from minibot.core.channels import ChannelCapabilities, ChannelResponse, RenderableResponse, session_identifier
from minibot.core.decisions import DecisionClient
from minibot.core.events import (
    BaseEvent,
    MessageEvent,
    OutboundEvent,
    OutboundFormatRepairEvent,
    TurnCompletedEvent,
    TurnFailedEvent,
    TurnStartedEvent,
)
from minibot.core.files import FileStorage
from minibot.core.memory import MemoryBackend, PendingTurnRepository
from minibot.llm.provider_factory import LLMClient
from minibot.llm.tools.base import ToolBinding
from minibot.shared.utils import humanize_token_count, summarize_items

_INTERNAL_ERROR_REPLY = "Sorry, I couldn't answer right now."


def _token_trace_log_fields(token_trace: object) -> dict[str, object]:
    """Log-friendly view of a response's token trace, shared by both handler paths."""
    if not isinstance(token_trace, dict):
        return {"turn_total_tokens": None, "session_total_tokens": None, "compaction_performed": None}
    return {
        "turn_total_tokens": humanize_token_count(token_trace["turn_total_tokens"])
        if isinstance(token_trace.get("turn_total_tokens"), int)
        else None,
        "session_total_tokens": humanize_token_count(token_trace["session_total_tokens"])
        if isinstance(token_trace.get("session_total_tokens"), int)
        else None,
        "compaction_performed": token_trace.get("compaction_performed"),
    }


class Dispatcher:
    def __init__(
        self,
        event_bus: EventBus,
        *,
        pending_turns: PendingTurnRepository,
        settings: Settings,
        memory_backend: MemoryBackend,
        agent_registry: AgentRegistry,
        llm_factory: LLMClientFactory,
        skill_registry: SkillRegistry,
        config_path: Path | None,
        llm_client: LLMClient,
        extensions: ExtensionRegistry,
        managed_storage: FileStorage | None,
        channel_capabilities: Mapping[str, ChannelCapabilities] | None = None,
        decision_client: DecisionClient | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._channel_capabilities = dict(channel_capabilities or {})
        self._subscription = event_bus.subscribe(types=(MessageEvent, OutboundFormatRepairEvent))
        self._history_subscription = event_bus.subscribe(types=(OutboundEvent,))
        self._memory = memory_backend
        self._pending_turns = pending_turns
        # Kept so a roster reload can rebuild the tool list: fetch_agent_info only exists when the
        # roster is non-empty, so a reload from zero agents has to add it, not just re-filter.
        self._settings = settings
        self._agent_registry = agent_registry
        self._llm_factory = llm_factory
        self._skill_registry = skill_registry
        self._extensions = extensions
        self._managed_storage = managed_storage
        self._config_path = config_path
        tools = self._build_tools()
        main_agent_tools_view = self._main_agent_view(tools)
        guardrail_mode = settings.orchestration.main_tool_use_guardrail
        if guardrail_mode == "llm_classifier":
            tool_use_guardrail: NoopToolUseGuardrail | LLMClassifierToolUseGuardrail = LLMClassifierToolUseGuardrail(
                llm_client=llm_client,
                tools=main_agent_tools_view.tools,
            )
        else:
            tool_use_guardrail = NoopToolUseGuardrail()
        turn_decision: NoopTurnDecision | ShadowTurnDecision = NoopTurnDecision()
        if decision_client is not None:
            turn_decision = ShadowTurnDecision(
                client=decision_client,
                tools=main_agent_tools_view.tools,
                timeout_seconds=settings.decision.timeout_seconds,
            )
        audio_transcription_cfg = getattr(settings.tools, "audio_transcription", None)
        auto_transcribe_enabled = bool(getattr(audio_transcription_cfg, "auto_transcribe_short_incoming", False))
        auto_transcribe_max_duration_seconds = int(
            getattr(audio_transcription_cfg, "auto_transcribe_max_duration_seconds", 45)
        )
        audio_auto_transcription_service = _build_audio_auto_transcription_service(
            tools=main_agent_tools_view.tools,
            enabled=auto_transcribe_enabled,
            max_duration_seconds=auto_transcribe_max_duration_seconds,
        )
        task_handoff_callback = getattr(self._pending_turns, "mark_task_handoff", None)
        if not callable(task_handoff_callback):
            task_handoff_callback = None
        turn_service = build_llm_turn_service(
            memory=memory_backend,
            llm_client=llm_client,
            tools=main_agent_tools_view.tools,
            owner_id=settings.runtime.owner_id,
            max_history_messages=settings.memory.max_history_messages,
            max_history_tokens=settings.memory.max_history_tokens,
            notify_compaction_updates=settings.memory.notify_compaction_updates,
            agent_timeout_seconds=settings.runtime.agent_timeout_seconds,
            environment_prompt_fragment=build_environment_prompt_fragment(settings, config_path),
            tool_use_guardrail=tool_use_guardrail,
            managed_files_root=settings.tools.file_storage.root_dir if settings.tools.file_storage.enabled else None,
            audio_auto_transcription_service=audio_auto_transcription_service,
            agent_registry=agent_registry,
            skill_registry=skill_registry,
            preload_skill_catalog=settings.tools.skills.preload_catalog,
            event_bus=event_bus,
            task_handoff_callback=task_handoff_callback,
            extension_prompt_fragments=extensions.prompt_fragments_for(main_agent_tools_view.tools),
            turn_decision=turn_decision,
        )
        self._handler = LLMMessageHandler(turn_service)
        self._turn_service = turn_service
        self._all_tools = tools
        self._logger = logging.getLogger("minibot.dispatcher")
        strip_logs = bool(getattr(getattr(settings, "llm", None), "strip_logs", False))
        self._main_agent_tool_names = sorted(binding.tool.name for binding in main_agent_tools_view.tools)
        tool_summary = summarize_items(self._main_agent_tool_names)
        self._logger.info(
            "main agent tool configuration loaded",
            extra={
                "main_agent_tools_count": tool_summary["count"],
                "main_agent_tools_preview": tool_summary["preview"],
            },
        )
        if not strip_logs:
            self._logger.debug(
                "main agent tools enabled",
                extra={"main_agent_tools_enabled": self._main_agent_tool_names or ["none"]},
            )
        if not skill_registry.is_empty():
            self._logger.info(
                "skills loaded",
                extra={"skills": skill_registry.names()},
            )
        if settings.tools.mcp.enabled:
            mcp_prefix = f"{settings.tools.mcp.name_prefix}_"
            mcp_tool_names = sorted(
                binding.tool.name
                for binding in tools
                if binding.tool.name.startswith(mcp_prefix) and "__" in binding.tool.name
            )
            tool_summary = summarize_items(mcp_tool_names)
            self._logger.info(
                "mcp tool configuration loaded",
                extra={
                    "mcp_servers_configured": len(settings.tools.mcp.servers),
                    "mcp_tools_count": tool_summary["count"],
                    "mcp_tools_preview": tool_summary["preview"],
                },
            )
            if not strip_logs:
                self._logger.debug("mcp tools enabled", extra={"mcp_tools_enabled": mcp_tool_names or ["none"]})
        if main_agent_tools_view.hidden_tool_names:
            self._logger.info(
                "main agent tools hidden due to exclusive ownership",
                extra={"hidden_tools": main_agent_tools_view.hidden_tool_names},
            )
        self._task: asyncio.Task[None] | None = None
        self._history_task: asyncio.Task[None] | None = None

    def _build_tools(self) -> list[ToolBinding]:
        return build_enabled_tools(
            self._settings,
            self._memory,
            event_bus=self._event_bus,
            agent_registry=self._agent_registry,
            llm_factory=self._llm_factory,
            skill_registry=self._skill_registry,
            extension_tools=self._extensions.tools,
            managed_storage=self._managed_storage,
            config_path=self._config_path,
        )

    def _main_agent_view(self, tools: list[ToolBinding]) -> MainAgentToolView:
        return main_agent_tool_view(
            tools=tools,
            orchestration_config=self._settings.orchestration,
            agent_specs=self._agent_registry.all(),
            mcp_name_prefix=self._settings.tools.mcp.name_prefix,
        )

    async def refresh_agent_roster(self, reader: AgentDefinitionReader) -> AgentRosterChange:
        """Re-read agent definitions and apply the new roster everywhere it is captured.

        Raises whatever the loader raises — a bad file, a name collision or a ceiling violation —
        without touching the running registry, so a rejected reload is a no-op. The whole tool list
        is rebuilt rather than filtered so a reload from zero agents adds ``fetch_agent_info``.
        """
        change = await reload_agent_roster(settings=self._settings, registry=self._agent_registry, reader=reader)
        tools = self._build_tools()
        view = self._main_agent_view(tools)
        self._all_tools = tools
        self._main_agent_tool_names = sorted(binding.tool.name for binding in view.tools)
        self._turn_service.replace_tools(
            view.tools,
            extension_prompt_fragments=self._extensions.prompt_fragments_for(view.tools),
        )
        if view.hidden_tool_names:
            self._logger.info(
                "main agent tools hidden due to exclusive ownership",
                extra={"hidden_tools": view.hidden_tool_names},
            )
        return change

    @property
    def main_agent_tool_names(self) -> list[str]:
        return list(self._main_agent_tool_names)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())
        self._history_task = asyncio.create_task(self._record_outbound_history())

    async def _record_outbound_history(self) -> None:
        async for event in self._history_subscription:
            if not isinstance(event, OutboundEvent):
                continue
            response = event.response
            text = response.metadata.get("history_text")
            if not isinstance(text, str) or not text:
                continue
            try:
                await self._memory.append_history(session_identifier(response.channel, response.chat_id), "user", text)
            except Exception:
                self._logger.exception(
                    "failed to record delivered message in history",
                    extra={"channel": response.channel, "chat_id": response.chat_id},
                )

    async def _run(self) -> None:
        async for event in self._subscription:
            if isinstance(event, MessageEvent):
                self._logger.info("processing message event", extra={"event_id": event.event_id})
                await self._handle_message(event)
            if isinstance(event, OutboundFormatRepairEvent):
                self._logger.info("processing outbound format repair event", extra={"event_id": event.event_id})
                await self._handle_format_repair(event)

    async def _publish_lifecycle(self, event: BaseEvent) -> None:
        """Publish turn telemetry without ever failing the turn.

        A stopped bus raises, and shutdown races are normal. Letting that escape would
        abort a live turn *and* run the ``finally`` below, clearing the pending-turn row
        that exists so an interrupted turn is replayed on the next boot.
        """
        with contextlib.suppress(Exception):
            await self._event_bus.publish(event)

    def _with_channel_capabilities(self, event: MessageEvent) -> MessageEvent:
        capabilities = self._channel_capabilities.get(event.message.channel)
        if capabilities is None or "capabilities" in event.message.model_fields_set:
            return event
        message = event.message.model_copy(update={"capabilities": capabilities})
        return event.model_copy(update={"message": message})

    async def _handle_message(self, event: MessageEvent) -> None:
        event = self._with_channel_capabilities(event)
        await self._pending_turns.mark_pending(event.event_id, event.message.model_dump_json())
        try:
            message = event.message
            await self._publish_lifecycle(
                TurnStartedEvent(
                    turn_id=event.event_id,
                    channel=message.channel,
                    chat_id=message.chat_id,
                    user_id=message.user_id,
                )
            )
            self._logger.debug(
                "incoming message",
                extra={
                    "event_id": event.event_id,
                    "chat_id": message.chat_id,
                    "user_id": message.user_id,
                    "text_length": len(message.text),
                },
            )
            response = await self._handler.handle(event)
            should_reply = response.metadata.get("should_reply", True)
            token_trace = response.metadata.get("token_trace")
            self._logger.debug(
                "handler response",
                extra={
                    "event_id": event.event_id,
                    "chat_id": response.chat_id,
                    "text_length": len(response.text),
                    "should_reply": should_reply,
                    "llm_provider": response.metadata.get("llm_provider"),
                    "llm_model": response.metadata.get("llm_model"),
                    **_token_trace_log_fields(token_trace),
                },
            )
            response_updates = response.metadata.get("response_updates")
            if isinstance(response_updates, list):
                for update in response_updates:
                    if not isinstance(update, dict):
                        continue
                    text = str(update.get("text") or "").strip()
                    if not text:
                        continue
                    kind = str(update.get("kind") or "text")
                    meta = update.get("meta")
                    if not isinstance(meta, dict):
                        meta = {}
                    await self._event_bus.publish(
                        OutboundEvent(
                            response=ChannelResponse(
                                channel=response.channel,
                                chat_id=response.chat_id,
                                text=text,
                                render=RenderableResponse(kind=kind, text=text, meta=meta),
                                metadata={"should_reply": True, "continuation_update": True},
                            )
                        )
                    )
            if should_reply:
                await self._event_bus.publish(OutboundEvent(response=response))
                compaction_updates = response.metadata.get("compaction_updates")
                if isinstance(compaction_updates, list):
                    for update in compaction_updates:
                        if not isinstance(update, str) or not update.strip():
                            continue
                        await self._event_bus.publish(
                            OutboundEvent(
                                response=ChannelResponse(
                                    channel=response.channel,
                                    chat_id=response.chat_id,
                                    text=update,
                                    render=RenderableResponse(kind="text", text=update),
                                    metadata={"should_reply": True, "compaction_update": True},
                                )
                            )
                        )
            else:
                self._logger.info("skipping user reply as instructed", extra={"event_id": event.event_id})
            await self._publish_lifecycle(
                TurnCompletedEvent(
                    turn_id=event.event_id,
                    channel=response.channel,
                    chat_id=response.chat_id,
                    should_reply=bool(should_reply),
                    llm_provider=response.metadata.get("llm_provider"),
                    llm_model=response.metadata.get("llm_model"),
                    token_trace=token_trace if isinstance(token_trace, dict) else {},
                    compaction_performed=token_trace.get("compaction_performed")
                    if isinstance(token_trace, dict)
                    else None,
                )
            )
        except Exception as exc:
            self._logger.exception("failed to handle message", exc_info=exc)
            await self._publish_lifecycle(
                TurnFailedEvent(
                    turn_id=event.event_id,
                    channel=event.message.channel,
                    chat_id=event.message.chat_id,
                    error=str(exc),
                )
            )
            await self._publish_failure_reply(event)
        finally:
            await self._pending_turns.clear_pending(event.event_id)

    async def _publish_failure_reply(self, event: MessageEvent) -> None:
        message = event.message
        try:
            await self._event_bus.publish(
                OutboundEvent(
                    response=ChannelResponse(
                        channel=message.channel,
                        chat_id=message.chat_id or message.user_id or 0,
                        text=_INTERNAL_ERROR_REPLY,
                        render=RenderableResponse(kind="text", text=_INTERNAL_ERROR_REPLY),
                        metadata={"should_reply": True},
                    )
                )
            )
        except Exception:
            self._logger.exception("failed to publish the failure reply", extra={"event_id": event.event_id})

    async def _handle_format_repair(self, event: OutboundFormatRepairEvent) -> None:
        try:
            repaired = await self._handler.repair_format_response(
                response=event.response,
                parse_error=event.parse_error,
                channel=event.channel,
                chat_id=event.chat_id,
                user_id=event.user_id,
                attempt=event.attempt,
                capabilities=event.capabilities,
            )
            should_reply = repaired.metadata.get("should_reply", True)
            token_trace = repaired.metadata.get("token_trace")
            self._logger.debug(
                "format repair handler response",
                extra={
                    "event_id": event.event_id,
                    "chat_id": repaired.chat_id,
                    "text": repaired.text,
                    "should_reply": should_reply,
                    "attempt": event.attempt,
                    **_token_trace_log_fields(token_trace),
                },
            )
            if not should_reply:
                return
            await self._event_bus.publish(OutboundEvent(response=repaired))
        except Exception as exc:
            self._logger.exception("failed to handle format repair", exc_info=exc)
            fallback_text = event.response.render.text if event.response.render is not None else event.response.text
            fallback_metadata = dict(event.response.metadata)
            fallback_metadata.pop("history_text", None)
            fallback_metadata["format_repair_failed"] = True
            fallback_metadata["format_repair_error"] = str(exc)
            fallback_response = ChannelResponse(
                channel=event.channel,
                chat_id=event.chat_id,
                text=fallback_text,
                render=RenderableResponse(kind="text", text=fallback_text),
                metadata=fallback_metadata,
            )
            try:
                await self._event_bus.publish(OutboundEvent(response=fallback_response))
            except Exception as publish_exc:
                self._logger.exception("failed to publish format repair fallback", exc_info=publish_exc)

    async def stop(self) -> None:
        await self._subscription.close()
        await self._history_subscription.close()
        for task in (self._task, self._history_task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


def _build_audio_auto_transcription_service(
    *,
    tools: list,
    enabled: bool,
    max_duration_seconds: int,
) -> AudioAutoTranscriptionService | None:
    transcribe_binding = next((binding for binding in tools if binding.tool.name == "transcribe_audio"), None)
    if transcribe_binding is None:
        return None
    return AudioAutoTranscriptionService(
        executor=ToolBindingAudioTranscriptionExecutor(transcribe_binding),
        policy=AudioAutoTranscribePolicy(enabled=enabled, max_duration_seconds=max_duration_seconds),
        logger=logging.getLogger("minibot.audio_auto_transcription"),
    )

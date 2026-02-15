from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Optional

from minibot.adapters.container import AppContainer
from minibot.app.event_bus import EventBus
from minibot.app.handlers import LLMMessageHandler
from minibot.app.tool_capabilities import main_agent_tool_view
from minibot.core.channels import ChannelResponse, RenderableResponse
from minibot.core.events import MessageEvent, OutboundEvent, OutboundFormatRepairEvent
from minibot.llm.tools.factory import build_enabled_tools
from minibot.shared.utils import humanize_token_count


class Dispatcher:
    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._subscription = event_bus.subscribe()
        settings = AppContainer.get_settings()
        prompt_service = AppContainer.get_scheduled_prompt_service()
        memory_backend = AppContainer.get_memory_backend()
        agent_registry = AppContainer.get_agent_registry()
        llm_factory = AppContainer.get_llm_factory()
        tools = build_enabled_tools(
            settings,
            memory_backend,
            AppContainer.get_kv_memory_backend(),
            prompt_service,
            event_bus,
            agent_registry,
            llm_factory,
        )
        main_agent_tools_view = main_agent_tool_view(
            tools=tools,
            orchestration_config=settings.orchestration,
            agent_specs=agent_registry.all(),
        )
        self._handler = LLMMessageHandler(
            memory=memory_backend,
            llm_client=AppContainer.get_llm_client(),
            tools=main_agent_tools_view.tools,
            default_owner_id=settings.tools.kv_memory.default_owner_id,
            max_history_messages=settings.memory.max_history_messages,
            max_history_tokens=settings.memory.max_history_tokens,
            notify_compaction_updates=settings.memory.notify_compaction_updates,
            agent_timeout_seconds=settings.runtime.agent_timeout_seconds,
        )
        self._logger = logging.getLogger("minibot.dispatcher")
        if settings.tools.mcp.enabled:
            mcp_prefix = f"{settings.tools.mcp.name_prefix}_"
            mcp_tool_names = sorted(
                binding.tool.name
                for binding in tools
                if binding.tool.name.startswith(mcp_prefix) and "__" in binding.tool.name
            )
            self._logger.info(
                "mcp tool configuration loaded",
                extra={
                    "mcp_servers_configured": len(settings.tools.mcp.servers),
                    "mcp_tools_enabled": mcp_tool_names or ["none"],
                },
            )
        if main_agent_tools_view.hidden_tool_names:
            self._logger.info(
                "main agent tools hidden due to exclusive ownership",
                extra={"hidden_tools": main_agent_tools_view.hidden_tool_names},
            )
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        async for event in self._subscription:
            if isinstance(event, MessageEvent):
                self._logger.info("processing message event", extra={"event_id": event.event_id})
                await self._handle_message(event)
            if isinstance(event, OutboundFormatRepairEvent):
                self._logger.info("processing outbound format repair event", extra={"event_id": event.event_id})
                await self._handle_format_repair(event)

    async def _handle_message(self, event: MessageEvent) -> None:
        try:
            message = event.message
            self._logger.debug(
                "incoming message",
                extra={
                    "event_id": event.event_id,
                    "chat_id": message.chat_id,
                    "user_id": message.user_id,
                    "text": message.text,
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
                    "text": response.text,
                    "should_reply": should_reply,
                    "llm_provider": response.metadata.get("llm_provider"),
                    "llm_model": response.metadata.get("llm_model"),
                    "turn_total_tokens": humanize_token_count(token_trace.get("turn_total_tokens"))
                    if isinstance(token_trace, dict) and isinstance(token_trace.get("turn_total_tokens"), int)
                    else None,
                    "session_total_tokens": humanize_token_count(token_trace.get("session_total_tokens"))
                    if isinstance(token_trace, dict) and isinstance(token_trace.get("session_total_tokens"), int)
                    else None,
                    "compaction_performed": token_trace.get("compaction_performed")
                    if isinstance(token_trace, dict)
                    else None,
                },
            )
            if not should_reply:
                self._logger.info("skipping user reply as instructed", extra={"event_id": event.event_id})
                return
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
        except Exception as exc:
            self._logger.exception("failed to handle message", exc_info=exc)

    async def _handle_format_repair(self, event: OutboundFormatRepairEvent) -> None:
        try:
            repaired = await self._handler.repair_format_response(
                response=event.response,
                parse_error=event.parse_error,
                channel=event.channel,
                chat_id=event.chat_id,
                user_id=event.user_id,
                attempt=event.attempt,
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
                    "turn_total_tokens": humanize_token_count(token_trace.get("turn_total_tokens"))
                    if isinstance(token_trace, dict) and isinstance(token_trace.get("turn_total_tokens"), int)
                    else None,
                    "session_total_tokens": humanize_token_count(token_trace.get("session_total_tokens"))
                    if isinstance(token_trace, dict) and isinstance(token_trace.get("session_total_tokens"), int)
                    else None,
                    "compaction_performed": token_trace.get("compaction_performed")
                    if isinstance(token_trace, dict)
                    else None,
                },
            )
            if not should_reply:
                return
            await self._event_bus.publish(OutboundEvent(response=repaired))
        except Exception as exc:
            self._logger.exception("failed to handle format repair", exc_info=exc)
            fallback_text = event.response.render.text if event.response.render is not None else event.response.text
            fallback_metadata = dict(event.response.metadata)
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
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Sequence

from minibot.app.response_parser import extract_answer
from minibot.core.memory import MemoryBackend
from minibot.llm.provider_factory import LLMClient

from minibot.app.handlers.services.prompt_service import PromptService
from minibot.app.handlers.services.session_state_service import SessionStateService


@dataclass(frozen=True)
class CompactionResult:
    updates: list[str]
    performed: bool
    tokens_used: int
    session_total_tokens_before_compaction: int | None
    session_total_tokens_after_compaction: int


class HistoryCompactionService:
    def __init__(
        self,
        *,
        memory: MemoryBackend,
        llm_client: LLMClient,
        session_state: SessionStateService,
        prompt_service: PromptService,
        logger: logging.Logger,
        max_history_tokens: int | None,
        compaction_user_request: str,
    ) -> None:
        self._memory = memory
        self._llm_client = llm_client
        self._session_state = session_state
        self._prompt_service = prompt_service
        self._logger = logger
        self._max_history_tokens = max_history_tokens
        self._compaction_user_request = compaction_user_request

    async def compact_history_if_needed(
        self,
        session_id: str,
        *,
        prompt_cache_key: str,
        system_prompt: str,
        notify: bool,
        responses_state_mode: str,
    ) -> CompactionResult:
        updates: list[str] = []
        if self._max_history_tokens is None:
            return CompactionResult(
                updates=updates,
                performed=False,
                tokens_used=0,
                session_total_tokens_before_compaction=None,
                session_total_tokens_after_compaction=self._session_state.current_tokens(session_id),
            )
        total_tokens = self._session_state.current_tokens(session_id)
        if total_tokens < self._max_history_tokens:
            return CompactionResult(
                updates=updates,
                performed=False,
                tokens_used=0,
                session_total_tokens_before_compaction=None,
                session_total_tokens_after_compaction=total_tokens,
            )
        history = list(await self._memory.get_history(session_id))
        if not history:
            session_before_reset = total_tokens
            self._session_state.session_total_tokens[session_id] = 0
            return CompactionResult(
                updates=updates,
                performed=False,
                tokens_used=0,
                session_total_tokens_before_compaction=session_before_reset,
                session_total_tokens_after_compaction=0,
            )
        if notify:
            updates.append("running compaction...")
        compaction_tokens = 0
        try:
            if self._should_use_responses_compaction_endpoint(session_id, responses_state_mode):
                try:
                    previous_response_id = self._session_state.get_previous_response_id(session_id)
                    if previous_response_id:
                        compacted = await self._llm_client.compact_response(
                            previous_response_id=previous_response_id,
                            prompt_cache_key=f"{prompt_cache_key}:compact",
                        )
                        compaction_text = self._extract_compaction_text(compacted.output) or "Conversation compacted."
                        session_before_reset = self._session_state.current_tokens(session_id)
                        compaction_tokens = self._session_state.track_tokens(session_id, compacted.total_tokens)
                        await self._memory.trim_history(session_id, 0)
                        await self._memory.append_history(session_id, "user", self._compaction_user_request)
                        await self._memory.append_history(session_id, "assistant", compaction_text)
                        self._session_state.session_total_tokens[session_id] = 0
                        self._session_state.set_previous_response_id(session_id, compacted.response_id)
                        if notify:
                            updates.append("done compacting")
                            updates.append(compaction_text)
                        return CompactionResult(
                            updates=updates,
                            performed=True,
                            tokens_used=compaction_tokens,
                            session_total_tokens_before_compaction=session_before_reset,
                            session_total_tokens_after_compaction=0,
                        )
                except Exception as exc:
                    self._logger.warning(
                        "responses compact endpoint failed; falling back to summary compaction",
                        extra={"error": str(exc)},
                    )

            compact_generation = await self._llm_client.generate(
                history,
                self._compaction_user_request,
                user_content=None,
                tools=[],
                tool_context=None,
                response_schema=None,
                prompt_cache_key=f"{prompt_cache_key}:compact",
                previous_response_id=None,
                system_prompt_override=self._prompt_service.compact_system_prompt(system_prompt),
            )
            session_before_reset = self._session_state.current_tokens(session_id)
            compaction_tokens = self._session_state.track_tokens(
                session_id,
                getattr(compact_generation, "total_tokens", None),
            )
            compact_render, _ = extract_answer(compact_generation.payload, logger=self._logger)
            await self._memory.trim_history(session_id, 0)
            await self._memory.append_history(session_id, "user", self._compaction_user_request)
            await self._memory.append_history(session_id, "assistant", compact_render.text)
            self._session_state.session_total_tokens[session_id] = 0
            if responses_state_mode == "previous_response_id":
                fallback_response_id = getattr(compact_generation, "response_id", None)
                if isinstance(fallback_response_id, str) and fallback_response_id:
                    self._session_state.set_previous_response_id(session_id, fallback_response_id)
                else:
                    self._session_state.clear_previous_response_id(session_id)
            if notify:
                updates.append("done compacting")
                updates.append(compact_render.text)
            return CompactionResult(
                updates=updates,
                performed=True,
                tokens_used=compaction_tokens,
                session_total_tokens_before_compaction=session_before_reset,
                session_total_tokens_after_compaction=0,
            )
        except Exception as exc:
            self._logger.exception("history compaction failed", exc_info=exc)
            if notify:
                updates.append("error compacting")
            return CompactionResult(
                updates=updates,
                performed=False,
                tokens_used=compaction_tokens,
                session_total_tokens_before_compaction=None,
                session_total_tokens_after_compaction=self._session_state.current_tokens(session_id),
            )

    def _should_use_responses_compaction_endpoint(self, session_id: str, responses_state_mode: str) -> bool:
        if not self._llm_client.is_responses_provider():
            return False
        if responses_state_mode != "previous_response_id":
            return False
        return self._session_state.has_previous_response_id(session_id)

    @staticmethod
    def _extract_compaction_text(items: Sequence[dict[str, object]]) -> str:
        parts: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for entry in content:
                if not isinstance(entry, dict):
                    continue
                text = entry.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        return "".join(parts).strip()

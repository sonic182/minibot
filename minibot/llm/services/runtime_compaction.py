from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart

_TRANSCRIPT_CHARS_PER_MESSAGE = 2000
_SUMMARY_SYSTEM_PROMPT = (
    "You are compacting the working transcript of an agent that is still mid-task. Return a "
    "concise but complete summary that preserves the goal, every finding and decision reached so "
    "far, and what is still pending. The agent continues from your summary alone, so anything you "
    "leave out is lost. Do not include preamble."
)
_SUMMARY_USER_PREFIX = "Compact this working transcript:\n\n"


@dataclass(frozen=True)
class CompactionOutcome:
    performed: bool
    response_id: str | None = None
    summary: str = ""


class RuntimeCompactor:
    """Compacts an in-flight ``AgentState`` when the context window starts filling up.

    ``HistoryCompactionService`` already does this for the main chat, but between turns and over
    persisted memory. A delegated run never reaches that layer: ``spawn_task`` and
    ``invoke_agent`` can loop for dozens of tool-calling steps inside a single
    ``AgentRuntime.run()``, so the same strategy has to apply to the live message list.
    """

    def __init__(self, *, llm_client: Any, threshold_tokens: int, logger: logging.Logger) -> None:
        self._llm_client = llm_client
        self._threshold_tokens = threshold_tokens
        self._logger = logger

    @property
    def threshold_tokens(self) -> int:
        return self._threshold_tokens

    def should_compact(self, input_tokens: int | None) -> bool:
        """``input_tokens`` is the pressure signal, not the accumulated total: it is what the
        provider actually had in context for the last call."""
        return isinstance(input_tokens, int) and input_tokens >= self._threshold_tokens

    async def compact(
        self,
        state: AgentState,
        *,
        previous_response_id: str | None,
        prompt_cache_key: str | None,
    ) -> CompactionOutcome:
        try:
            if self._can_compact_natively(previous_response_id):
                assert previous_response_id is not None
                outcome = await self._compact_natively(previous_response_id, prompt_cache_key)
            else:
                outcome = await self._compact_via_summary(state, prompt_cache_key)
        except Exception as exc:  # noqa: BLE001
            # Never take the run down over this: the step limits are still the hard ceiling.
            self._logger.warning(
                "runtime compaction failed; continuing uncompacted",
                extra={"error": str(exc)},
                exc_info=True,
            )
            return CompactionOutcome(performed=False)
        if outcome.performed and outcome.summary:
            _rewrite_state(state, outcome.summary)
        return outcome

    def _can_compact_natively(self, previous_response_id: str | None) -> bool:
        return (
            bool(previous_response_id)
            and self._llm_client.supports_responses_compaction()
            and self._llm_client.responses_state_mode() == "previous_response_id"
        )

    async def _compact_natively(self, previous_response_id: str, prompt_cache_key: str | None) -> CompactionOutcome:
        compacted = await self._llm_client.compact_response(
            previous_response_id=previous_response_id,
            prompt_cache_key=f"{prompt_cache_key}:compact" if prompt_cache_key else None,
        )
        summary = _text_from_output(compacted.output) or "Conversation compacted."
        return CompactionOutcome(performed=True, response_id=compacted.response_id, summary=summary)

    async def _compact_via_summary(self, state: AgentState, prompt_cache_key: str | None) -> CompactionOutcome:
        transcript = _transcript(state)
        if not transcript:
            return CompactionOutcome(performed=False)
        generation = await self._llm_client.generate(
            [],
            f"{_SUMMARY_USER_PREFIX}{transcript}",
            user_content=None,
            tools=[],
            tool_context=None,
            prompt_cache_key=f"{prompt_cache_key}:compact" if prompt_cache_key else None,
            previous_response_id=None,
            system_prompt_override=_SUMMARY_SYSTEM_PROMPT,
        )
        summary = generation.payload if isinstance(generation.payload, str) else str(generation.payload or "")
        if not summary.strip():
            return CompactionOutcome(performed=False)
        # No response_id: the summary lives in the local messages, so the next step must render
        # them in full rather than chain onto a response that still holds the old context.
        return CompactionOutcome(performed=True, response_id=None, summary=summary.strip())


def build_compactor(
    *,
    llm_client: Any,
    threshold_tokens: int | None,
    logger: logging.Logger,
) -> RuntimeCompactor | None:
    """``None`` whenever the threshold is unknown — a model with no catalog entry gives us
    nothing to compare against, and guessing a window is worse than not compacting."""
    if not threshold_tokens or threshold_tokens <= 0:
        return None
    return RuntimeCompactor(llm_client=llm_client, threshold_tokens=threshold_tokens, logger=logger)


def threshold_from_context_limit(context_limit: int | None, ratio: float) -> int | None:
    if not context_limit or ratio <= 0:
        return None
    return max(1, int(context_limit * ratio))


def _rewrite_state(state: AgentState, summary: str) -> None:
    """Keep the system prompt and the original task, replace everything else with the summary."""
    head: list[AgentMessage] = []
    for message in state.messages:
        if message.role == "system":
            head.append(message)
            continue
        if message.role == "user":
            head.append(message)
            break
    state.messages[:] = [
        *head,
        AgentMessage(role="assistant", content=[MessagePart(type="text", text=summary)]),
    ]


def _transcript(state: AgentState) -> str:
    lines: list[str] = []
    for message in state.messages:
        if message.role not in ("assistant", "tool") or message.name == "pre_response":
            continue
        text = _message_text(message)
        if not text:
            continue
        label = "assistant" if message.role == "assistant" else f"tool:{message.name or 'unknown'}"
        lines.append(f"[{label}] {text[:_TRANSCRIPT_CHARS_PER_MESSAGE]}")
    return "\n".join(lines)


def _message_text(message: AgentMessage) -> str:
    chunks: list[str] = []
    for part in message.content:
        if part.type == "text" and part.text:
            chunks.append(part.text.strip())
        elif part.value is not None:
            chunks.append(json.dumps(part.value, ensure_ascii=False, default=str))
    return " ".join(chunk for chunk in chunks if chunk)


def _text_from_output(items: Any) -> str:
    parts: list[str] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for entry in content:
            if isinstance(entry, dict) and isinstance(entry.get("text"), str) and entry["text"]:
                parts.append(entry["text"])
    return "".join(parts).strip()

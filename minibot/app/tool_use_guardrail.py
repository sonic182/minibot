from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from minibot.core.agent_runtime import AgentMessage, AgentState
from minibot.app.tool_guardrail_validator import ToolGuardrailValidator, _FailResult, _RetryResult, _ValidResult
from minibot.llm.tools.base import ToolBinding, ToolContext


@dataclass(frozen=True)
class GuardrailDecision:
    requires_retry: bool
    retry_system_prompt_suffix: str | None = None
    resolved_render_text: str | None = None
    suggested_tool: str | None = None
    suggested_path: str | None = None
    reason: str | None = None
    attempts: int = 1
    tokens_used: int = 0


class ToolUseGuardrail(Protocol):
    async def apply(
        self,
        *,
        session_id: str,
        user_text: str,
        tool_context: ToolContext,
        state: AgentState,
        system_prompt: str,
        prompt_cache_key: str | None,
    ) -> GuardrailDecision: ...


class NoopToolUseGuardrail:
    async def apply(self, **_: Any) -> GuardrailDecision:
        return GuardrailDecision(requires_retry=False)


class LLMClassifierToolUseGuardrail:
    def __init__(
        self,
        *,
        llm_client: Any,
        tools: Sequence[ToolBinding],
        validation_max_attempts: int = 3,
        fail_open: bool = True,
    ) -> None:
        self._llm_client = llm_client
        self._tools = list(tools)
        self._validation_max_attempts = max(1, validation_max_attempts)
        self._fail_open = fail_open
        self._logger = logging.getLogger("minibot.tool_use_guardrail")

    async def apply(
        self,
        *,
        session_id: str,
        user_text: str,
        tool_context: ToolContext,
        state: AgentState,
        system_prompt: str,
        prompt_cache_key: str | None,
    ) -> GuardrailDecision:
        _ = tool_context
        if not self._tools:
            return GuardrailDecision(requires_retry=False, attempts=0)

        tool_names = [binding.tool.name for binding in self._tools]
        prompt_patch: str | None = None
        tokens = 0
        validator = ToolGuardrailValidator(max_attempts=self._validation_max_attempts)
        attempts = 0
        try:
            history = _classifier_history(state)
            while True:
                classifier_prompt = self._classifier_prompt(
                    tool_names=tool_names,
                    user_text=user_text,
                    prompt_patch=prompt_patch,
                )
                generation = await self._llm_client.generate(
                    history,
                    classifier_prompt,
                    user_content=None,
                    tools=[],
                    tool_context=None,
                    prompt_cache_key=f"{prompt_cache_key}:tool-requirement" if prompt_cache_key else None,
                    previous_response_id=None,
                    system_prompt_override="You are a strict tool-routing classifier. Output JSON only.",
                    include_provider_native_tools=False,
                )
                raw_tokens = getattr(generation, "total_tokens", None)
                if isinstance(raw_tokens, int) and raw_tokens > 0:
                    tokens += raw_tokens
                result = validator.receive(generation.payload)
                attempts = result.attempts
                if isinstance(result, _ValidResult):
                    payload = validator.valid_payload(result)
                    break
                if isinstance(result, _RetryResult):
                    prompt_patch = result.prompt_patch.strip()
                    self._logger.warning(
                        "tool requirement classifier output invalid; retrying",
                        extra={
                            "session_id": session_id,
                            "attempts": result.attempts,
                            "reason": result.reason,
                            "retry_patch_present": bool(prompt_patch),
                        },
                    )
                    continue
                if isinstance(result, _FailResult):
                    self._logger.warning(
                        "tool requirement classifier validation failed",
                        extra={
                            "session_id": session_id,
                            "attempts": result.attempts,
                            "reason": result.reason,
                            "fail_open": self._fail_open,
                        },
                    )
                    reason = f"guardrail_validation_failed: {result.reason}"
                    if self._fail_open:
                        return GuardrailDecision(
                            requires_retry=False,
                            reason=reason,
                            attempts=result.attempts,
                            tokens_used=tokens,
                        )
                    return GuardrailDecision(
                        requires_retry=True,
                        retry_system_prompt_suffix=self._tool_retry_suffix(),
                        reason=reason,
                        attempts=result.attempts,
                        tokens_used=tokens,
                    )

            if not payload.requires_tools:
                self._logger.debug(
                    "tool requirement classifier completed",
                    extra={
                        "session_id": session_id,
                        "attempts": attempts,
                        "requires_retry": False,
                        "reason": payload.reason,
                    },
                )
                return GuardrailDecision(
                    requires_retry=False,
                    reason=payload.reason,
                    attempts=attempts,
                    tokens_used=tokens,
                )

            suggested_tool = payload.suggested_tool if payload.suggested_tool in tool_names else None
            suggested_path = payload.path

            self._logger.debug(
                "tool requirement classifier requires retry",
                extra={
                    "session_id": session_id,
                    "attempts": attempts,
                    "suggested_tool": suggested_tool,
                    "suggested_path": suggested_path,
                    "reason": payload.reason,
                },
            )
            return GuardrailDecision(
                requires_retry=True,
                retry_system_prompt_suffix=self._tool_retry_suffix(),
                suggested_tool=suggested_tool,
                suggested_path=suggested_path,
                reason=payload.reason,
                attempts=attempts,
                tokens_used=tokens,
            )
        except Exception:
            self._logger.exception("tool requirement classifier failed; skipping guardrail")
            return GuardrailDecision(requires_retry=False, attempts=attempts, tokens_used=tokens)

    @staticmethod
    def _classifier_prompt(*, tool_names: Sequence[str], user_text: str, prompt_patch: str | None) -> str:
        prompt = (
            "Decide whether the user's request requires executing at least one tool before answering.\n"
            "Return only a JSON object with keys: requires_tools, suggested_tool, path, reason.\n"
            "Use available tool names exactly when suggested_tool is not null.\n"
            "If the user asks to edit/refactor an existing file, suggested_tool should be apply_patch.\n"
            "Set suggested_tool/path/reason to null when not applicable.\n\n"
            f"Available tools: {', '.join(tool_names)}\n"
            f"User request:\n{user_text}"
        )
        if prompt_patch:
            return f"{prompt}\n\nValidation feedback:\n{prompt_patch}"
        return prompt

    @staticmethod
    def _tool_retry_suffix() -> str:
        return (
            "Tool policy reminder: this request requires using tools before final answer. "
            "Call the relevant tool now, then provide the final answer from tool output. "
            "Do not answer with intent statements like 'I will check'."
        )

@dataclass(frozen=True)
class _ClassifierHistoryEntry:
    role: str
    content: str


def _classifier_history(state: AgentState) -> list[_ClassifierHistoryEntry]:
    entries: list[_ClassifierHistoryEntry] = []
    for message in state.messages:
        if message.role not in {"user", "assistant", "tool"}:
            continue
        content = _message_content_as_text(message)
        if content.strip():
            entries.append(_ClassifierHistoryEntry(role=message.role, content=content))
    return entries


def _message_content_as_text(message: AgentMessage) -> str:
    parts: list[str] = []
    for part in message.content:
        if part.text is not None and part.text.strip():
            parts.append(part.text)
        elif part.value is not None:
            parts.append(str(part.value))
    if parts:
        return "\n".join(parts)
    if isinstance(message.raw_content, str):
        return message.raw_content
    if message.raw_content is not None:
        return str(message.raw_content)
    return ""

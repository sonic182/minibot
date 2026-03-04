from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from ratchet_sm import FailAction, RetryAction, ValidAction

from minibot.core.agent_runtime import AgentMessage, AgentState
from minibot.app.tool_guardrail_validator import ToolGuardrailValidator
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.shared.path_utils import normalize_path_separators


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
                    response_schema=self._schema(),
                    prompt_cache_key=f"{prompt_cache_key}:tool-requirement" if prompt_cache_key else None,
                    previous_response_id=None,
                    system_prompt_override="You are a strict tool-routing classifier. Output JSON only.",
                )
                raw_tokens = getattr(generation, "total_tokens", None)
                if isinstance(raw_tokens, int) and raw_tokens > 0:
                    tokens += raw_tokens
                action = validator.receive(generation.payload)
                attempts += 1
                if isinstance(action, ValidAction):
                    payload = validator.valid_payload(action)
                    break
                if isinstance(action, RetryAction):
                    prompt_patch = (action.prompt_patch or "").strip()
                    self._logger.warning(
                        "tool requirement classifier output invalid; retrying",
                        extra={
                            "session_id": session_id,
                            "attempts": action.attempts,
                            "reason": action.reason,
                            "errors": list(action.errors),
                            "retry_patch_present": bool(prompt_patch),
                        },
                    )
                    continue
                if isinstance(action, FailAction):
                    self._logger.warning(
                        "tool requirement classifier validation failed",
                        extra={
                            "session_id": session_id,
                            "attempts": action.attempts,
                            "reason": action.reason,
                            "fail_open": self._fail_open,
                        },
                    )
                    reason = f"guardrail_validation_failed: {action.reason}"
                    if self._fail_open:
                        return GuardrailDecision(
                            requires_retry=False,
                            reason=reason,
                            attempts=action.attempts,
                            tokens_used=tokens,
                        )
                    return GuardrailDecision(
                        requires_retry=True,
                        retry_system_prompt_suffix=self._tool_retry_suffix(),
                        reason=reason,
                        attempts=action.attempts,
                        tokens_used=tokens,
                    )
                reason = f"guardrail_validation_failed: unsupported_action:{type(action).__name__}"
                if self._fail_open:
                    return GuardrailDecision(
                        requires_retry=False,
                        reason=reason,
                        attempts=attempts,
                        tokens_used=tokens,
                    )
                return GuardrailDecision(
                    requires_retry=True,
                    retry_system_prompt_suffix=self._tool_retry_suffix(),
                    reason=reason,
                    attempts=attempts,
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

            direct_result = await self._try_direct_delete(
                user_text=user_text,
                tool_context=tool_context,
                suggested_tool=suggested_tool,
                suggested_path=suggested_path,
            )
            if direct_result is not None:
                self._logger.debug(
                    "tool requirement classifier resolved action directly",
                    extra={
                        "session_id": session_id,
                        "attempts": attempts,
                        "suggested_tool": suggested_tool,
                        "suggested_path": suggested_path,
                        "reason": payload.reason,
                    },
                )
                return GuardrailDecision(
                    requires_retry=False,
                    resolved_render_text=direct_result,
                    suggested_tool=suggested_tool,
                    suggested_path=suggested_path,
                    reason=payload.reason,
                    attempts=attempts,
                    tokens_used=tokens,
                )

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

    async def _try_direct_delete(
        self,
        *,
        user_text: str,
        tool_context: ToolContext,
        suggested_tool: str | None,
        suggested_path: str | None,
    ) -> str | None:
        if suggested_tool not in {"delete_file", "filesystem"}:
            return None
        binding = next((item for item in self._tools if item.tool.name == "filesystem"), None)
        if binding is None:
            return None
        candidates = _extract_delete_path_candidates(user_text)
        if suggested_path is not None and suggested_path not in candidates:
            candidates.insert(0, suggested_path)
        if not candidates:
            return None
        for candidate in candidates:
            try:
                raw_result = await binding.handler({"action": "delete", "path": candidate}, tool_context)
                if not isinstance(raw_result, dict):
                    continue
                if int(raw_result.get("deleted_count") or 0) > 0:
                    return str(raw_result.get("message") or f"Deleted file successfully: {candidate}")
            except Exception:
                self._logger.exception("direct filesystem delete failed", extra={"path": candidate})
        return None

    @staticmethod
    def _schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "requires_tools": {"type": "boolean"},
                "suggested_tool": {"type": ["string", "null"]},
                "path": {"type": ["string", "null"]},
                "reason": {"type": ["string", "null"]},
            },
            "required": ["requires_tools", "suggested_tool", "path", "reason"],
            "additionalProperties": False,
        }


def _extract_delete_path_candidates(text: str) -> list[str]:
    candidates: list[str] = []

    def _normalize(value: str) -> str | None:
        value = value.strip().strip('"').strip("'")
        if not value:
            return None
        if value.startswith("./"):
            value = value[2:]
        return normalize_path_separators(value)

    for item in re.findall(r"['\"]([^'\"]+)['\"]", text):
        n = _normalize(item)
        if n and n not in candidates:
            candidates.append(n)

    for item in re.findall(r"(?:\.?/?[a-zA-Z0-9_-]+/)+[a-zA-Z0-9_.-]+\.[a-zA-Z0-9]{1,10}", text):
        n = _normalize(item)
        if n and n not in candidates:
            candidates.append(n)

    m = re.search(r"([a-zA-Z0-9_.-]+\.[a-zA-Z0-9]{1,10})", text)
    if m:
        n = _normalize(m.group(1))
        if n and n not in candidates:
            candidates.append(n)

        return candidates


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

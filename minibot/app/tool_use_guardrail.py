from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from minibot.core.agent_runtime import AgentState
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.shared.path_utils import normalize_path_separators


@dataclass(frozen=True)
class GuardrailDecision:
    requires_retry: bool
    retry_system_prompt_suffix: str | None = None
    resolved_render_text: str | None = None
    suggested_tool: str | None = None
    suggested_path: str | None = None
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
    ) -> None:
        self._llm_client = llm_client
        self._tools = list(tools)
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
            return GuardrailDecision(requires_retry=False)

        tool_names = [binding.tool.name for binding in self._tools]
        classifier_prompt = (
            "Decide whether the user's request requires executing at least one tool before answering. "
            "Use the available tool names exactly as given. "
            "Return structured output only.\n\n"
            f"Available tools: {', '.join(tool_names)}\n"
            f"User request:\n{user_text}"
        )
        try:
            history = [m for m in state.messages if m.role in {"user", "assistant", "tool"}]
            generation = await self._llm_client.generate(
                history,
                classifier_prompt,
                user_content=None,
                tools=[],
                tool_context=None,
                response_schema=self._schema(),
                prompt_cache_key=f"{prompt_cache_key}:tool-requirement" if prompt_cache_key else None,
                previous_response_id=None,
                system_prompt_override="You are a strict tool-routing classifier.",
            )
            raw_tokens = getattr(generation, "total_tokens", None)
            tokens = raw_tokens if isinstance(raw_tokens, int) and raw_tokens > 0 else 0
            payload = generation.payload
            payload_obj: dict[str, Any] | None = None
            if isinstance(payload, dict):
                payload_obj = payload
            elif isinstance(payload, str) and payload.strip():
                parsed = json.loads(payload.strip())
                if isinstance(parsed, dict):
                    payload_obj = parsed
            if not payload_obj or not bool(payload_obj.get("requires_tools", False)):
                return GuardrailDecision(requires_retry=False, tokens_used=tokens)

            raw_tool = payload_obj.get("suggested_tool")
            suggested_tool = raw_tool if isinstance(raw_tool, str) and raw_tool in tool_names else None
            raw_path = payload_obj.get("path")
            suggested_path = raw_path.strip() if isinstance(raw_path, str) and raw_path.strip() else None

            direct_result = await self._try_direct_delete(
                user_text=user_text,
                tool_context=tool_context,
                suggested_tool=suggested_tool,
                suggested_path=suggested_path,
            )
            if direct_result is not None:
                return GuardrailDecision(
                    requires_retry=False,
                    resolved_render_text=direct_result,
                    tokens_used=tokens,
                )

            self._logger.debug(
                "tool requirement classifier requires retry",
                extra={"suggested_tool": suggested_tool, "suggested_path": suggested_path},
            )
            return GuardrailDecision(
                requires_retry=True,
                retry_system_prompt_suffix=(
                    "Tool policy reminder: this request requires using tools before final answer. "
                    "Call the relevant tool now, then provide the final answer from tool output. "
                    "Do not answer with intent statements like 'I will check'."
                ),
                suggested_tool=suggested_tool,
                suggested_path=suggested_path,
                tokens_used=tokens,
            )
        except Exception:
            self._logger.exception("tool requirement classifier failed; skipping guardrail")
            return GuardrailDecision(requires_retry=False)

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
            },
            "required": ["requires_tools", "suggested_tool", "path"],
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

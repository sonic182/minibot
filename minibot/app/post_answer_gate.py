from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from minibot.core.agent_runtime import AgentState
from minibot.core.memory import MemoryEntry
from minibot.llm.provider_factory import LLMClient
from minibot.llm.tools.base import ToolBinding
from minibot.shared.parse_utils import parse_json_maybe_python_object


@dataclass(frozen=True)
class GateDecision:
    action: Literal["accept", "continue_with_tools", "ask_clarification", "fail_unsolved", "guardrail_block"]
    reason_code: str
    requires_tools: bool
    suggested_tool: str | None
    unsatisfiable: bool
    notes: str | None
    tokens_used: int


class PostAnswerGate:
    def __init__(self, llm_client: LLMClient, tools: Sequence[ToolBinding]) -> None:
        self._llm_client = llm_client
        self._tools = list(tools)
        self._logger = logging.getLogger("minibot.post_answer_gate")

    async def evaluate(
        self,
        *,
        session_id: str,
        history: Sequence[MemoryEntry],
        user_text: str,
        assistant_response: str,
        state: AgentState | None,
        prompt_cache_key: str | None,
    ) -> GateDecision:
        tool_messages_count = self._count_tool_messages(state) if state else 0
        tool_names = [binding.tool.name for binding in self._tools]

        gate_prompt = self._build_gate_prompt(
            user_text=user_text,
            assistant_response=assistant_response,
            tool_names=tool_names,
            tool_messages_count=tool_messages_count,
        )

        try:
            generation = await self._llm_client.generate(
                history,
                gate_prompt,
                user_content=None,
                tools=[],
                tool_context=None,
                response_schema=self._gate_decision_schema(),
                prompt_cache_key=f"{prompt_cache_key}:post-answer-gate" if prompt_cache_key else None,
                previous_response_id=None,
                system_prompt_override="You are a strict response validation classifier.",
            )
            tokens_used = int(generation.total_tokens or 0)

            payload = generation.payload
            payload_obj: dict[str, Any] | None = None
            if isinstance(payload, dict):
                payload_obj = payload
            elif isinstance(payload, str):
                stripped = payload.strip()
                if stripped:
                    parsed = parse_json_maybe_python_object(stripped)
                    if isinstance(parsed, dict):
                        payload_obj = parsed

            if not payload_obj:
                self._logger.warning("gate classifier returned invalid payload; defaulting to accept")
                return GateDecision(
                    action="accept",
                    reason_code="classifier_invalid_payload",
                    requires_tools=False,
                    suggested_tool=None,
                    unsatisfiable=False,
                    notes=None,
                    tokens_used=tokens_used,
                )

            action = payload_obj.get("action", "accept")
            if action not in {
                "accept",
                "continue_with_tools",
                "ask_clarification",
                "fail_unsolved",
                "guardrail_block",
            }:
                action = "accept"

            reason_code = str(payload_obj.get("reason_code", "unknown"))
            requires_tools = bool(payload_obj.get("requires_tools", False))
            raw_suggested_tool = payload_obj.get("suggested_tool")
            suggested_tool = (
                raw_suggested_tool
                if isinstance(raw_suggested_tool, str) and raw_suggested_tool in tool_names
                else None
            )
            unsatisfiable = bool(payload_obj.get("unsatisfiable", False))
            raw_notes = payload_obj.get("notes")
            notes = raw_notes.strip() if isinstance(raw_notes, str) and raw_notes.strip() else None

            self._logger.debug(
                "gate decision computed",
                extra={
                    "action": action,
                    "reason_code": reason_code,
                    "requires_tools": requires_tools,
                    "suggested_tool": suggested_tool,
                    "unsatisfiable": unsatisfiable,
                    "notes": notes,
                    "tokens_used": tokens_used,
                },
            )

            return GateDecision(
                action=action,
                reason_code=reason_code,
                requires_tools=requires_tools,
                suggested_tool=suggested_tool,
                unsatisfiable=unsatisfiable,
                notes=notes,
                tokens_used=tokens_used,
            )
        except Exception:
            self._logger.exception("gate evaluation failed; defaulting to accept")
            return GateDecision(
                action="accept",
                reason_code="classifier_exception",
                requires_tools=False,
                suggested_tool=None,
                unsatisfiable=False,
                notes=None,
                tokens_used=0,
            )

    @staticmethod
    def _build_gate_prompt(
        *,
        user_text: str,
        assistant_response: str,
        tool_names: Sequence[str],
        tool_messages_count: int,
    ) -> str:
        return (
            "Validate whether the assistant's response is complete and appropriate, or requires action.\n\n"
            f"User request:\n{user_text}\n\n"
            f"Assistant response:\n{assistant_response}\n\n"
            f"Tools executed: {tool_messages_count}\n"
            f"Available tools: {', '.join(tool_names) if tool_names else 'none'}\n\n"
            "Decide the appropriate action:\n"
            "- accept: response is complete and appropriate\n"
            "- continue_with_tools: response is a placeholder/intent statement and requires tool execution\n"
            "- ask_clarification: user request is ambiguous and needs clarification\n"
            "- fail_unsolved: request is clearly unsatisfiable or outside capabilities\n"
            "- guardrail_block: request violates safety/policy guardrails\n\n"
            "Return structured output only."
        )

    @staticmethod
    def _gate_decision_schema() -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["accept", "continue_with_tools", "ask_clarification", "fail_unsolved", "guardrail_block"],
                },
                "reason_code": {"type": "string"},
                "requires_tools": {"type": "boolean"},
                "suggested_tool": {"type": ["string", "null"]},
                "unsatisfiable": {"type": "boolean"},
                "notes": {"type": ["string", "null"]},
            },
            "required": ["action", "reason_code", "requires_tools", "suggested_tool", "unsatisfiable", "notes"],
            "additionalProperties": False,
        }

    @staticmethod
    def _count_tool_messages(state: AgentState | None) -> int:
        if state is None:
            return 0
        return sum(1 for message in state.messages if message.role == "tool")

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from minibot.core.agent_runtime import AgentState


@dataclass(frozen=True)
class DelegationTraceResult:
    trace: list[dict[str, Any]]
    fallback_used: bool
    unresolved: bool


def count_tool_messages(state: AgentState) -> int:
    return sum(1 for message in state.messages if message.role == "tool")


def extract_delegation_trace(state: AgentState) -> DelegationTraceResult:
    trace: list[dict[str, Any]] = []
    fallback_used = False
    last_result_status: str | None = None
    last_should_answer: bool | None = None
    saw_invoke = False
    for message in state.messages:
        if message.role != "tool" or message.name != "invoke_agent":
            continue
        if not message.content:
            continue
        part = message.content[0]
        if part.type != "json" or not isinstance(part.value, dict):
            continue
        saw_invoke = True
        agent = str(part.value.get("agent") or "")
        ok = bool(part.value.get("ok", False))
        error = part.value.get("error")
        result_status = part.value.get("result_status")
        delegated_should_answer = part.value.get("should_answer_to_user")
        if not ok:
            fallback_used = True
        if isinstance(result_status, str):
            last_result_status = result_status
        else:
            last_result_status = None
        if isinstance(delegated_should_answer, bool):
            last_should_answer = delegated_should_answer
        else:
            last_should_answer = None
        trace_entry: dict[str, Any] = {
            "agent": "minibot",
            "decision": "invoke_agent",
            "target": agent or None,
            "ok": ok,
        }
        if isinstance(result_status, str) and result_status.strip():
            trace_entry["result_status"] = result_status
        if isinstance(delegated_should_answer, bool):
            trace_entry["should_answer_to_user"] = delegated_should_answer
        if isinstance(error, str) and error.strip():
            trace_entry["error"] = error
        trace.append(trace_entry)

    unresolved = False
    if saw_invoke:
        if last_result_status in {"invalid_result", "not_user_answerable"}:
            unresolved = True
        if last_should_answer is False:
            unresolved = True

    return DelegationTraceResult(
        trace=trace,
        fallback_used=fallback_used,
        unresolved=unresolved,
    )

from __future__ import annotations

from pathlib import Path

import pytest

from minibot.app.agent_registry import AgentRegistry
from minibot.core.agents import AgentSpec
from minibot.llm.tools.agent_delegate import AgentDelegateTool
from minibot.llm.tools.base import ToolContext


class _UnusedFactory:
    def create_for_agent(self, spec: AgentSpec):
        raise AssertionError(f"unexpected llm factory call for {spec.name}")


def _registry() -> AgentRegistry:
    return AgentRegistry(
        [
            AgentSpec(
                name="worker",
                description="worker",
                system_prompt="You are worker.",
                source_path=Path("worker.md"),
            )
        ]
    )


@pytest.mark.asyncio
async def test_invoke_agent_defaults_to_async_error_when_jobs_disabled() -> None:
    tool = AgentDelegateTool(
        registry=_registry(),
        llm_factory=_UnusedFactory(),
        tools=[],
        default_timeout_seconds=30,
        job_service=None,
    )

    result = await tool._invoke_agent(
        {"agent_name": "worker", "task": "do work"},
        ToolContext(owner_id="owner", channel="console", chat_id=1, user_id=2),
    )

    assert result == {
        "ok": False,
        "agent": "worker",
        "error_code": "job_service_unavailable",
        "error": "async delegated jobs are not enabled",
    }


@pytest.mark.asyncio
async def test_invoke_agent_sync_mode_still_uses_inline_path(monkeypatch: pytest.MonkeyPatch) -> None:
    tool = AgentDelegateTool(
        registry=_registry(),
        llm_factory=_UnusedFactory(),
        tools=[],
        default_timeout_seconds=30,
        job_service=None,
    )
    seen: dict[str, object] = {}

    async def _fake_run_agent(*, spec, task, details, context):
        seen.update({"spec": spec.name, "task": task, "details": details, "context": context})
        return {"ok": True, "mode": "sync"}

    monkeypatch.setattr(tool, "run_agent", _fake_run_agent)

    result = await tool._invoke_agent(
        {"agent_name": "worker", "task": "do work", "context": "extra", "mode": "sync"},
        ToolContext(owner_id="owner", channel="console", chat_id=1, user_id=2),
    )

    assert result == {"ok": True, "mode": "sync"}
    assert seen["spec"] == "worker"
    assert seen["task"] == "do work"
    assert seen["details"] == "extra"


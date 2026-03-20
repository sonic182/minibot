from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from llm_async.models import Tool

from minibot.app.agent_registry import AgentRegistry
from minibot.core.agents import AgentSpec
from minibot.llm.tools.agent_delegate import AgentDelegateTool
from minibot.llm.tools.base import ToolBinding, ToolContext


class _StubLLMClient:
    def provider_name(self) -> str:
        return "openrouter"

    def model_name(self) -> str:
        return "z-ai/glm-4.7"

    def max_tool_iterations(self) -> int:
        return 8

    def responses_state_mode(self) -> str:
        return "full_messages"

    def prompt_cache_enabled(self) -> bool:
        return False


class _StubLLMFactory:
    def __init__(self) -> None:
        self.client = _StubLLMClient()

    def create_for_agent(self, _: AgentSpec) -> _StubLLMClient:
        return self.client


@pytest.mark.asyncio
async def test_invoke_agent_returns_timeout_payload_when_runtime_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = AgentSpec(
        name="playwright_mcp_agent",
        description="browser specialist",
        system_prompt="use tools",
        source_path=Path("agents/browser_agent.md"),
        tools_allow=["mcp_playwright-cli__*"],
        mcp_servers=["playwright-cli"],
    )
    registry = AgentRegistry([spec])
    factory = _StubLLMFactory()
    runtime_calls = 0

    class _TimeoutRuntime:
        def __init__(self, **_: Any) -> None:
            pass

        async def run(self, **_: Any) -> Any:
            nonlocal runtime_calls
            runtime_calls += 1
            raise TimeoutError("provider timed out")

    monkeypatch.setattr("minibot.llm.tools.agent_delegate.AgentRuntime", _TimeoutRuntime)

    tool = AgentDelegateTool(
        registry=registry,
        llm_factory=cast(Any, factory),
        tools=[
            ToolBinding(
                tool=Tool(
                    name="mcp_playwright-cli__browser_navigate",
                    description="navigate",
                    parameters={"type": "object"},
                ),
                handler=cast(Any, lambda *_: None),
            )
        ],
        default_timeout_seconds=180,
        delegated_tool_call_policy="auto",
    )

    result = await tool._invoke_agent(
        {"agent_name": "playwright_mcp_agent", "task": "check page"},
        ToolContext(owner_id="primary"),
    )

    assert runtime_calls == 1
    assert result["ok"] is False
    assert result["result_status"] == "timeout"
    assert result["should_continue"] is False
    assert result["error_code"] == "delegated_timeout"
    assert result["provider"] == "openrouter"
    assert result["model"] == "z-ai/glm-4.7"

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from llm_async.models import Tool

from minibot.adapters.config.schema import Settings
from minibot.app.agent_registry import AgentRegistry
from minibot.app.agent_runtime import RuntimeResult
from minibot.app.llm_client_factory import ProviderOption
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart
from minibot.core.agents import AgentSpec
from minibot.llm.errors import ProviderHTTPError
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
        self.settings = Settings()

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
    assert result["error_code"] == "delegated_timeout"
    assert result["provider"] == "openrouter"
    assert result["model"] == "z-ai/glm-4.7"


@pytest.mark.asyncio
async def test_invoke_agent_reports_quota_error_with_dedicated_error_code(monkeypatch: pytest.MonkeyPatch) -> None:
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

    class _QuotaFailingRuntime:
        def __init__(self, **_: Any) -> None:
            pass

        async def run(self, **_: Any) -> Any:
            raise ProviderHTTPError(429, '{"code":"insufficient_quota","error":"out of credits"}')

    monkeypatch.setattr("minibot.llm.tools.agent_delegate.AgentRuntime", _QuotaFailingRuntime)

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

    assert result["ok"] is False
    assert result["error_code"] == "delegated_agent_quota_exceeded"
    assert result["error"] == "out of credits"


@pytest.mark.asyncio
async def test_invoke_agent_timeout_salvages_partial_work_and_honours_agent_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = AgentSpec(
        name="prospector",
        description="prospecting specialist",
        system_prompt="sweep the market",
        source_path=Path("agents/prospector.md"),
        timeout_seconds=900,
        tools_allow=["bash"],
    )
    registry = AgentRegistry([spec])
    seen_limits: list[int] = []

    class _PartialWorkRuntime:
        def __init__(self, **kwargs: Any) -> None:
            seen_limits.append(kwargs["limits"].timeout_seconds)

        async def run(self, *, state: Any, **_: Any) -> Any:
            state.messages.append(
                AgentMessage(role="assistant", content=[MessagePart(type="text", text="creating the leads table")])
            )
            state.messages.append(
                AgentMessage(
                    role="tool",
                    name="bash",
                    tool_call_id="call_1",
                    content=[MessagePart(type="json", value={"stdout": "3 rows inserted"})],
                )
            )
            raise TimeoutError("provider timed out")

    monkeypatch.setattr("minibot.llm.tools.agent_delegate.AgentRuntime", _PartialWorkRuntime)

    tool = AgentDelegateTool(
        registry=registry,
        llm_factory=cast(Any, _StubLLMFactory()),
        tools=[
            ToolBinding(
                tool=Tool(name="bash", description="run", parameters={"type": "object"}),
                handler=cast(Any, lambda *_: None),
            )
        ],
        default_timeout_seconds=180,
        delegated_tool_call_policy="auto",
    )

    result = await tool._invoke_agent(
        {"agent_name": "prospector", "task": "sweep Doral"},
        ToolContext(owner_id="primary"),
    )

    assert seen_limits == [900]
    assert result["result_status"] == "timeout"
    assert result["tool_messages_count"] == 1
    assert "creating the leads table" in cast(str, result["result"])
    assert "3 rows inserted" in cast(str, result["result"])


class _SpecCapturingFactory:
    """Records the spec each delegation resolves, plus the providers it may target."""

    def __init__(self, providers: list[ProviderOption]) -> None:
        self.client = _StubLLMClient()
        self.specs: list[AgentSpec] = []
        self.settings = Settings()
        self._providers = providers

    def create_for_agent(self, spec: AgentSpec) -> _StubLLMClient:
        self.specs.append(spec)
        return self.client

    def available_providers(self) -> list[ProviderOption]:
        return list(self._providers)


def _opencode_go_option() -> ProviderOption:
    return ProviderOption(
        name="opencode_go",
        api_format="openai_responses",
        base_url="https://opencode.ai/zen/go/v1",
        models=("deepseek-v3.6",),
    )


@pytest.mark.asyncio
async def test_invoke_agent_retargets_one_call_at_another_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = AgentSpec(
        name="general_agent",
        description="generalist",
        system_prompt="do the work",
        source_path=Path("agents/general.md"),
        model_provider="openai",
        model="gpt-4o-mini",
        reasoning_effort="low",
        max_new_tokens=4096,
        context_limit=128000,
    )
    factory = _SpecCapturingFactory([_opencode_go_option()])

    class _AnsweringRuntime:
        def __init__(self, **_: Any) -> None:
            pass

        async def run(self, **_: Any) -> Any:
            return RuntimeResult(payload="done", response_id=None, state=AgentState(messages=[]))

    monkeypatch.setattr("minibot.llm.tools.agent_delegate.AgentRuntime", _AnsweringRuntime)

    tool = AgentDelegateTool(
        registry=AgentRegistry([spec]),
        llm_factory=cast(Any, factory),
        tools=[],
        default_timeout_seconds=180,
        delegated_tool_call_policy="never",
    )

    result = await tool._invoke_agent(
        {
            "agent_name": "general_agent",
            "task": "summarize the repo",
            "model_provider": "opencode_go",
            "model": "deepseek-v3.6",
            "reasoning_effort": "high",
        },
        ToolContext(owner_id="primary"),
    )

    assert result["ok"] is True
    used = factory.specs[0]
    assert (used.model_provider, used.model, used.reasoning_effort) == ("opencode_go", "deepseek-v3.6", "high")
    # Both were derived at boot from gpt-4o-mini, so they must not follow the spec to another model.
    assert used.context_limit is None
    assert used.max_new_tokens is None
    assert (spec.model_provider, spec.context_limit) == ("openai", 128000)


@pytest.mark.asyncio
async def test_invoke_agent_rejects_provider_without_configured_credentials() -> None:
    spec = AgentSpec(
        name="general_agent",
        description="generalist",
        system_prompt="do the work",
        source_path=Path("agents/general.md"),
    )
    factory = _SpecCapturingFactory([_opencode_go_option()])

    tool = AgentDelegateTool(
        registry=AgentRegistry([spec]),
        llm_factory=cast(Any, factory),
        tools=[],
        default_timeout_seconds=180,
        delegated_tool_call_policy="never",
    )

    result = await tool._invoke_agent(
        {"agent_name": "general_agent", "task": "summarize the repo", "model_provider": "zai"},
        ToolContext(owner_id="primary"),
    )

    assert result["ok"] is False
    assert result["error_code"] == "provider_not_available"
    assert [option["name"] for option in cast(list[Any], result["available_providers"])] == ["opencode_go"]
    assert factory.specs == []

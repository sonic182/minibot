from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.console_harness import run_console_turn, write_config
from tests.fixtures.console_harness import write_agent as _write_agent
from tests.fixtures.llm.mock_client import ScriptedLLMClient, ScriptedLLMFactory


def _write_config(
    *,
    tmp_path: Path,
    provider: str,
    orchestration_dir: Path,
    tool_ownership_mode: str = "shared",
    main_agent_tools_allow: list[str] | None = None,
) -> Path:
    return write_config(
        tmp_path=tmp_path,
        provider=provider,
        db_name="test_agents_functional.db",
        orchestration_dir=orchestration_dir,
        tool_ownership_mode=tool_ownership_mode,
        main_agent_tools_allow=main_agent_tools_allow,
    )


async def _run_single_turn(*, config_path: Path, text: str, llm_factory: ScriptedLLMFactory):
    result = await run_console_turn(config_path=config_path, text=text, llm_factory=llm_factory)
    return result.response


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_responses"])
async def test_main_agent_invokes_specialist_via_tool(tmp_path: Path, provider: str) -> None:
    agents_dir = tmp_path / "agents"
    _write_agent(
        agents_dir=agents_dir,
        name="workspace_manager_agent",
        description="workspace specialist",
        model_provider=provider,
        tools_allow=["calculate_expression"],
    )

    default_client = ScriptedLLMClient(provider=provider)
    default_client.runtime_steps = [
        {
            "content": "delegating",
            "tool_name": "invoke_agent",
            "arguments": {
                "agent_name": "workspace_manager_agent",
                "task": "Calculate 2+3 and return only result",
            },
            "call_id": "delegate-1",
            "response_id": "main-step-1",
            "total_tokens": 5,
        },
        {
            "content": "delegated result is 5",
            "response_id": "main-final",
            "total_tokens": 6,
        },
    ]

    worker_client = ScriptedLLMClient(provider=provider)
    worker_client.runtime_steps = [
        {
            "content": "calling calculate",
            "tool_name": "calculate_expression",
            "arguments": {"expression": "2+3"},
            "call_id": "worker-calc",
            "response_id": "worker-step-1",
            "total_tokens": 4,
        },
        {
            "content": "5",
            "response_id": "worker-final",
            "total_tokens": 4,
        },
    ]

    response = await _run_single_turn(
        config_path=_write_config(
            tmp_path=tmp_path,
            provider=provider,
            orchestration_dir=agents_dir,
        ),
        text="delegate this",
        llm_factory=ScriptedLLMFactory(
            default_client=default_client,
            agent_clients={"workspace_manager_agent": worker_client},
        ),
    )

    assert response.text == "delegated result is 5"
    assert response.metadata["primary_agent"] == "minibot"
    assert response.metadata["delegation_fallback_used"] is False
    trace = response.metadata.get("agent_trace")
    assert isinstance(trace, list)
    assert any(entry.get("target") == "workspace_manager_agent" and entry.get("ok") is True for entry in trace)
    assert worker_client.complete_requests
    worker_messages = worker_client.complete_requests[0]["messages"]
    assert isinstance(worker_messages, list)
    worker_system = worker_messages[0].get("content") if worker_messages else None
    assert isinstance(worker_system, str)
    assert "Browser artifacts directory" in worker_system


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_responses"])
async def test_disabled_agent_is_not_invokable(tmp_path: Path, provider: str) -> None:
    agents_dir = tmp_path / "agents"
    _write_agent(
        agents_dir=agents_dir,
        name="workspace_manager_agent",
        description="workspace specialist",
        model_provider=provider,
        enabled=False,
        tools_allow=["calculate_expression"],
    )

    default_client = ScriptedLLMClient(provider=provider)
    default_client.runtime_steps = [
        {
            "content": "delegating",
            "tool_name": "invoke_agent",
            "arguments": {
                "agent_name": "workspace_manager_agent",
                "task": "Try a task",
            },
            "call_id": "delegate-missing",
            "response_id": "main-step-1",
            "total_tokens": 5,
        },
        {
            "content": "fallback answer",
            "response_id": "main-final",
            "total_tokens": 5,
        },
    ]

    response = await _run_single_turn(
        config_path=_write_config(
            tmp_path=tmp_path,
            provider=provider,
            orchestration_dir=agents_dir,
        ),
        text="delegate this",
        llm_factory=ScriptedLLMFactory(default_client=default_client),
    )

    assert response.text == "fallback answer"
    assert response.metadata["delegation_fallback_used"] is True
    trace = response.metadata.get("agent_trace")
    assert isinstance(trace, list)
    assert any(entry.get("ok") is False for entry in trace)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_responses"])
async def test_exclusive_ownership_hides_specialist_tool_from_main_agent(tmp_path: Path, provider: str) -> None:
    agents_dir = tmp_path / "agents"
    _write_agent(
        agents_dir=agents_dir,
        name="workspace_manager_agent",
        description="workspace specialist",
        model_provider=provider,
        tools_allow=["calculate_expression"],
    )

    default_client = ScriptedLLMClient(provider=provider)
    default_client.runtime_steps = [
        {
            "content": "delegating",
            "tool_name": "invoke_agent",
            "arguments": {
                "agent_name": "workspace_manager_agent",
                "task": "calculate 2+3",
            },
            "call_id": "delegate-exclusive",
            "response_id": "main-step-1",
            "total_tokens": 5,
        },
        {
            "content": "result is 5",
            "response_id": "main-final",
            "total_tokens": 6,
        },
    ]

    worker_client = ScriptedLLMClient(provider=provider)
    worker_client.runtime_steps = [
        {
            "content": "calling calculate",
            "tool_name": "calculate_expression",
            "arguments": {"expression": "2+3"},
            "call_id": "worker-calc",
            "response_id": "worker-step-1",
            "total_tokens": 4,
        },
        {
            "content": "5",
            "response_id": "worker-final",
            "total_tokens": 4,
        },
    ]

    response = await _run_single_turn(
        config_path=_write_config(
            tmp_path=tmp_path,
            provider=provider,
            orchestration_dir=agents_dir,
            tool_ownership_mode="exclusive",
            main_agent_tools_allow=["current_*", "calculate_*", "invoke_agent"],
        ),
        text="delegate this",
        llm_factory=ScriptedLLMFactory(
            default_client=default_client,
            agent_clients={"workspace_manager_agent": worker_client},
        ),
    )

    assert response.text == "result is 5"
    main_agent_tools = default_client.complete_requests[0]["tool_names"]
    assert "calculate_expression" not in main_agent_tools
    assert "invoke_agent" in main_agent_tools
    assert worker_client.complete_requests


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_responses"])
async def test_delegated_agent_without_tool_calls_triggers_fallback(tmp_path: Path, provider: str) -> None:
    agents_dir = tmp_path / "agents"
    _write_agent(
        agents_dir=agents_dir,
        name="workspace_manager_agent",
        description="workspace specialist",
        model_provider=provider,
        tools_allow=["calculate_expression"],
    )

    default_client = ScriptedLLMClient(provider=provider)
    default_client.runtime_steps = [
        {
            "content": "delegating",
            "tool_name": "invoke_agent",
            "arguments": {
                "agent_name": "workspace_manager_agent",
                "task": "calculate 2+3",
            },
            "call_id": "delegate-no-tool",
            "response_id": "main-step-1",
            "total_tokens": 5,
        },
        {
            "content": "fallback answer",
            "response_id": "main-final",
            "total_tokens": 5,
        },
    ]

    worker_client = ScriptedLLMClient(provider=provider)
    worker_client.runtime_steps = [
        {
            "content": "5",
            "response_id": "worker-final-1",
            "total_tokens": 4,
        },
        {
            "content": "5",
            "response_id": "worker-final-2",
            "total_tokens": 4,
        },
    ]

    response = await _run_single_turn(
        config_path=_write_config(
            tmp_path=tmp_path,
            provider=provider,
            orchestration_dir=agents_dir,
        ),
        text="delegate this",
        llm_factory=ScriptedLLMFactory(
            default_client=default_client,
            agent_clients={"workspace_manager_agent": worker_client},
        ),
    )

    assert response.text == "fallback answer"
    assert len(worker_client.complete_requests) == 2

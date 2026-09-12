from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.console_harness import run_console_turn as _run_console_turn
from tests.fixtures.console_harness import write_agent
from tests.fixtures.console_harness import write_config as _write_config
from tests.fixtures.llm.mock_client import ScriptedLLMClient, ScriptedLLMFactory


def _write_plain_config(tmp_path: Path, provider: str) -> Path:
    return _write_config(tmp_path=tmp_path, provider=provider, db_name="test_console_minibot.db")


def _write_agents_config(tmp_path: Path, provider: str) -> Path:
    return _write_config(
        tmp_path=tmp_path,
        provider=provider,
        db_name="test_console_minibot.db",
        orchestration_dir=tmp_path / "agents",
    )


@pytest.mark.asyncio
async def test_console_functional_openai_chat_completion_flow(tmp_path: Path) -> None:
    default_client = ScriptedLLMClient(provider="openai")
    default_client.runtime_steps = [
        {
            "content": "ok from fake chat",
            "response_id": "console-openai-1",
            "total_tokens": 8,
        }
    ]
    factory = ScriptedLLMFactory(default_client=default_client)
    config_path = _write_plain_config(tmp_path, provider="openai")

    response = await _run_console_turn(
        config_path=config_path,
        llm_factory=factory,
        text="hello from console",
        chat_id=100,
        user_id=200,
    )

    assert response.response.channel == "console"
    assert "ok from fake chat" in response.response.text
    assert default_client.complete_requests


@pytest.mark.asyncio
async def test_console_functional_openai_responses_flow(tmp_path: Path) -> None:
    default_client = ScriptedLLMClient(provider="openai_responses")
    default_client.runtime_steps = [
        {
            "content": "ok from fake responses",
            "response_id": "console-responses-1",
            "total_tokens": 8,
        }
    ]
    factory = ScriptedLLMFactory(default_client=default_client)
    config_path = _write_plain_config(tmp_path, provider="openai_responses")

    response = await _run_console_turn(
        config_path=config_path,
        llm_factory=factory,
        text="hello via responses api",
        chat_id=101,
        user_id=201,
    )

    assert response.response.channel == "console"
    assert "ok from fake responses" in response.response.text
    assert default_client.complete_requests


@pytest.mark.asyncio
async def test_console_functional_agent_delegation_metadata(tmp_path: Path) -> None:
    write_agent(
        agents_dir=tmp_path / "agents",
        name="worker",
        description="test worker agent",
        model_provider="openai",
    )

    default_client = ScriptedLLMClient(provider="openai")
    default_client.runtime_steps = [
        {
            "content": "delegating",
            "tool_name": "invoke_agent",
            "arguments": {
                "agent_name": "worker",
                "task": "answer delegated task",
            },
            "response_id": "main-agent-1",
            "total_tokens": 8,
        },
        {
            "content": "delegated response",
            "response_id": "main-agent-2",
            "total_tokens": 8,
        },
    ]
    worker_client = ScriptedLLMClient(provider="openai")
    worker_client.runtime_steps = [
        {
            "content": "delegated response",
            "response_id": "worker-1",
            "total_tokens": 8,
        }
    ]
    factory = ScriptedLLMFactory(default_client=default_client, agent_clients={"worker": worker_client})

    config_path = _write_agents_config(tmp_path, provider="openai")
    response = await _run_console_turn(
        config_path=config_path,
        llm_factory=factory,
        text="route this to specialist",
        chat_id=102,
        user_id=202,
    )

    assert response.response.text == "delegated response"
    assert response.response.metadata["primary_agent"] == "minibot"
    assert isinstance(response.response.metadata.get("agent_trace"), list)

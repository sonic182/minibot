from __future__ import annotations

from pathlib import Path

import pytest

from minibot.adapters.config.schema import SqliteTaskQueueConfig
from minibot.adapters.tasks.sqlite_store import SQLiteTaskStore
from minibot.core.tasks import TaskRecord
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
        tasks_enabled=True,
    )


async def _run_single_turn(*, config_path: Path, text: str, llm_factory: ScriptedLLMFactory):
    result = await run_console_turn(config_path=config_path, text=text, llm_factory=llm_factory)
    return result.response


async def _queued_tasks(tmp_path: Path) -> list[TaskRecord]:
    """Read the queue back: dialect-independent, unlike scraping the tool message."""
    store = SQLiteTaskStore(
        SqliteTaskQueueConfig(sqlite_url=f"sqlite+aiosqlite:///{(tmp_path / 'tasks.db').as_posix()}")
    )
    await store.initialize()
    return await store.list(owner_id="primary", statuses=None, limit=10)


def _delegating_client(provider: str, *, agent_name: str, call_id: str, final: str) -> ScriptedLLMClient:
    client = ScriptedLLMClient(provider=provider)
    client.runtime_steps = [
        {
            "content": "delegating",
            "tool_name": "spawn_task",
            "arguments": {"agent_name": agent_name, "prompt": "Calculate 2+3 and return only result"},
            "call_id": call_id,
            "response_id": "main-step-1",
            "total_tokens": 5,
        },
        {"content": final, "response_id": "main-final", "total_tokens": 6},
    ]
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_responses"])
async def test_main_agent_delegates_to_a_specialist_via_spawn_task(tmp_path: Path, provider: str) -> None:
    agents_dir = tmp_path / "agents"
    _write_agent(
        agents_dir=agents_dir,
        name="workspace_manager_agent",
        description="workspace specialist",
        model_provider=provider,
        tools_allow=["calculate_expression"],
    )

    default_client = _delegating_client(
        provider,
        agent_name="workspace_manager_agent",
        call_id="delegate-1",
        final="handed the work off",
    )

    response = await _run_single_turn(
        config_path=_write_config(tmp_path=tmp_path, provider=provider, orchestration_dir=agents_dir),
        text="delegate this",
        llm_factory=ScriptedLLMFactory(default_client=default_client),
    )

    assert response.text == "handed the work off"
    assert response.metadata["primary_agent"] == "minibot"

    queued = await _queued_tasks(tmp_path)
    assert len(queued) == 1
    assert queued[0].request.agent_name == "workspace_manager_agent"
    assert queued[0].request.channel == "console"

    # The roster is what makes `agent_name` usable at all, and it hangs off spawn_task.
    system_prompt = default_client.complete_requests[0]["messages"][0]["content"]
    assert "workspace_manager_agent" in system_prompt
    assert "spawn_task" in default_client.complete_requests[0]["tool_names"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openai_responses"])
async def test_disabled_agent_is_not_offered_for_delegation(tmp_path: Path, provider: str) -> None:
    """A disabled spec never reaches the roster, so the model cannot name it.

    ``spawn_task`` refusing an unknown ``agent_name`` outright is covered in
    ``tests/test_task_tools.py``.
    """
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
    default_client.runtime_steps = [{"content": "answering locally", "response_id": "main-final", "total_tokens": 5}]

    response = await _run_single_turn(
        config_path=_write_config(tmp_path=tmp_path, provider=provider, orchestration_dir=agents_dir),
        text="delegate this",
        llm_factory=ScriptedLLMFactory(default_client=default_client),
    )

    assert response.text == "answering locally"
    system_prompt = default_client.complete_requests[0]["messages"][0]["content"]
    assert "workspace_manager_agent" not in system_prompt
    assert await _queued_tasks(tmp_path) == []


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

    default_client = _delegating_client(
        provider,
        agent_name="workspace_manager_agent",
        call_id="delegate-exclusive",
        final="handed the work off",
    )

    response = await _run_single_turn(
        config_path=_write_config(
            tmp_path=tmp_path,
            provider=provider,
            orchestration_dir=agents_dir,
            tool_ownership_mode="exclusive",
            main_agent_tools_allow=["current_*", "calculate_*", "spawn_task", "fetch_agent_info"],
        ),
        text="delegate this",
        llm_factory=ScriptedLLMFactory(default_client=default_client),
    )

    assert response.text == "handed the work off"
    main_agent_tools = default_client.complete_requests[0]["tool_names"]
    assert "calculate_expression" not in main_agent_tools
    assert "spawn_task" in main_agent_tools

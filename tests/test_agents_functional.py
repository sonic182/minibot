from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from minibot.adapters.container.app_container import AppContainer
from minibot.adapters.messaging.console.service import ConsoleService
from minibot.app.dispatcher import Dispatcher
from tests.fixtures.llm.mock_client import ScriptedLLMClient, ScriptedLLMFactory


def _reset_container() -> None:
    AppContainer._settings = None
    AppContainer._logger = None
    AppContainer._event_bus = None
    AppContainer._memory_backend = None
    AppContainer._kv_memory_backend = None
    AppContainer._llm_client = None
    AppContainer._llm_factory = None
    AppContainer._agent_registry = None
    AppContainer._prompt_store = None
    AppContainer._prompt_service = None


def _write_config(
    *,
    tmp_path: Path,
    provider: str,
    orchestration_dir: Path,
    tool_ownership_mode: str = "shared",
    main_agent_tools_allow: list[str] | None = None,
) -> Path:
    config_path = tmp_path / "config.toml"
    sqlite_url = f"sqlite+aiosqlite:///{(tmp_path / 'test_agents_functional.db').as_posix()}"
    main_agent_lines = ["\n[orchestration.main_agent]"]
    if main_agent_tools_allow:
        entries = ", ".join([f'"{name}"' for name in main_agent_tools_allow])
        main_agent_lines.append(f"tools_allow = [{entries}]")
    main_agent_block = "\n".join(main_agent_lines)
    orchestration_block = (
        "\n[orchestration]\n"
        f'directory = "{orchestration_dir.as_posix()}"\n'
        "default_timeout_seconds = 30\n"
        f'tool_ownership_mode = "{tool_ownership_mode}"\n'
    ) + main_agent_block
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'log_level = "INFO"',
                "",
                "[channels.telegram]",
                "enabled = false",
                'bot_token = ""',
                "",
                "[llm]",
                f'provider = "{provider}"',
                'model = "gpt-4o-mini"',
                'system_prompt = "You are Minibot, a helpful assistant."',
                "",
                f"[providers.{provider}]",
                'api_key = "test-key"',
                'base_url = "http://mock.local/v1"',
                "",
                "[memory]",
                f'sqlite_url = "{sqlite_url}"',
                "",
                "[scheduler.prompts]",
                "enabled = false",
            ]
        )
        + orchestration_block
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _write_agent(
    *,
    agents_dir: Path,
    name: str,
    description: str,
    model_provider: str,
    enabled: bool = True,
    tools_allow: list[str] | None = None,
) -> None:
    agents_dir.mkdir(parents=True, exist_ok=True)
    allow_lines = ""
    if tools_allow:
        allow_lines = "tools_allow:\n" + "".join([f"  - {item}\n" for item in tools_allow])
    enabled_line = "true" if enabled else "false"
    (agents_dir / f"{name}.md").write_text(
        (
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"enabled: {enabled_line}\n"
            "mode: agent\n"
            f"model_provider: {model_provider}\n"
            "model: gpt-4o-mini\n"
            f"{allow_lines}"
            "---\n\n"
            f"You are {name}."
        ),
        encoding="utf-8",
    )


async def _run_single_turn(*, config_path: Path, text: str, llm_factory: ScriptedLLMFactory):
    _reset_container()
    AppContainer.configure(config_path)
    await AppContainer.initialize_storage()
    bus = AppContainer.get_event_bus()
    with (
        patch.object(AppContainer, "get_llm_factory", return_value=llm_factory),
        patch.object(AppContainer, "get_llm_client", return_value=llm_factory.create_default()),
    ):
        dispatcher = Dispatcher(bus)
        console_service = ConsoleService(bus, chat_id=999, user_id=777)
        await dispatcher.start()
        await console_service.start()
        try:
            await console_service.publish_user_message(text)
            response = await console_service.wait_for_response(3.0)
            return response.response
        finally:
            await console_service.stop()
            await dispatcher.stop()
            _reset_container()


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
            "content": '{"answer":{"kind":"text","content":"delegated result is 5"},"should_answer_to_user":true}',
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
            "content": '{"answer":{"kind":"text","content":"5"},"should_answer_to_user":true}',
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
            "content": '{"answer":{"kind":"text","content":"fallback answer"},"should_answer_to_user":true}',
            "response_id": "main-final",
            "total_tokens": 5,
        },
        {
            "content": '{"answer":{"kind":"text","content":"fallback answer"},"should_answer_to_user":true}',
            "response_id": "main-final-retry",
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
            "content": '{"answer":{"kind":"text","content":"result is 5"},"should_answer_to_user":true}',
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
            "content": '{"answer":{"kind":"text","content":"5"},"should_answer_to_user":true}',
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
            "content": '{"answer":{"kind":"text","content":"fallback answer"},"should_answer_to_user":true}',
            "response_id": "main-final",
            "total_tokens": 5,
        },
        {
            "content": '{"answer":{"kind":"text","content":"fallback answer"},"should_answer_to_user":true}',
            "response_id": "main-final-retry",
            "total_tokens": 5,
        },
    ]

    worker_client = ScriptedLLMClient(provider=provider)
    worker_client.runtime_steps = [
        {
            "content": '{"answer":{"kind":"text","content":"5"},"should_answer_to_user":true}',
            "response_id": "worker-final-1",
            "total_tokens": 4,
        },
        {
            "content": '{"answer":{"kind":"text","content":"5"},"should_answer_to_user":true}',
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

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


def _write_config(tmp_path: Path, provider: str = "openai", agents_enabled: bool = False) -> Path:
    config_path = tmp_path / "config.toml"
    agents_lines = ""
    if agents_enabled:
        agents_lines = (
            "\n[agents]\n"
            "enabled = true\n"
            f'directory = "{tmp_path / "agents"}"\n'
            "max_delegation_depth = 2\n"
            "default_timeout_seconds = 90\n"
        )
    sqlite_url = f"sqlite+aiosqlite:///{(tmp_path / 'test_console_minibot.db').as_posix()}"
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
        + agents_lines
        + "\n",
        encoding="utf-8",
    )
    return config_path


async def _run_console_turn(
    *,
    config_path: Path,
    llm_factory: ScriptedLLMFactory,
    text: str,
    chat_id: int,
    user_id: int,
):
    _reset_container()
    AppContainer.configure(config_path)
    await AppContainer.initialize_storage()
    bus = AppContainer.get_event_bus()
    with (
        patch.object(AppContainer, "get_llm_factory", return_value=llm_factory),
        patch.object(AppContainer, "get_llm_client", return_value=llm_factory.create_default()),
    ):
        dispatcher = Dispatcher(bus)
        console_service = ConsoleService(bus, chat_id=chat_id, user_id=user_id)
        await dispatcher.start()
        await console_service.start()
        try:
            await console_service.publish_user_message(text)
            return await console_service.wait_for_response(3.0)
        finally:
            await console_service.stop()
            await dispatcher.stop()
            _reset_container()


@pytest.mark.asyncio
async def test_console_functional_openai_chat_completion_flow(tmp_path: Path) -> None:
    default_client = ScriptedLLMClient(provider="openai")
    default_client.runtime_steps = [
        {
            "content": '{"answer":{"kind":"text","content":"ok from fake chat"},"should_answer_to_user":true}',
            "response_id": "console-openai-1",
            "total_tokens": 8,
        }
    ]
    factory = ScriptedLLMFactory(default_client=default_client)
    config_path = _write_config(tmp_path, provider="openai", agents_enabled=False)

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
            "content": '{"answer":{"kind":"text","content":"ok from fake responses"},"should_answer_to_user":true}',
            "response_id": "console-responses-1",
            "total_tokens": 8,
        }
    ]
    factory = ScriptedLLMFactory(default_client=default_client)
    config_path = _write_config(tmp_path, provider="openai_responses", agents_enabled=False)

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
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "worker.md").write_text(
        (
            "---\n"
            "name: worker\n"
            "description: test worker agent\n"
            "mode: agent\n"
            "model_provider: openai\n"
            "model: gpt-4o-mini\n"
            "---\n\n"
            "You are worker agent."
        ),
        encoding="utf-8",
    )

    default_client = ScriptedLLMClient(provider="openai")
    default_client.generate_steps = [
        {
            "payload": {
                "strategy": "delegate_agent",
                "agent_name": "worker",
                "reason": "test",
                "requires_tool_execution": False,
            },
            "response_id": "router-1",
            "total_tokens": 8,
        }
    ]
    worker_client = ScriptedLLMClient(provider="openai")
    worker_client.runtime_steps = [
        {
            "content": '{"answer":{"kind":"text","content":"delegated response"},"should_answer_to_user":true}',
            "response_id": "worker-1",
            "total_tokens": 8,
        }
    ]
    factory = ScriptedLLMFactory(default_client=default_client, agent_clients={"worker": worker_client})

    config_path = _write_config(tmp_path, provider="openai", agents_enabled=True)
    response = await _run_console_turn(
        config_path=config_path,
        llm_factory=factory,
        text="route this to specialist",
        chat_id=102,
        user_id=202,
    )

    assert response.response.text == "delegated response"
    assert response.response.metadata["primary_agent"] == "worker"
    assert isinstance(response.response.metadata.get("agent_trace"), list)

from __future__ import annotations

from pathlib import Path

import pytest

from minibot.adapters.container.app_container import AppContainer
from minibot.adapters.messaging.console.service import ConsoleService
from minibot.app.dispatcher import Dispatcher

pytest_plugins = ("tests.fixtures.llm.server",)


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


def _write_config(tmp_path: Path, base_url: str, provider: str = "openai", agents_enabled: bool = False) -> Path:
    config_path = tmp_path / "config.toml"
    agents_lines = ""
    if agents_enabled:
        agents_lines = (
            "\n[agents]\n"
            "enabled = true\n"
            f"directory = \"{tmp_path / 'agents'}\"\n"
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
                f'base_url = "{base_url}"',
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


@pytest.mark.asyncio
async def test_console_functional_openai_chat_completion_flow(tmp_path: Path, fake_llm_server) -> None:
    _reset_container()
    config_path = _write_config(tmp_path, fake_llm_server["base_url"], provider="openai", agents_enabled=False)
    AppContainer.configure(config_path)
    await AppContainer.initialize_storage()
    bus = AppContainer.get_event_bus()
    dispatcher = Dispatcher(bus)
    console_service = ConsoleService(bus, chat_id=100, user_id=200)
    await dispatcher.start()
    await console_service.start()

    try:
        await console_service.publish_user_message("hello from console")
        response = await console_service.wait_for_response(3.0)
        assert response.response.channel == "console"
        assert "ok from fake chat" in response.response.text
        assert fake_llm_server["state"].chat_requests
    finally:
        await console_service.stop()
        await dispatcher.stop()
        _reset_container()


@pytest.mark.asyncio
async def test_console_functional_openai_responses_flow(tmp_path: Path, fake_llm_server) -> None:
    _reset_container()
    config_path = _write_config(
        tmp_path,
        fake_llm_server["base_url"],
        provider="openai_responses",
        agents_enabled=False,
    )
    AppContainer.configure(config_path)
    await AppContainer.initialize_storage()
    bus = AppContainer.get_event_bus()
    dispatcher = Dispatcher(bus)
    console_service = ConsoleService(bus, chat_id=101, user_id=201)
    await dispatcher.start()
    await console_service.start()

    try:
        await console_service.publish_user_message("hello via responses api")
        response = await console_service.wait_for_response(3.0)
        assert response.response.channel == "console"
        assert "ok from fake responses" in response.response.text
        assert fake_llm_server["state"].responses_requests
    finally:
        await console_service.stop()
        await dispatcher.stop()
        _reset_container()


@pytest.mark.asyncio
async def test_console_functional_agent_delegation_metadata(tmp_path: Path, fake_llm_server) -> None:
    _reset_container()
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
            "tools:\n"
            "  write: false\n"
            "  edit: false\n"
            "  bash: false\n"
            "---\n\n"
            "You are worker agent."
        ),
        encoding="utf-8",
    )
    state = fake_llm_server["state"]
    state.chat_payloads = [
        {
            "id": "chatcmpl-router",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": '{"should_delegate":true,"agent_name":"worker","reason":"test"}',
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 4, "completion_tokens": 4, "total_tokens": 8},
        },
        {
            "id": "chatcmpl-worker",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": (
                            '{"answer":{"kind":"text","content":"delegated response"},'
                            '"should_answer_to_user":true}'
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 4, "completion_tokens": 4, "total_tokens": 8},
        },
    ]
    config_path = _write_config(tmp_path, fake_llm_server["base_url"], provider="openai", agents_enabled=True)
    AppContainer.configure(config_path)
    await AppContainer.initialize_storage()
    bus = AppContainer.get_event_bus()
    dispatcher = Dispatcher(bus)
    console_service = ConsoleService(bus, chat_id=102, user_id=202)
    await dispatcher.start()
    await console_service.start()

    try:
        await console_service.publish_user_message("route this to specialist")
        response = await console_service.wait_for_response(3.0)
        assert response.response.text == "delegated response"
        assert response.response.metadata["primary_agent"] == "worker"
        assert isinstance(response.response.metadata.get("agent_trace"), list)
    finally:
        await console_service.stop()
        await dispatcher.stop()
        _reset_container()

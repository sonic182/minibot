from __future__ import annotations

import json
from pathlib import Path

import pytest

from minibot.config.schema import Settings
from minibot.core.tools import ToolContext
from minibot.llm.tools.settings_info import SettingsInfoTool

_SECRET = "SENTINEL-8f3a"


async def _call(settings: Settings, **kwargs: object) -> dict[str, object]:
    [binding] = SettingsInfoTool(settings, **kwargs).bindings()  # type: ignore[arg-type]
    return await binding.handler({}, ToolContext())


@pytest.mark.asyncio
async def test_no_secret_field_reaches_the_result() -> None:
    settings = Settings.from_dict(
        {
            "channels": {"telegram": {"bot_token": f"{_SECRET}-bot"}},
            "llm": {
                "api_key": f"{_SECRET}-key",
                "base_url": f"https://{_SECRET}.example.com",
                "extra_headers": {"Authorization": f"{_SECRET}-header"},
                "auth_path": f"/{_SECRET}/auth.json",
            },
            "providers": {"openai": {"api_key": f"{_SECRET}-provider"}},
            "memory": {"sqlite_url": f"sqlite:///{_SECRET}.db"},
            "http": {"enabled": True, "auth_token": f"{_SECRET}-http", "basic_auth_password": f"{_SECRET}-basic"},
            "rabbitmq": {"broker_url": f"amqp://user:{_SECRET}@host/"},
            "vault": {"enabled": True, "path": f"{_SECRET}.yml", "password_file": f"/{_SECRET}/pw"},
            "tools": {
                "mcp": {
                    "enabled": True,
                    "servers": [
                        {
                            "name": "github",
                            "transport": "http",
                            "url": f"https://{_SECRET}.example.com/mcp",
                            "headers": {"Authorization": f"{_SECRET}-mcp"},
                            "env": {"TOKEN": f"{_SECRET}-env"},
                            "auth_secret": f"{_SECRET}-secret",
                        }
                    ],
                }
            },
        }
    )

    result = await _call(settings, config_path=Path("config.toml"))

    assert _SECRET not in json.dumps(result)
    assert result["channels"] == ["telegram"]
    assert result["tools"]["mcp"] == {"servers": ["github"]}  # type: ignore[index]
    assert result["vault"] == {"enabled": True}


@pytest.mark.asyncio
async def test_disabled_features_are_absent() -> None:
    result = await _call(
        Settings.from_dict(
            {
                "tasks": {"enabled": False},
                # Specialists run on the task backend, so they go off with it.
                "orchestration": {"specialists": {"enabled": False}},
                "scheduler": {"prompts": {"enabled": False}},
            }
        )
    )

    assert result["channels"] == []
    assert not {"tasks", "scheduler", "vault"} & set(result)
    assert not {"bash", "mcp", "http_client", "file_storage"} & set(result["tools"])  # type: ignore[arg-type]
    assert result["agents"] == {"directory": "./agents", "specialists_enabled": False, "names": []}


@pytest.mark.asyncio
async def test_agent_management_is_absent_until_it_is_enabled() -> None:
    result = await _call(Settings())

    assert result["agents"]["specialists_enabled"] is True  # type: ignore[index]
    assert "agent_management" not in result["agents"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_agent_management_reports_the_ceiling_and_switches() -> None:
    settings = Settings.from_dict(
        {
            "orchestration": {
                "agent_management": {
                    "write": True,
                    "directory": "./data/agents",
                    "tools_allow": ["filesystem"],
                    "mcp_servers": ["playwright"],
                    "providers": ["anthropic"],
                },
            }
        }
    )

    result = await _call(settings)

    assert result["agents"]["agent_management"] == {  # type: ignore[index]
        "reload": False,
        "write": True,
        "directory": "./data/agents",
        "tools_allow": ["filesystem"],
        "mcp_servers": ["playwright"],
        "providers": ["anthropic"],
    }


@pytest.mark.asyncio
async def test_agent_and_skill_names_are_read_at_call_time() -> None:
    names = ["alpha"]
    settings = Settings.from_dict({"tools": {"skills": {"enabled": True}}})
    [binding] = SettingsInfoTool(settings, agent_names=lambda: list(names), skill_names=lambda: ["deploy"]).bindings()

    names.append("beta")
    result = await binding.handler({}, ToolContext())

    assert result["agents"]["names"] == ["alpha", "beta"]  # type: ignore[index]
    assert result["tools"]["skills"]["names"] == ["deploy"]  # type: ignore[index]

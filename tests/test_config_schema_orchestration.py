from __future__ import annotations

import pytest

from minibot.config.schema import (
    AgentManagementConfig,
    OrchestrationConfig,
    Settings,
    SpecialistsConfig,
)


def _settings(**orchestration: object) -> Settings:
    return Settings.from_dict({"orchestration": orchestration})


def test_defaults_keep_specialists_on_and_management_off() -> None:
    settings = Settings()

    assert settings.orchestration.specialists.enabled is True
    assert settings.orchestration.agent_management.reload is False
    assert settings.orchestration.agent_management.write is False
    assert settings.orchestration.agent_management.active is False


def test_specialists_require_the_task_backend() -> None:
    with pytest.raises(ValueError, match=r"\[orchestration\.specialists\]\.enabled requires"):
        Settings.from_dict({"tasks": {"enabled": False}})


def test_tasks_without_specialists_is_allowed() -> None:
    settings = Settings.from_dict({"tasks": {"enabled": True}, "orchestration": {"specialists": {"enabled": False}}})

    assert settings.orchestration.specialists.enabled is False
    assert settings.tasks.enabled is True


def test_management_requires_specialists() -> None:
    with pytest.raises(ValueError, match=r"\[orchestration\.agent_management\] requires"):
        _settings(
            specialists={"enabled": False},
            agent_management={"reload": True},
        )


def test_write_without_reload_is_allowed() -> None:
    settings = _settings(agent_management={"write": True})

    assert settings.orchestration.agent_management.write is True
    assert settings.orchestration.agent_management.reload is False
    assert settings.orchestration.agent_management.active is True


def test_management_directory_must_not_overlap_the_owner_directory() -> None:
    with pytest.raises(ValueError, match="must not overlap"):
        _settings(
            directory="./agents",
            agent_management={"reload": True, "directory": "./agents/managed"},
        )


def test_management_directory_may_sit_outside_the_owner_directory() -> None:
    settings = _settings(
        directory="./agents",
        agent_management={"reload": True, "directory": "./data/agents"},
    )

    assert settings.orchestration.agent_management.directory == "./data/agents"


def test_management_directory_is_not_checked_while_management_is_off() -> None:
    settings = _settings(directory="./agents", agent_management={"directory": "./agents"})

    assert settings.orchestration.agent_management.directory == "./agents"


def test_management_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError):
        AgentManagementConfig.model_validate({"writes": True})


def test_specialists_reject_unknown_keys() -> None:
    with pytest.raises(ValueError):
        SpecialistsConfig.model_validate({"enable": True})


def test_orchestration_defaults_fill_both_sub_tables() -> None:
    config = OrchestrationConfig()

    assert isinstance(config.specialists, SpecialistsConfig)
    assert isinstance(config.agent_management, AgentManagementConfig)

from __future__ import annotations

from pathlib import Path

import pytest

from minibot.adapters.agents.managed_store import LocalManagedAgentStore
from minibot.app.agent_management import AgentManagementService
from minibot.config.schema import Settings


def _definition(*, name: str = "helper_agent", tools: str = "  - filesystem\n", enabled: str = "true") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        "description: helper\n"
        "mode: agent\n"
        f"enabled: {enabled}\n"
        "tools_allow:\n"
        f"{tools}"
        "---\n\n"
        "You are a helper."
    )


def _service(tmp_path: Path, *, ceiling: list[str] | None = None, refresh=None) -> AgentManagementService:
    settings = Settings.from_dict(
        {
            "orchestration": {
                "agent_management": {
                    "write": True,
                    "directory": str(tmp_path / "managed"),
                    "tools_allow": ceiling if ceiling is not None else ["filesystem"],
                }
            }
        }
    )
    return AgentManagementService(
        settings=settings,
        store=LocalManagedAgentStore(tmp_path / "managed"),
        refresh=refresh,
    )


@pytest.mark.asyncio
async def test_create_writes_and_lists(tmp_path: Path) -> None:
    service = _service(tmp_path)

    outcome = await service.create(name="helper_agent", content=_definition())

    assert outcome.ok is True
    assert outcome.action == "create"
    assert outcome.names == ["helper_agent"]
    assert (tmp_path / "managed" / "helper_agent.md").exists()


@pytest.mark.asyncio
async def test_create_rejects_an_existing_name(tmp_path: Path) -> None:
    service = _service(tmp_path)
    await service.create(name="helper_agent", content=_definition())

    outcome = await service.create(name="helper_agent", content=_definition())

    assert outcome.ok is False
    assert outcome.error is not None and "already exists" in outcome.error


@pytest.mark.asyncio
async def test_update_rejects_a_missing_name(tmp_path: Path) -> None:
    outcome = await _service(tmp_path).update(name="helper_agent", content=_definition())

    assert outcome.ok is False
    assert outcome.error is not None and "does not exist" in outcome.error


@pytest.mark.asyncio
async def test_update_replaces_the_definition(tmp_path: Path) -> None:
    service = _service(tmp_path)
    await service.create(name="helper_agent", content=_definition())

    outcome = await service.update(name="helper_agent", content=_definition(tools=""))

    assert outcome.ok is True
    assert outcome.action == "update"


@pytest.mark.asyncio
async def test_delete_removes_the_definition(tmp_path: Path) -> None:
    service = _service(tmp_path)
    await service.create(name="helper_agent", content=_definition())

    outcome = await service.delete(name="helper_agent")

    assert outcome.ok is True
    assert outcome.names == []


@pytest.mark.asyncio
async def test_delete_rejects_a_missing_name(tmp_path: Path) -> None:
    outcome = await _service(tmp_path).delete(name="helper_agent")

    assert outcome.ok is False
    assert outcome.error is not None and "does not exist" in outcome.error


class _FailingStore:
    """A store whose writes fail at the filesystem layer, not at validation."""

    directory = Path("/nonexistent/managed")

    def list_names(self) -> list[str]:
        return []

    def exists(self, name: str) -> bool:
        del name
        return False

    def write(self, name: str, content: str) -> None:
        del name, content
        raise OSError("disk full")

    def delete(self, name: str) -> None:
        del name
        raise OSError("disk full")


@pytest.mark.asyncio
async def test_a_store_oserror_is_reported_as_a_failure() -> None:
    settings = Settings.from_dict(
        {"orchestration": {"agent_management": {"write": True, "tools_allow": ["filesystem"]}}}
    )
    service = AgentManagementService(settings=settings, store=_FailingStore())

    outcome = await service.create(name="helper_agent", content=_definition())

    assert outcome.ok is False
    assert outcome.error is not None and "disk full" in outcome.error


@pytest.mark.asyncio
async def test_an_unauthorized_tool_is_rejected_and_not_written(tmp_path: Path) -> None:
    service = _service(tmp_path, ceiling=["filesystem"])

    outcome = await service.create(name="helper_agent", content=_definition(tools="  - bash\n"))

    assert outcome.ok is False
    assert outcome.error is not None and "not allowed by" in outcome.error
    assert (tmp_path / "managed" / "helper_agent.md").exists() is False


@pytest.mark.asyncio
async def test_a_frontmatter_name_mismatch_is_rejected(tmp_path: Path) -> None:
    outcome = await _service(tmp_path).create(name="helper_agent", content=_definition(name="other_agent"))

    assert outcome.ok is False
    assert outcome.error is not None and "must match the requested name" in outcome.error


@pytest.mark.asyncio
async def test_an_invalid_name_is_rejected(tmp_path: Path) -> None:
    outcome = await _service(tmp_path).create(name="../escape", content=_definition(name="../escape"))

    assert outcome.ok is False
    assert outcome.error is not None and "agent name" in outcome.error


@pytest.mark.asyncio
async def test_a_disabled_definition_is_rejected(tmp_path: Path) -> None:
    outcome = await _service(tmp_path).create(name="helper_agent", content=_definition(enabled="false"))

    assert outcome.ok is False
    assert outcome.error is not None and "cannot set enabled = false" in outcome.error


@pytest.mark.asyncio
async def test_an_empty_body_is_reported_as_a_validation_failure(tmp_path: Path) -> None:
    content = "---\nname: helper_agent\ndescription: helper\nmode: agent\n---\n\n"

    outcome = await _service(tmp_path).create(name="helper_agent", content=content)

    assert outcome.ok is False
    assert outcome.error is not None and "body prompt cannot be empty" in outcome.error


@pytest.mark.asyncio
async def test_refresh_runs_after_a_successful_write(tmp_path: Path) -> None:
    calls: list[str] = []

    async def refresh() -> None:
        calls.append("refresh")

    outcome = await _service(tmp_path, refresh=refresh).create(name="helper_agent", content=_definition())

    assert outcome.ok is True
    assert calls == ["refresh"]


@pytest.mark.asyncio
async def test_a_failed_refresh_is_reported_and_the_file_remains(tmp_path: Path) -> None:
    async def refresh() -> None:
        raise RuntimeError("registry exploded")

    outcome = await _service(tmp_path, refresh=refresh).create(name="helper_agent", content=_definition())

    assert outcome.ok is False
    assert outcome.error is not None and "saved but the agent roster could not be refreshed" in outcome.error
    assert (tmp_path / "managed" / "helper_agent.md").exists() is True


@pytest.mark.asyncio
async def test_concurrent_creates_of_the_same_name_leave_one_definition(tmp_path: Path) -> None:
    import asyncio

    service = _service(tmp_path)

    outcomes = await asyncio.gather(*(service.create(name="helper_agent", content=_definition()) for _ in range(4)))

    assert sum(1 for outcome in outcomes if outcome.ok) == 1
    assert (tmp_path / "managed" / "helper_agent.md").exists() is True

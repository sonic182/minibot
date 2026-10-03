from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from minibot.adapters.agents.definition_reader import LocalAgentDefinitionReader
from minibot.adapters.agents.managed_store import LocalManagedAgentStore
from minibot.app.agent_definitions_loader import load_active_agent_specs
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
        reader=LocalAgentDefinitionReader(),
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
async def test_management_keeps_filesystem_io_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop_thread = threading.get_ident()
    store = LocalManagedAgentStore(tmp_path / "managed")
    reader = LocalAgentDefinitionReader()

    def off_loop(operation: Callable[..., object]) -> Callable[..., object]:
        def call(*args: object, **kwargs: object) -> object:
            assert threading.get_ident() != loop_thread
            return operation(*args, **kwargs)

        return call

    for name in ("exists", "write", "delete", "list_names"):
        monkeypatch.setattr(store, name, off_loop(getattr(store, name)))
    monkeypatch.setattr(reader, "read", off_loop(reader.read))
    settings = Settings.from_dict(
        {
            "orchestration": {
                "directory": str(tmp_path / "owner"),
                "agent_management": {"write": True, "tools_allow": ["filesystem"]},
            }
        }
    )
    service = AgentManagementService(settings=settings, store=store, reader=reader)

    created = await service.create(name="helper_agent", content=_definition())
    updated = await service.update(name="helper_agent", content=_definition(tools=""))
    deleted = await service.delete(name="helper_agent")

    assert created.ok and updated.ok and deleted.ok
    assert created.names == ["helper_agent"]
    assert deleted.names == []


@pytest.mark.asyncio
async def test_create_rejects_an_existing_name(tmp_path: Path) -> None:
    service = _service(tmp_path)
    await service.create(name="helper_agent", content=_definition())

    outcome = await service.create(name="helper_agent", content=_definition())

    assert outcome.ok is False
    assert outcome.error is not None and "already exists" in outcome.error


@pytest.mark.asyncio
async def test_create_rejects_an_owner_name_without_poisoning_the_roster(tmp_path: Path) -> None:
    owner_dir = tmp_path / "owner"
    owner_dir.mkdir()
    owner_file = owner_dir / "different_filename.md"
    owner_content = _definition()
    owner_file.write_text(owner_content, encoding="utf-8")
    managed_dir = tmp_path / "managed"
    settings = Settings.from_dict(
        {
            "orchestration": {
                "directory": str(owner_dir),
                "agent_management": {
                    "write": True,
                    "directory": str(managed_dir),
                    "tools_allow": ["filesystem"],
                },
            }
        }
    )
    service = AgentManagementService(
        settings=settings, store=LocalManagedAgentStore(managed_dir), reader=LocalAgentDefinitionReader()
    )

    outcome = await service.create(name="helper_agent", content=owner_content)

    assert outcome.ok is False
    assert outcome.error is not None and "owner-authored" in outcome.error
    assert not (managed_dir / "helper_agent.md").exists()
    assert owner_file.read_text(encoding="utf-8") == owner_content
    assert [spec.name for spec in load_active_agent_specs(settings, reader=LocalAgentDefinitionReader())] == [
        "helper_agent"
    ]


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
    service = AgentManagementService(settings=settings, store=_FailingStore(), reader=LocalAgentDefinitionReader())

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
async def test_cancelled_create_finishes_io_before_releasing_the_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = threading.Event()
    release = threading.Event()
    store = LocalManagedAgentStore(tmp_path / "managed")
    original_write = store.write

    def write(name: str, content: str) -> None:
        started.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test did not release the write")
        original_write(name, content)

    monkeypatch.setattr(store, "write", write)
    settings = Settings.from_dict(
        {"orchestration": {"directory": str(tmp_path / "owner"), "agent_management": {"tools_allow": ["filesystem"]}}}
    )
    service = AgentManagementService(settings=settings, store=store, reader=LocalAgentDefinitionReader())
    operation = asyncio.create_task(service.create(name="helper_agent", content=_definition()))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done()
    finally:
        release.set()
        await asyncio.gather(operation, return_exceptions=True)

    assert operation.cancelled()
    assert (tmp_path / "managed" / "helper_agent.md").read_text(encoding="utf-8") == _definition()


@pytest.mark.asyncio
async def test_concurrent_creates_of_the_same_name_leave_one_definition(tmp_path: Path) -> None:
    service = _service(tmp_path)

    outcomes = await asyncio.gather(*(service.create(name="helper_agent", content=_definition()) for _ in range(4)))

    assert sum(1 for outcome in outcomes if outcome.ok) == 1
    assert (tmp_path / "managed" / "helper_agent.md").exists() is True

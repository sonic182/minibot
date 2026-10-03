from __future__ import annotations

from pathlib import Path

import pytest

from minibot.adapters.agents.managed_store import LocalManagedAgentStore


def test_write_then_exists_and_list(tmp_path: Path) -> None:
    store = LocalManagedAgentStore(tmp_path / "managed")

    store.write("helper_agent", "content")

    assert store.exists("helper_agent") is True
    assert store.list_names() == ["helper_agent"]
    assert (tmp_path / "managed" / "helper_agent.md").read_text(encoding="utf-8") == "content"


def test_list_names_is_empty_before_the_directory_exists(tmp_path: Path) -> None:
    assert LocalManagedAgentStore(tmp_path / "missing").list_names() == []


def test_write_replaces_an_existing_definition(tmp_path: Path) -> None:
    store = LocalManagedAgentStore(tmp_path / "managed")

    store.write("helper_agent", "first")
    store.write("helper_agent", "second")

    assert (tmp_path / "managed" / "helper_agent.md").read_text(encoding="utf-8") == "second"


@pytest.mark.parametrize("name", ["../escape", "a/b", "ab", "with space", "helper_agent.md", ".hidden"])
def test_invalid_names_are_rejected(tmp_path: Path, name: str) -> None:
    store = LocalManagedAgentStore(tmp_path / "managed")

    with pytest.raises(ValueError, match="agent name"):
        store.write(name, "content")


def test_a_symlink_is_not_written_through(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("original", encoding="utf-8")
    (managed / "helper_agent.md").symlink_to(outside)
    store = LocalManagedAgentStore(managed)

    with pytest.raises(ValueError, match="refusing to write through the symlink"):
        store.write("helper_agent", "content")

    assert outside.read_text(encoding="utf-8") == "original"


def test_a_symlink_is_not_deleted_through(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    managed.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("original", encoding="utf-8")
    (managed / "helper_agent.md").symlink_to(outside)
    store = LocalManagedAgentStore(managed)

    with pytest.raises(ValueError, match="refusing to write through the symlink"):
        store.delete("helper_agent")

    assert outside.exists() is True


def test_an_oversized_definition_is_rejected(tmp_path: Path) -> None:
    store = LocalManagedAgentStore(tmp_path / "managed", max_write_bytes=16)

    with pytest.raises(ValueError, match="over the 16-byte limit"):
        store.write("helper_agent", "x" * 17)


def test_a_failed_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    managed = tmp_path / "managed"
    store = LocalManagedAgentStore(managed, max_write_bytes=4)

    with pytest.raises(ValueError):
        store.write("helper_agent", "too long")

    assert list(managed.glob("*.tmp")) == []


def test_delete_removes_the_definition(tmp_path: Path) -> None:
    store = LocalManagedAgentStore(tmp_path / "managed")
    store.write("helper_agent", "content")

    store.delete("helper_agent")

    assert store.exists("helper_agent") is False

from __future__ import annotations

import pytest
from pydantic import ValidationError

from minibot.adapters.config.schema import SqliteTaskQueueConfig, TasksConfig


def test_default_queue_backend_is_sqlite() -> None:
    assert TasksConfig().backend == "sqlite"


def test_sqlite_lease_must_outlive_the_worker_timeout() -> None:
    with pytest.raises(ValueError, match="lease_timeout_seconds must be greater"):
        TasksConfig(
            backend="sqlite",
            worker_timeout_seconds=300,
            sqlite=SqliteTaskQueueConfig(lease_timeout_seconds=300),
        )


def test_sqlite_lease_longer_than_worker_timeout_is_accepted() -> None:
    config = TasksConfig(
        backend="sqlite", worker_timeout_seconds=60, sqlite=SqliteTaskQueueConfig(lease_timeout_seconds=61)
    )
    assert config.sqlite.lease_timeout_seconds == 61


def test_rabbitmq_backend_ignores_the_lease_guard() -> None:
    # The lease only exists for the sqlite queue, so a long worker timeout must not be rejected here.
    config = TasksConfig(backend="rabbitmq", worker_timeout_seconds=600)
    assert config.worker_timeout_seconds == 600


def test_task_history_requires_the_sqlite_backend() -> None:
    with pytest.raises(ValidationError):
        TasksConfig(backend="rabbitmq", history=True)


@pytest.mark.parametrize(
    ("key", "value", "replacement"),
    [
        ("continue_turn_mode", "always", 'delegation_mode = "delegate"'),
        ("continue_turn_default", True, "`delegate`"),
    ],
)
def test_removed_continue_turn_keys_are_rejected_with_the_replacement(
    key: str, value: object, replacement: str
) -> None:
    with pytest.raises(ValidationError, match=f"tasks.{key} was removed") as excinfo:
        TasksConfig.model_validate({key: value})

    assert replacement in str(excinfo.value)


def test_delegation_mode_defaults_to_auto() -> None:
    assert TasksConfig().delegation_mode == "auto"

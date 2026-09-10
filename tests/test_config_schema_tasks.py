from __future__ import annotations

import pytest

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

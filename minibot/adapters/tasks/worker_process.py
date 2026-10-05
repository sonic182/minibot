from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from minibot.adapters.agents.definition_reader import LocalAgentDefinitionReader
from minibot.adapters.config.loader import load_settings
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.adapters.mcp.client import MCPClient
from minibot.app.tasks.worker import WorkerBackends
from minibot.app.tasks.worker import worker_entry as run_worker
from minibot.config.schema import Settings


def _load_worker_settings(secrets: Mapping[str, str] | None) -> Settings:
    return load_settings(secrets=secrets)


def build_managed_storage(settings: Settings) -> LocalFileStorage | None:
    if not settings.tools.file_storage.enabled:
        return None
    return LocalFileStorage(
        root_dir=settings.tools.file_storage.root_dir,
        max_write_bytes=settings.tools.file_storage.max_write_bytes,
        allow_outside_root=settings.tools.file_storage.allow_outside_root,
    )


def worker_entry(pipe: Any) -> None:
    run_worker(
        pipe,
        WorkerBackends(
            load_settings=_load_worker_settings,
            agent_reader=LocalAgentDefinitionReader(),
            build_mcp_client=MCPClient,
            build_storage=build_managed_storage,
        ),
    )

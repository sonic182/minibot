from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from llm_async.models import Tool

from minibot import __version__
from minibot.config.schema import Settings
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import empty_object_schema

_TOOL_NAMES = (
    "kv_memory",
    "http_client",
    "time",
    "wait",
    "calculator",
    "python_exec",
    "bash",
    "tool_output_spill",
    "apply_patch",
    "file_storage",
    "grep",
    "browser",
    "audio_transcription",
    "mcp",
    "skills",
    "rag",
)


class SettingsInfoTool:
    """Read-only view of the running configuration.

    Every emitted field is named here one by one. ``Settings`` carries tokens, API keys and URLs with
    inline credentials, so a denylist would leak whichever secret field is added next.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        config_path: Path | None = None,
        agent_names: Callable[[], list[str]] | None = None,
        skill_names: Callable[[], list[str]] | None = None,
    ) -> None:
        self._settings = settings
        self._config_path = config_path
        self._agent_names = agent_names
        self._skill_names = skill_names

    def bindings(self) -> list[ToolBinding]:
        schema = Tool(
            name="get_settings",
            description=load_tool_description("get_settings"),
            parameters=empty_object_schema(),
        )
        return [ToolBinding(tool=schema, handler=self._info)]

    async def _info(self, _: dict[str, object], __: ToolContext) -> dict[str, Any]:
        settings = self._settings
        result: dict[str, Any] = {
            "ok": True,
            "version": __version__,
            "config_path": self._config_path.expanduser().resolve().as_posix() if self._config_path else None,
            "cwd": Path.cwd().as_posix(),
            "channels": self._channels(),
            "llm": {
                "provider": settings.llm.provider,
                "model": settings.llm.model,
                "prompts_dir": settings.llm.prompts_dir,
            },
            "memory": {
                "backend": settings.memory.backend,
                "max_history_messages": settings.memory.max_history_messages,
                "max_history_tokens": settings.memory.max_history_tokens,
            },
            "tools": self._tools(),
            "agents": {
                "directory": settings.orchestration.directory,
                "names": self._agent_names() if self._agent_names else [],
            },
        }
        if settings.tasks.enabled:
            result["tasks"] = {"backend": settings.tasks.backend}
        if settings.scheduler.prompts.enabled:
            result["scheduler"] = {}
        if settings.vault.enabled:
            result["vault"] = {"enabled": True}
        return result

    def _channels(self) -> list[str]:
        channels = self._settings.channels
        names = ["telegram"] if channels.telegram.enabled and channels.telegram.bot_token else []
        names.extend(name for name, section in (channels.model_extra or {}).items() if section.get("enabled", True))
        return names

    def _tools(self) -> dict[str, dict[str, Any]]:
        tools = self._settings.tools
        enabled: dict[str, dict[str, Any]] = {}
        for name in _TOOL_NAMES:
            if getattr(getattr(tools, name), "enabled", False):
                enabled[name] = self._tool_details(name)
        if tools.approval.require_approval:
            enabled["approval"] = {
                "require_approval": list(tools.approval.require_approval),
                "timeout_seconds": tools.approval.timeout_seconds,
            }
        return enabled

    def _tool_details(self, name: str) -> dict[str, Any]:
        tools = self._settings.tools
        if name == "file_storage":
            storage = tools.file_storage
            return {
                "root_dir": storage.root_dir,
                "mode": "yolo" if storage.allow_outside_root else "confined",
                "max_write_bytes": storage.max_write_bytes,
            }
        if name == "skills":
            skills = tools.skills
            names = self._skill_names() if self._skill_names else []
            return {
                "paths": list(skills.paths),
                "write_path": skills.write_path,
                "native": skills.native,
                "names": names,
            }
        if name == "bash":
            bash = tools.bash
            return {
                "default_timeout_seconds": bash.default_timeout_seconds,
                "max_timeout_seconds": bash.max_timeout_seconds,
                "env_allowlist": list(bash.env_allowlist),
            }
        if name == "mcp":
            return {"servers": [server.name for server in tools.mcp.servers]}
        return {}

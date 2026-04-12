from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path
from typing import Any

from llm_async.models import Tool

from minibot.adapters.config.schema import BashToolConfig
from minibot.llm.tools.arg_utils import int_with_default, optional_str, require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import nullable_integer, nullable_string, strict_object


class BashTool:
    def __init__(self, config: BashToolConfig) -> None:
        self._config = config

    def bindings(self) -> list[ToolBinding]:
        return [ToolBinding(tool=self._schema(), handler=self._handle)]

    def _schema(self) -> Tool:
        return Tool(
            name="bash",
            description=load_tool_description("bash"),
            parameters=strict_object(
                properties={
                    "command": {"type": "string", "description": "Bash command to execute with /bin/bash -lc."},
                    "timeout_seconds": nullable_integer(minimum=1, description="Optional timeout override."),
                    "cwd": nullable_string("Optional working directory."),
                    "env": {
                        "type": ["object", "null"],
                        "description": "Optional environment variable overrides.",
                        "additionalProperties": {"type": "string"},
                    },
                },
                required=["command", "timeout_seconds", "cwd", "env"],
            ),
        )

    async def _handle(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        command = require_non_empty_str(payload, "command")
        timeout_seconds = self._coerce_timeout(payload.get("timeout_seconds"))
        cwd = self._coerce_cwd(payload.get("cwd"))
        env = self._build_env(payload.get("env"))
        started = time.perf_counter()

        try:
            process = await asyncio.create_subprocess_exec(
                "/bin/bash",
                "-lc",
                command,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                start_new_session=(os.name != "nt"),
            )
        except Exception as exc:
            return {
                "ok": False,
                "exit_code": None,
                "stdout": "",
                "stderr": str(exc),
                "timed_out": False,
                "truncated": False,
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "cwd": cwd,
                "command": command,
            }

        timed_out = False
        try:
            stdout_data, stderr_data = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            timed_out = True
            await self._terminate_process(process)
            stdout_data, stderr_data = await process.communicate()

        stdout_text, stderr_text, truncated = self._truncate_output(stdout_data, stderr_data)
        duration_ms = int((time.perf_counter() - started) * 1000)
        ok = process.returncode == 0 and not timed_out

        return {
            "ok": ok,
            "exit_code": process.returncode,
            "stdout": stdout_text,
            "stderr": stderr_text,
            "timed_out": timed_out,
            "truncated": truncated,
            "duration_ms": duration_ms,
            "cwd": cwd,
            "command": command,
        }

    def _coerce_timeout(self, value: Any) -> int:
        return int_with_default(
            value,
            default=self._config.default_timeout_seconds,
            field="timeout_seconds",
            min_value=1,
            max_value=self._config.max_timeout_seconds,
            clamp_max=True,
            type_error="timeout_seconds must be an integer",
            min_error="timeout_seconds must be >= 1",
        )

    @staticmethod
    def _coerce_cwd(value: Any) -> str:
        cwd = optional_str(value, error_message="cwd must be a string or null")
        resolved = str(Path(cwd).expanduser().resolve()) if cwd is not None else os.getcwd()
        path = Path(resolved)
        if not path.exists():
            raise ValueError(f"cwd does not exist: {resolved}")
        if not path.is_dir():
            raise ValueError(f"cwd is not a directory: {resolved}")
        return resolved

    def _build_env(self, value: Any) -> dict[str, str]:
        overrides = self._coerce_env(value)
        if self._config.pass_parent_env:
            env = dict(os.environ)
        else:
            env = {}
            for key in self._config.env_allowlist:
                current = os.environ.get(key)
                if current is not None:
                    env[key] = current
        env.update(overrides)
        return env

    @staticmethod
    def _coerce_env(value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("env must be an object or null")
        parsed: dict[str, str] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError("env keys must be non-empty strings")
            if not isinstance(item, str):
                raise ValueError("env values must be strings")
            parsed[key] = item
        return parsed

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except Exception:
            process.kill()
        await process.wait()

    def _truncate_output(self, stdout_data: bytes, stderr_data: bytes) -> tuple[str, str, bool]:
        cap = self._config.max_output_bytes
        truncated = len(stdout_data) + len(stderr_data) > cap
        if not truncated:
            return (
                stdout_data.decode("utf-8", errors="replace"),
                stderr_data.decode("utf-8", errors="replace"),
                False,
            )

        stdout_slice = stdout_data[:cap]
        remaining = max(cap - len(stdout_slice), 0)
        stderr_slice = stderr_data[:remaining]
        return (
            stdout_slice.decode("utf-8", errors="replace"),
            stderr_slice.decode("utf-8", errors="replace"),
            True,
        )

from __future__ import annotations

import asyncio
import hashlib
import os
import signal
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from llm_async.models import Tool

from minibot.adapters.config.schema import BashToolConfig
from minibot.llm.tools.arg_utils import int_with_default, optional_str, require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import nullable_integer, nullable_string, strict_object

if TYPE_CHECKING:
    from minibot.adapters.files.local_storage import LocalFileStorage


class BashTool:
    """Execute shell commands via ``/bin/bash -lc``.

    Enabled by ``[tools.bash]`` in ``config.toml``.

    Key config options:

    - ``default_timeout_seconds`` / ``max_timeout_seconds`` — execution time limits.
    - ``pass_parent_env`` — pass the full parent environment; when ``false``, only
      keys in ``env_allowlist`` are forwarded.
    - ``max_output_bytes`` — combined stdout+stderr cap; excess is truncated.
    - ``spill_to_managed_file`` — when ``true``, output exceeding ``spill_after_chars``
      is saved to a managed temp file instead of being returned inline.

    Returns ``ok``, ``exit_code``, ``stdout``, ``stderr``, ``timed_out``,
    ``truncated``, and ``duration_ms``.
    """

    def __init__(self, config: BashToolConfig, storage: LocalFileStorage | None = None) -> None:
        self._config = config
        self._storage = storage

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

        duration_ms = int((time.perf_counter() - started) * 1000)
        ok = process.returncode == 0 and not timed_out

        if self._should_spill(stdout_data, stderr_data):
            spill_info = self._save_spilled_output(command, stdout_data, stderr_data)
            if spill_info is not None:
                stdout_text, stderr_text, truncated = self._truncate_output(stdout_data, stderr_data)
                preview_len = self._config.spill_preview_chars
                return {
                    "ok": ok,
                    "exit_code": process.returncode,
                    "stdout_storage": "managed_file",
                    "stdout_file_path": spill_info["path"],
                    "stdout_file_absolute_path": spill_info["absolute_path"],
                    "stdout_bytes_written": spill_info["bytes_written"],
                    "stdout_preview": (stdout_text + stderr_text)[:preview_len],
                    "stdout_notice": (
                        f"Output exceeded {self._config.spill_after_chars} chars and was saved to a managed temp file."
                        " Use grep or read on stdout_file_absolute_path to inspect the full output."
                    ),
                    "timed_out": timed_out,
                    "truncated": truncated,
                    "duration_ms": duration_ms,
                    "cwd": cwd,
                    "command": command,
                }

        stdout_text, stderr_text, truncated = self._truncate_output(stdout_data, stderr_data)

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

    def _should_spill(self, stdout: bytes, stderr: bytes) -> bool:
        return (
            self._config.spill_to_managed_file
            and self._storage is not None
            and len(stdout) + len(stderr) > self._config.spill_after_chars
        )

    def _save_spilled_output(self, command: str, stdout: bytes, stderr: bytes) -> dict[str, str | int] | None:
        try:
            if self._storage is None:
                return None
            digest = hashlib.sha256(command.encode("utf-8")).hexdigest()[:8]
            stem = f"bash-{digest}"
            return self._storage.create_managed_temp_bytes_file(
                subdir=self._config.spill_subdir,
                stem=stem,
                suffix=".txt",
                content=stdout + stderr,
            )
        except Exception:  # noqa: BLE001
            return None

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

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile
import time
from typing import Any

from llm_async.models import Tool

from minibot.adapters.config.schema import PythonExecToolConfig
from minibot.llm.tools.base import ToolBinding, ToolContext


class HostPythonExecTool:
    def __init__(self, config: PythonExecToolConfig) -> None:
        self._config = config
        self._logger = logging.getLogger("minibot.python_exec")

    def bindings(self) -> list[ToolBinding]:
        return [ToolBinding(tool=self._schema(), handler=self._handle)]

    def _schema(self) -> Tool:
        return Tool(
            name="python_execute",
            description=(
                "Execute arbitrary Python code on host backend with configurable timeout and "
                "best-effort sandbox controls."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python source code to execute.",
                    },
                    "stdin": {
                        "type": ["string", "null"],
                        "description": "Optional stdin text sent to the process.",
                    },
                    "timeout_seconds": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "description": "Optional timeout for this execution.",
                    },
                },
                "required": ["code", "stdin", "timeout_seconds"],
                "additionalProperties": False,
            },
        )

    async def _handle(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        self._logger.debug(
            "python exec request received",
            extra={
                "sandbox_mode": self._config.sandbox_mode,
                "has_stdin": payload.get("stdin") is not None,
                "has_timeout_override": payload.get("timeout_seconds") is not None,
            },
        )
        code = payload.get("code")
        if not isinstance(code, str):
            return {"ok": False, "error": "code must be a string"}
        if not code.strip():
            return {"ok": False, "error": "code cannot be empty"}
        code_size = len(code.encode("utf-8"))
        if code_size > self._config.max_code_bytes:
            return {
                "ok": False,
                "error": f"code size exceeds limit {self._config.max_code_bytes} bytes",
            }
        stdin = payload.get("stdin")
        if stdin is not None and not isinstance(stdin, str):
            return {"ok": False, "error": "stdin must be a string or null"}

        timeout_seconds = self._coerce_timeout(payload.get("timeout_seconds"))
        executable = self._resolve_python_executable()
        self._logger.debug(
            "python exec runtime resolved",
            extra={
                "python_executable": executable,
                "timeout_seconds": timeout_seconds,
                "code_bytes": code_size,
            },
        )
        started = time.perf_counter()

        try:
            result = await self._execute(
                code=code,
                stdin=stdin,
                timeout_seconds=timeout_seconds,
                executable=executable,
            )
        except Exception as exc:
            self._logger.exception("python exec failed before process start", exc_info=exc)
            return {
                "ok": False,
                "error": str(exc),
                "timed_out": False,
            }

        duration_ms = int((time.perf_counter() - started) * 1000)
        result["duration_ms"] = duration_ms
        result["sandbox_mode"] = result.get("sandbox_mode") or self._config.sandbox_mode
        result["python_executable"] = executable
        self._logger.debug(
            "python exec completed",
            extra={
                "ok": result.get("ok"),
                "exit_code": result.get("exit_code"),
                "timed_out": result.get("timed_out"),
                "truncated": result.get("truncated"),
                "duration_ms": duration_ms,
                "sandbox_mode": result.get("sandbox_mode"),
            },
        )
        return result

    def _coerce_timeout(self, value: Any) -> int:
        max_timeout = self._config.max_timeout_seconds
        if value is None:
            return self._config.default_timeout_seconds
        if isinstance(value, bool):
            raise ValueError("timeout_seconds must be an integer")
        if isinstance(value, int):
            timeout = value
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return self._config.default_timeout_seconds
            timeout = int(stripped)
        else:
            raise ValueError("timeout_seconds must be an integer")
        if timeout < 1:
            raise ValueError("timeout_seconds must be >= 1")
        if timeout > max_timeout:
            return max_timeout
        return timeout

    def _resolve_python_executable(self) -> str:
        explicit_path = (self._config.python_path or "").strip()
        if explicit_path:
            candidate = Path(explicit_path)
            if not candidate.exists():
                raise ValueError(f"python_path does not exist: {explicit_path}")
            return str(candidate)

        venv_path = (self._config.venv_path or "").strip()
        if venv_path:
            root = Path(venv_path)
            if os.name == "nt":
                candidate = root / "Scripts" / "python.exe"
            else:
                candidate = root / "bin" / "python"
            if not candidate.exists():
                raise ValueError(f"venv interpreter not found at: {candidate}")
            return str(candidate)

        return sys.executable

    async def _execute(self, code: str, stdin: str | None, timeout_seconds: int, executable: str) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="minibot_pyexec_") as tmp_dir:
            script_path = Path(tmp_dir) / "snippet.py"
            script_path.write_text(code, encoding="utf-8")

            mode = self._config.sandbox_mode
            command = [executable, "-u", "-B", "-I", str(script_path)]
            sandbox_applied = mode
            preexec_fn = None

            if mode == "jail":
                command, sandbox_applied = self._wrap_jail(command)
            elif mode == "cgroup":
                command, sandbox_applied = self._wrap_cgroup(command)
            elif mode == "rlimit":
                preexec_fn, sandbox_applied = self._build_rlimit_preexec()

            self._logger.debug(
                "python exec launching process",
                extra={
                    "sandbox_requested": mode,
                    "sandbox_applied": sandbox_applied,
                    "command_argv0": command[0] if command else "",
                    "cwd": tmp_dir,
                    "timeout_seconds": timeout_seconds,
                },
            )

            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=tmp_dir,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._build_env(),
                preexec_fn=preexec_fn,
                start_new_session=(os.name != "nt"),
            )

            input_bytes = stdin.encode("utf-8") if stdin is not None else None
            timed_out = False
            try:
                stdout_data, stderr_data = await asyncio.wait_for(
                    process.communicate(input=input_bytes),
                    timeout=timeout_seconds,
                )
            except asyncio.TimeoutError:
                timed_out = True
                self._logger.warning(
                    "python exec timed out",
                    extra={
                        "pid": process.pid,
                        "timeout_seconds": timeout_seconds,
                        "sandbox_mode": sandbox_applied,
                    },
                )
                await self._terminate_process(process)
                stdout_data, stderr_data = await process.communicate()

            stdout_text, stderr_text, truncated = self._truncate_output(stdout_data, stderr_data)

            self._logger.debug(
                "python exec process ended",
                extra={
                    "pid": process.pid,
                    "returncode": process.returncode,
                    "stdout_bytes": len(stdout_data),
                    "stderr_bytes": len(stderr_data),
                    "truncated": truncated,
                    "sandbox_mode": sandbox_applied,
                },
            )

            return {
                "ok": process.returncode == 0 and not timed_out,
                "exit_code": process.returncode,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "timed_out": timed_out,
                "truncated": truncated,
                "sandbox_mode": sandbox_applied,
            }

    def _wrap_jail(self, command: list[str]) -> tuple[list[str], str]:
        jail_cfg = self._config.jail
        if not jail_cfg.enabled:
            self._logger.debug("python exec jail disabled; using basic mode")
            return command, "basic"
        prefix = list(jail_cfg.command_prefix)
        if not prefix:
            self._logger.debug("python exec jail prefix missing; using basic mode")
            return command, "basic"
        if shutil.which(prefix[0]) is None:
            self._logger.warning(
                "python exec jail binary not found; using basic mode",
                extra={"jail_binary": prefix[0]},
            )
            return command, "basic"
        self._logger.debug(
            "python exec jail wrapper applied",
            extra={"jail_binary": prefix[0]},
        )
        return prefix + command, "jail"

    def _wrap_cgroup(self, command: list[str]) -> tuple[list[str], str]:
        cgroup_cfg = self._config.cgroup
        if not cgroup_cfg.enabled:
            self._logger.debug("python exec cgroup disabled; using basic mode")
            return command, "basic"
        if os.name != "posix" or sys.platform != "linux":
            self._logger.debug("python exec cgroup unsupported platform; using basic mode")
            return command, "basic"
        if cgroup_cfg.driver != "systemd":
            self._logger.debug("python exec cgroup unsupported driver; using basic mode")
            return command, "basic"
        if shutil.which("systemd-run") is None:
            self._logger.warning("python exec systemd-run missing; using basic mode")
            return command, "basic"
        wrapped = ["systemd-run", "--user", "--scope", "--quiet"]
        if cgroup_cfg.memory_max_mb is not None:
            wrapped.extend(["-p", f"MemoryMax={cgroup_cfg.memory_max_mb}M"])
        if cgroup_cfg.cpu_quota_percent is not None:
            wrapped.extend(["-p", f"CPUQuota={cgroup_cfg.cpu_quota_percent}%"])
        wrapped.extend(command)
        self._logger.debug("python exec cgroup wrapper applied")
        return wrapped, "cgroup"

    def _build_rlimit_preexec(self) -> tuple[Any, str]:
        rlimit_cfg = self._config.rlimit
        if not rlimit_cfg.enabled:
            self._logger.debug("python exec rlimit disabled; using basic mode")
            return None, "basic"
        if os.name != "posix":
            self._logger.debug("python exec rlimit unsupported platform; using basic mode")
            return None, "basic"

        import resource

        cpu_seconds = rlimit_cfg.cpu_seconds
        memory_mb = rlimit_cfg.memory_mb
        fsize_mb = rlimit_cfg.fsize_mb
        nproc = rlimit_cfg.nproc
        nofile = rlimit_cfg.nofile

        def _apply_limits() -> None:
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            if cpu_seconds is not None:
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            if memory_mb is not None:
                memory_bytes = memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            if fsize_mb is not None:
                size_bytes = fsize_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_FSIZE, (size_bytes, size_bytes))
            if nproc is not None and hasattr(resource, "RLIMIT_NPROC"):
                resource.setrlimit(resource.RLIMIT_NPROC, (nproc, nproc))
            if nofile is not None:
                resource.setrlimit(resource.RLIMIT_NOFILE, (nofile, nofile))

        self._logger.debug(
            "python exec rlimit preexec prepared",
            extra={
                "cpu_seconds": cpu_seconds,
                "memory_mb": memory_mb,
                "fsize_mb": fsize_mb,
                "nproc": nproc,
                "nofile": nofile,
            },
        )
        return _apply_limits, "rlimit"

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

    def _build_env(self) -> dict[str, str]:
        if self._config.pass_parent_env:
            env = dict(os.environ)
            self._logger.debug("python exec environment uses parent env")
        else:
            env = {}
            for key in self._config.env_allowlist:
                value = os.environ.get(key)
                if value is not None:
                    env[key] = value
            self._logger.debug(
                "python exec environment uses allowlist",
                extra={"allowlist_size": len(self._config.env_allowlist), "injected_size": len(env)},
            )
        env["PYTHONUNBUFFERED"] = "1"
        return env

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

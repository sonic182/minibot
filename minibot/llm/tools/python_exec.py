from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
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
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.llm.tools.base import ToolBinding, ToolContext


class HostPythonExecTool:
    def __init__(self, config: PythonExecToolConfig, storage: LocalFileStorage | None = None) -> None:
        self._config = config
        self._storage = storage
        self._logger = logging.getLogger("minibot.python_exec")

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._schema(), handler=self._handle),
            ToolBinding(tool=self._environment_schema(), handler=self._handle_environment_info),
        ]

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
                    "save_artifacts": {
                        "type": ["boolean", "null"],
                        "description": "When true, save generated files into managed files storage.",
                    },
                    "artifact_globs": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                        "description": "Optional glob patterns to select generated files (for example ['*.png']).",
                    },
                    "artifact_subdir": {
                        "type": ["string", "null"],
                        "description": "Destination subdirectory under managed files root.",
                    },
                    "max_artifacts": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "description": "Optional per-run cap for exported artifact count.",
                    },
                },
                "required": [
                    "code",
                    "stdin",
                    "timeout_seconds",
                    "save_artifacts",
                    "artifact_globs",
                    "artifact_subdir",
                    "max_artifacts",
                ],
                "additionalProperties": False,
            },
        )

    def _environment_schema(self) -> Tool:
        return Tool(
            name="python_environment_info",
            description=(
                "Return details about the configured Python runtime and installed packages available "
                "to python_execute."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "include_packages": {
                        "type": ["boolean", "null"],
                        "description": "When true, include installed packages.",
                    },
                    "limit": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "description": "Maximum packages returned when include_packages is true.",
                    },
                    "name_prefix": {
                        "type": ["string", "null"],
                        "description": "Optional case-insensitive package prefix filter.",
                    },
                },
                "required": ["include_packages", "limit", "name_prefix"],
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
        try:
            artifact_options = self._coerce_artifact_options(payload)
        except Exception as exc:
            return {
                "ok": False,
                "error_code": "invalid_artifact_options",
                "error": str(exc),
                "timed_out": False,
            }
        if artifact_options["enabled"] and not self._config.artifacts_enabled:
            return {
                "ok": False,
                "error_code": "artifacts_disabled",
                "error": "artifact export is disabled by tools.python_exec.artifacts_enabled",
                "timed_out": False,
            }
        if artifact_options["enabled"] and self._storage is None:
            return {
                "ok": False,
                "error_code": "file_storage_unavailable",
                "error": "artifact export requires tools.file_storage.enabled = true",
                "timed_out": False,
            }
        if (
            artifact_options["enabled"]
            and self._config.sandbox_mode == "jail"
            and not self._config.artifacts_allow_in_jail
        ):
            return {
                "ok": False,
                "error_code": "artifacts_not_supported_in_jail",
                "error": (
                    "artifact export is blocked in sandbox_mode='jail'. "
                    "Set tools.python_exec.artifacts_allow_in_jail=true and configure artifacts_jail_shared_dir"
                ),
                "timed_out": False,
            }
        if artifact_options["enabled"] and self._config.sandbox_mode == "jail":
            shared_dir = (self._config.artifacts_jail_shared_dir or "").strip()
            if not shared_dir:
                return {
                    "ok": False,
                    "error_code": "artifact_export_unreachable",
                    "error": "tools.python_exec.artifacts_jail_shared_dir must be configured for jail artifact export",
                    "timed_out": False,
                }
            try:
                self._resolve_jail_shared_dir()
            except Exception as exc:
                return {
                    "ok": False,
                    "error_code": "artifact_export_unreachable",
                    "error": str(exc),
                    "timed_out": False,
                }
        executable = self._resolve_python_executable()
        self._logger.debug(
            "python exec runtime resolved",
            extra={
                "python_executable": executable,
                "timeout_seconds": timeout_seconds,
                "code_bytes": code_size,
                "save_artifacts": artifact_options["enabled"],
            },
        )
        started = time.perf_counter()

        try:
            result = await self._execute(
                code=code,
                stdin=stdin,
                timeout_seconds=timeout_seconds,
                executable=executable,
                artifact_options=artifact_options,
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

    async def _handle_environment_info(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        include_packages = self._coerce_optional_bool(payload.get("include_packages"), default=True)
        limit = self._coerce_package_limit(payload.get("limit"))
        name_prefix = self._coerce_prefix(payload.get("name_prefix"))
        executable = self._resolve_python_executable()
        timeout_seconds = self._config.default_timeout_seconds

        self._logger.debug(
            "python env info request received",
            extra={
                "python_executable": executable,
                "include_packages": include_packages,
                "limit": limit,
                "name_prefix": name_prefix,
            },
        )

        script = self._environment_probe_script(
            include_packages=include_packages,
            limit=limit,
            name_prefix=name_prefix,
        )
        started = time.perf_counter()
        try:
            execution_result = await self._execute(
                code=script,
                stdin=None,
                timeout_seconds=timeout_seconds,
                executable=executable,
                artifact_options={
                    "enabled": False,
                    "patterns": [],
                    "subdir": self._config.artifacts_default_subdir,
                    "max_artifacts": self._config.artifacts_max_files,
                },
            )
        except Exception as exc:
            self._logger.exception("python env info failed before process start", exc_info=exc)
            return {
                "ok": False,
                "error": str(exc),
                "timed_out": False,
                "python_executable": executable,
            }

        duration_ms = int((time.perf_counter() - started) * 1000)
        if not execution_result.get("ok"):
            error_text = execution_result.get("stderr") or execution_result.get("stdout") or "environment probe failed"
            return {
                "ok": False,
                "error": error_text,
                "exit_code": execution_result.get("exit_code"),
                "timed_out": execution_result.get("timed_out", False),
                "truncated": execution_result.get("truncated", False),
                "duration_ms": duration_ms,
                "sandbox_mode": execution_result.get("sandbox_mode") or self._config.sandbox_mode,
                "python_executable": executable,
            }

        try:
            parsed = json.loads(execution_result.get("stdout") or "{}")
        except Exception as exc:
            self._logger.warning("python env info JSON parse failed", extra={"error": str(exc)})
            return {
                "ok": False,
                "error": "failed to parse environment probe output",
                "raw_stdout": execution_result.get("stdout", "")[:600],
                "duration_ms": duration_ms,
                "sandbox_mode": execution_result.get("sandbox_mode") or self._config.sandbox_mode,
                "python_executable": executable,
            }

        parsed["ok"] = True
        parsed["duration_ms"] = duration_ms
        parsed["sandbox_mode"] = execution_result.get("sandbox_mode") or self._config.sandbox_mode
        parsed["python_executable"] = executable
        return parsed

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

    def _coerce_package_limit(self, value: Any) -> int:
        default_limit = 100
        max_limit = 500
        if value is None:
            return default_limit
        if isinstance(value, bool):
            raise ValueError("limit must be an integer")
        if isinstance(value, int):
            limit = value
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return default_limit
            limit = int(stripped)
        else:
            raise ValueError("limit must be an integer")
        if limit < 1:
            raise ValueError("limit must be >= 1")
        return min(limit, max_limit)

    @staticmethod
    def _coerce_optional_bool(value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
        raise ValueError("include_packages must be a boolean")

    @staticmethod
    def _coerce_prefix(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("name_prefix must be a string")
        stripped = value.strip()
        return stripped or None

    def _coerce_artifact_options(self, payload: dict[str, Any]) -> dict[str, Any]:
        save_raw = payload.get("save_artifacts")
        save_artifacts = False if save_raw is None else self._coerce_optional_bool(save_raw, default=False)
        patterns = self._coerce_artifact_globs(payload.get("artifact_globs"))
        subdir = self._coerce_artifact_subdir(payload.get("artifact_subdir"))
        max_artifacts = self._coerce_max_artifacts(payload.get("max_artifacts"))
        return {
            "enabled": save_artifacts,
            "patterns": patterns,
            "subdir": subdir,
            "max_artifacts": max_artifacts,
        }

    @staticmethod
    def _coerce_artifact_globs(value: Any) -> list[str]:
        if value is None:
            return ["*.png", "*.jpg", "*.jpeg", "*.pdf", "*.csv", "*.txt", "*.json", "*.svg"]
        if not isinstance(value, list):
            raise ValueError("artifact_globs must be an array of strings")
        parsed: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("artifact_globs must contain only strings")
            stripped = item.strip()
            if stripped:
                parsed.append(stripped)
        if not parsed:
            raise ValueError("artifact_globs cannot be empty when provided")
        return parsed

    def _coerce_artifact_subdir(self, value: Any) -> str:
        if value is None:
            return self._config.artifacts_default_subdir.strip() or "generated"
        if not isinstance(value, str):
            raise ValueError("artifact_subdir must be a string")
        cleaned = value.strip().replace("\\", "/").strip("/")
        if not cleaned:
            return self._config.artifacts_default_subdir.strip() or "generated"
        return cleaned

    def _coerce_max_artifacts(self, value: Any) -> int:
        config_max = self._config.artifacts_max_files
        if value is None:
            return config_max
        if isinstance(value, bool):
            raise ValueError("max_artifacts must be an integer")
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return config_max
            parsed = int(stripped)
        else:
            raise ValueError("max_artifacts must be an integer")
        if parsed < 1:
            raise ValueError("max_artifacts must be >= 1")
        return min(parsed, config_max)

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

    @staticmethod
    def _environment_probe_script(include_packages: bool, limit: int, name_prefix: str | None) -> str:
        prefix_literal = json.dumps(name_prefix or "")
        include_literal = "True" if include_packages else "False"
        return (
            "import json\n"
            "import platform\n"
            "import sys\n"
            "from importlib import metadata\n"
            f"include_packages = {include_literal}\n"
            f"limit = {limit}\n"
            f"name_prefix = {prefix_literal}.lower()\n"
            "packages = []\n"
            "package_count = 0\n"
            "truncated_packages = False\n"
            "if include_packages:\n"
            "    collected = []\n"
            "    for dist in metadata.distributions():\n"
            "        name = (dist.metadata.get('Name') or '').strip()\n"
            "        if not name:\n"
            "            continue\n"
            "        if name_prefix and not name.lower().startswith(name_prefix):\n"
            "            continue\n"
            "        collected.append((name, str(dist.version or '')))\n"
            "    collected.sort(key=lambda item: item[0].lower())\n"
            "    package_count = len(collected)\n"
            "    selected = collected[:limit]\n"
            "    truncated_packages = package_count > len(selected)\n"
            "    for name, version in selected:\n"
            "        packages.append(f'{name}=={version}' if version else name)\n"
            "result = {\n"
            "    'runtime_executable': sys.executable,\n"
            "    'python_version': sys.version.split()[0],\n"
            "    'implementation': platform.python_implementation(),\n"
            "    'include_packages': include_packages,\n"
            "    'name_prefix': name_prefix or None,\n"
            "    'limit': limit,\n"
            "    'package_count': package_count,\n"
            "    'truncated_packages': truncated_packages,\n"
            "    'packages': packages,\n"
            "}\n"
            "print(json.dumps(result, ensure_ascii=True))\n"
        )

    async def _execute(
        self,
        code: str,
        stdin: str | None,
        timeout_seconds: int,
        executable: str,
        artifact_options: dict[str, Any],
    ) -> dict[str, Any]:
        mode = self._config.sandbox_mode
        run_dir: Path
        cleanup_path: Path | None = None
        if mode == "jail" and artifact_options["enabled"]:
            shared_root = self._resolve_jail_shared_dir()
            run_dir = Path(tempfile.mkdtemp(prefix="minibot_pyexec_", dir=str(shared_root))).resolve()
            cleanup_path = run_dir
        else:
            run_dir = Path(tempfile.mkdtemp(prefix="minibot_pyexec_")).resolve()
            cleanup_path = run_dir

        try:
            command = [executable, "-u", "-B", "-I", "-c", code]
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
                    "cwd": str(run_dir),
                    "timeout_seconds": timeout_seconds,
                },
            )

            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(run_dir),
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
            artifacts_saved: list[dict[str, Any]] = []
            artifacts_skipped: list[dict[str, Any]] = []
            if artifact_options["enabled"]:
                artifacts_saved, artifacts_skipped = self._collect_artifacts(run_dir, artifact_options)

            self._logger.debug(
                "python exec process ended",
                extra={
                    "pid": process.pid,
                    "returncode": process.returncode,
                    "stdout_bytes": len(stdout_data),
                    "stderr_bytes": len(stderr_data),
                    "stderr_preview": stderr_data[:300].decode("utf-8", errors="replace"),
                    "truncated": truncated,
                    "sandbox_mode": sandbox_applied,
                    "artifacts_saved": len(artifacts_saved),
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
                "artifacts_saved": artifacts_saved,
                "artifacts_skipped": artifacts_skipped,
            }
        finally:
            if cleanup_path is not None:
                shutil.rmtree(cleanup_path, ignore_errors=True)

    def _resolve_jail_shared_dir(self) -> Path:
        shared_dir = (self._config.artifacts_jail_shared_dir or "").strip()
        if not shared_dir:
            raise ValueError("artifacts_jail_shared_dir is required when exporting artifacts in jail mode")
        root = Path(shared_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise ValueError("artifacts_jail_shared_dir must be a directory")
        return root

    def _collect_artifacts(
        self, run_dir: Path, artifact_options: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if self._storage is None:
            return [], [{"name": "*", "reason": "file_storage_unavailable"}]

        patterns = artifact_options["patterns"]
        subdir = artifact_options["subdir"]
        max_artifacts = int(artifact_options["max_artifacts"])
        allowed_ext = {ext.lower() for ext in self._config.artifacts_allowed_extensions}
        max_file_bytes = int(self._config.artifacts_max_file_bytes)
        max_total_bytes = int(self._config.artifacts_max_total_bytes)

        candidates: list[Path] = []
        seen: set[str] = set()
        for pattern in patterns:
            for file_path in sorted(run_dir.rglob(pattern)):
                if not file_path.is_file():
                    continue
                key = str(file_path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(file_path)

        saved: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        total_saved_bytes = 0

        for source_path in candidates:
            rel_name = str(source_path.relative_to(run_dir)).replace("\\", "/")
            if len(saved) >= max_artifacts:
                skipped.append({"name": rel_name, "reason": "max_artifacts_reached"})
                continue

            suffix = source_path.suffix.lower()
            if allowed_ext and suffix not in allowed_ext:
                skipped.append({"name": rel_name, "reason": "extension_not_allowed"})
                continue

            size_bytes = int(source_path.stat().st_size)
            if size_bytes > max_file_bytes:
                skipped.append({"name": rel_name, "reason": "file_too_large"})
                continue
            if total_saved_bytes + size_bytes > max_total_bytes:
                skipped.append({"name": rel_name, "reason": "total_size_limit"})
                continue

            destination_rel = self._allocate_artifact_destination(subdir=subdir, filename=source_path.name)
            destination_abs = self._storage.resolve_file(destination_rel)
            destination_abs.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination_abs)

            mime_type, _ = mimetypes.guess_type(str(destination_abs), strict=False)
            saved.append(
                {
                    "path": destination_rel,
                    "name": destination_abs.name,
                    "size_bytes": size_bytes,
                    "mime": mime_type or "application/octet-stream",
                }
            )
            total_saved_bytes += size_bytes

        return saved, skipped

    def _allocate_artifact_destination(self, subdir: str, filename: str) -> str:
        safe_subdir = subdir.replace("\\", "/").strip("/")
        candidate = f"{safe_subdir}/{filename}" if safe_subdir else filename
        target = self._storage.resolve_file(candidate) if self._storage is not None else Path(candidate)
        if not target.exists():
            return candidate

        stem = Path(filename).stem or "artifact"
        suffix = Path(filename).suffix
        for index in range(1, 1000):
            variant_name = f"{stem}-{index}{suffix}"
            variant_rel = f"{safe_subdir}/{variant_name}" if safe_subdir else variant_name
            variant_target = (
                self._storage.resolve_file(variant_rel) if self._storage is not None else Path(variant_rel)
            )
            if not variant_target.exists():
                return variant_rel
        raise ValueError("unable to allocate artifact destination path")

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

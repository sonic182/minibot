from __future__ import annotations

import asyncio
import json
import logging
import signal
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from minibot.adapters.config.loader import load_settings
from minibot.adapters.config.schema import Settings
from minibot.adapters.files.local_storage import LocalFileStorage
from minibot.adapters.mcp.client import MCPClient
from minibot.app.agent_definitions_loader import load_agent_specs
from minibot.app.agent_policies import filter_tools_for_agent, strip_reserved_delegation_tools
from minibot.app.agent_registry import AgentRegistry
from minibot.app.agent_runtime import AgentRuntime
from minibot.app.environment_context import build_environment_prompt_fragment
from minibot.app.event_bus import EventBus
from minibot.app.extensions import load_extensions
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.app.response_parser import extract_answer, resolve_reply_render
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart, RuntimeLimits
from minibot.core.agents import AgentSpec
from minibot.core.tasks import TaskLimits, TaskStopReason
from minibot.llm.errors import ProviderHTTPError
from minibot.llm.tools.apply_patch import ApplyPatchTool
from minibot.llm.tools.audio_transcription import AudioTranscriptionTool
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.bash import BashTool
from minibot.llm.tools.calculator import CalculatorTool
from minibot.llm.tools.code_read import CodeReadTool
from minibot.llm.tools.file_storage import FileStorageTool
from minibot.llm.tools.grep import GrepTool
from minibot.llm.tools.http_client import HTTPClientTool
from minibot.llm.tools.mcp_bridge import build_mcp_bindings
from minibot.llm.tools.output_spill import apply_tool_output_spill
from minibot.llm.tools.python_exec import HostPythonExecTool
from minibot.llm.tools.time import CurrentTimeTool
from minibot.shared.utils import session_identifier, validate_attachments

_LOGGER = logging.getLogger("minibot.task_worker")
_WORKER_SPEC_PATH = Path("<task_worker>")
_WORKER_TOOL_ALLOWLIST = [
    "current_datetime",
    "calculate_expression",
    "http_request",
    "filesystem",
    "glob_files",
    "read_file",
    "code_read",
    "grep",
    "bash",
    "python_execute",
    "python_environment_info",
    "apply_patch",
    "transcribe_audio",
]
_WORKER_SYSTEM_PROMPT_SUFFIX = (
    "You are an isolated task worker.\n"
    "Complete the assigned task using the available tools when useful.\n"
    "Do not delegate to other agents or attempt to spawn additional tasks.\n"
    "Do not keep probing large HTML or JavaScript assets unless the answer cannot be obtained otherwise.\n"
    "Prefer targeted reads, focused grep searches, and small excerpts over broad page or script inspection.\n"
    "Avoid fetching linked JavaScript assets unless the page itself clearly points to required data living there.\n"
    "Return only the task result needed by the main agent."
)


def worker_entry(pipe: Any) -> None:
    # The worker is forked from the daemon, which installs an asyncio no-op SIGTERM/SIGINT
    # handler for graceful shutdown; forked children inherit that disposition, so
    # TaskManager.cancel()'s proc.terminate() would otherwise be swallowed instead of
    # killing this process. Reset to default so terminate() actually stops the worker.
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    asyncio.run(_worker_async(pipe))


async def _worker_async(pipe: Any) -> None:
    async with pipe.open() as (rx, tx):
        raw = await rx.readline()

        async def emit_progress(progress: dict[str, Any]) -> None:
            tx.write(json.dumps({"type": "progress", "progress": progress}).encode() + b"\n")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            result = {
                "type": "result",
                "task_id": "",
                "status": "failed",
                "error": "invalid task payload",
                "stop_reason": TaskStopReason.INVALID_RESULT.value,
                "metadata": {"error_type": "invalid_payload"},
            }
        else:
            result = await run_agent_loop(payload, progress_callback=emit_progress)
        tx.write(json.dumps(result).encode() + b"\n")


async def run_agent_loop(
    task: dict[str, Any],
    progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    task_id = str(task.get("task_id") or "")
    try:
        channel = _require_string(task.get("channel"), "channel")
        prompt = _require_string(task.get("prompt"), "prompt")
        settings = load_settings()
        extensions = load_extensions(settings, EventBus(), _LOGGER, entrypoint="worker")
        llm_factory = LLMClientFactory(settings)
        environment_prompt_fragment = build_environment_prompt_fragment(settings)
        spec = _resolve_task_spec(
            settings=settings,
            llm_factory=llm_factory,
            environment_prompt_fragment=environment_prompt_fragment,
            task=task,
            extension_tool_names=[binding.tool.name for binding in extensions.tools],
        )
        llm_client = llm_factory.create_for_agent(spec)
        tools = _build_worker_tools(settings=settings, spec=spec, extension_tools=extensions.tools)
        limits = _task_limits(task, settings)
        runtime = AgentRuntime(
            llm_client=llm_client,
            tools=tools,
            limits=RuntimeLimits(
                max_steps=limits.max_steps,
                max_tool_calls=limits.max_tool_calls,
                timeout_seconds=limits.timeout_seconds,
            ),
            allowed_append_message_tools=[],
            allow_system_inserts=False,
            managed_files_root=settings.tools.file_storage.root_dir if settings.tools.file_storage.enabled else None,
        )
        state = _build_worker_state(
            spec=spec,
            prompt=prompt,
            context=_coerce_context(task.get("context")),
        )
        tool_context = ToolContext(
            owner_id=settings.runtime.owner_id,
            channel=channel,
            chat_id=_coerce_int(task.get("chat_id")),
            user_id=_coerce_int(task.get("user_id")),
        )
        prompt_cache_key = _worker_prompt_cache_key(tool_context=tool_context, task_id=task_id)
        generation = await runtime.run(
            state=state,
            tool_context=tool_context,
            prompt_cache_key=prompt_cache_key,
            initial_previous_response_id=None,
            progress_callback=progress_callback,
        )
        parsed = extract_answer(generation.payload, pre_response_meta=generation.pre_response_meta)
        render = resolve_reply_render(parsed)
        text = render.text
        metadata = {
            "tool_count": sum(1 for message in generation.state.messages if message.role == "tool"),
            "model": llm_client.model_name(),
            "provider": llm_client.provider_name(),
            "agent_name": spec.name,
            "total_tokens": getattr(generation, "total_tokens", 0),
            "provider_tool_calls": getattr(generation, "provider_tool_calls", 0),
            "managed_files_root": settings.tools.file_storage.root_dir
            if settings.tools.file_storage.enabled
            else None,
        }
        attachments = validate_attachments((generation.pre_response_meta or {}).get("attachments"))
        stop_reason = getattr(generation, "stop_reason", TaskStopReason.COMPLETED)
        if stop_reason is not TaskStopReason.COMPLETED:
            return {
                "type": "result",
                "task_id": task_id,
                "status": "failed",
                "error": text,
                "stop_reason": stop_reason.value,
                "metadata": metadata,
            }
        return {
            "type": "result",
            "task_id": task_id,
            "status": "done",
            "text": text,
            "attachments": attachments,
            "stop_reason": TaskStopReason.COMPLETED.value,
            "metadata": metadata,
        }
    except TimeoutError:
        return {
            "type": "result",
            "task_id": task_id,
            "status": "timed_out",
            "error": "task worker timed out",
            "stop_reason": TaskStopReason.TIMEOUT.value,
            "metadata": {},
        }
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("task worker failed", exc_info=exc, extra={"task_id": task_id or "unknown"})
        return {
            "type": "result",
            "task_id": task_id,
            "status": "failed",
            "error": str(exc),
            "stop_reason": _stop_reason_for_error(exc).value,
            "metadata": _build_error_metadata(exc),
        }


def _build_worker_tools(
    *, settings: Settings, spec: AgentSpec, extension_tools: Sequence[ToolBinding] = ()
) -> list[ToolBinding]:
    bindings: list[ToolBinding] = []
    managed_storage = _build_managed_storage(settings)

    if settings.tools.time.enabled:
        bindings.extend(CurrentTimeTool(settings.tools.time.default_format).bindings())
    if settings.tools.calculator.enabled:
        bindings.extend(
            CalculatorTool(
                default_scale=settings.tools.calculator.default_scale,
                max_expression_length=settings.tools.calculator.max_expression_length,
                max_exponent_abs=settings.tools.calculator.max_exponent_abs,
            ).bindings()
        )
    if settings.tools.http_client.enabled:
        bindings.extend(HTTPClientTool(settings.tools.http_client, storage=managed_storage).bindings())
    if settings.tools.python_exec.enabled:
        bindings.extend(HostPythonExecTool(settings.tools.python_exec, storage=managed_storage).bindings())
    if settings.tools.bash.enabled:
        bindings.extend(BashTool(settings.tools.bash).bindings())
    if settings.tools.apply_patch.enabled:
        bindings.extend(ApplyPatchTool(settings.tools.apply_patch).bindings())
    if managed_storage is not None:
        bindings.extend(FileStorageTool(storage=managed_storage, event_bus=None).bindings())
        bindings.extend(CodeReadTool(storage=managed_storage).bindings())
        if settings.tools.grep.enabled:
            bindings.extend(GrepTool(storage=managed_storage, config=settings.tools.grep).bindings())
        if settings.tools.audio_transcription.enabled:
            bindings.extend(
                AudioTranscriptionTool(
                    config=settings.tools.audio_transcription,
                    storage=managed_storage,
                ).bindings()
            )
    if settings.tools.mcp.enabled and spec.mcp_servers:
        for server in settings.tools.mcp.servers:
            if server.name not in spec.mcp_servers:
                continue
            client = MCPClient(
                server_name=server.name,
                transport=server.transport,
                timeout_seconds=settings.tools.mcp.timeout_seconds,
                command=server.command,
                args=server.args,
                env=server.env or None,
                cwd=server.cwd,
                url=server.url,
                headers=server.headers,
            )
            bindings.extend(
                build_mcp_bindings(
                    mode=server.mode,
                    server_name=server.name,
                    client=client,
                    name_prefix=settings.tools.mcp.name_prefix,
                    enabled_tools=server.enabled_tools,
                    disabled_tools=server.disabled_tools,
                    catalog_cache_ttl_seconds=server.catalog_cache_ttl_seconds,
                )
            )

    bindings.extend(extension_tools)
    scoped = strip_reserved_delegation_tools(filter_tools_for_agent(bindings, spec))
    return apply_tool_output_spill(
        scoped,
        storage=managed_storage,
        config=settings.tools.tool_output_spill,
    )


def _build_worker_spec(
    *, system_prompt: str, environment_prompt_fragment: str, extension_tool_names: Sequence[str] = ()
) -> AgentSpec:
    prompt = f"{system_prompt.strip()}\n\n{_WORKER_SYSTEM_PROMPT_SUFFIX}"
    if environment_prompt_fragment.strip():
        prompt = f"{prompt}\n\n{environment_prompt_fragment.strip()}"
    return AgentSpec(
        name="task_worker",
        description="Isolated subprocess worker for async task execution.",
        system_prompt=prompt,
        source_path=_WORKER_SPEC_PATH,
        max_tool_iterations=None,
        tools_allow=[*_WORKER_TOOL_ALLOWLIST, *extension_tool_names],
    )


def _resolve_task_spec(
    *,
    settings: Settings,
    llm_factory: LLMClientFactory,
    environment_prompt_fragment: str,
    task: dict[str, Any],
    extension_tool_names: Sequence[str] = (),
) -> AgentSpec:
    agent_name = task.get("agent_name")
    if isinstance(agent_name, str) and agent_name.strip():
        registry = AgentRegistry(load_agent_specs(settings.orchestration.directory))
        spec = registry.get(agent_name.strip())
        if spec is None:
            raise ValueError(f"agent '{agent_name.strip()}' is not available for async task execution")
        if not environment_prompt_fragment.strip():
            return spec
        return AgentSpec(
            name=spec.name,
            description=spec.description,
            system_prompt=f"{spec.system_prompt}\n\n{environment_prompt_fragment.strip()}",
            source_path=spec.source_path,
            model_provider=spec.model_provider,
            model=spec.model,
            temperature=spec.temperature,
            max_new_tokens=spec.max_new_tokens,
            reasoning_effort=spec.reasoning_effort,
            max_tool_iterations=spec.max_tool_iterations,
            tools_allow=list(spec.tools_allow),
            tools_deny=list(spec.tools_deny),
            mcp_servers=list(spec.mcp_servers),
            openrouter_provider_overrides=dict(spec.openrouter_provider_overrides),
            openrouter_reasoning_enabled=spec.openrouter_reasoning_enabled,
        )
    return _build_worker_spec(
        system_prompt=llm_factory.create_default().system_prompt(),
        environment_prompt_fragment=environment_prompt_fragment,
        extension_tool_names=extension_tool_names,
    )


def _build_worker_state(*, spec: AgentSpec, prompt: str, context: dict[str, Any]) -> AgentState:
    user_text = prompt.strip()
    if context:
        user_text = f"{user_text}\n\nContext:\n{json.dumps(context, ensure_ascii=True, indent=2, sort_keys=True)}"
    return AgentState(
        messages=[
            AgentMessage(role="system", content=[MessagePart(type="text", text=spec.system_prompt)]),
            AgentMessage(role="user", content=[MessagePart(type="text", text=user_text)]),
        ]
    )


def _build_managed_storage(settings: Settings) -> LocalFileStorage | None:
    if not settings.tools.file_storage.enabled:
        return None
    return LocalFileStorage(
        root_dir=settings.tools.file_storage.root_dir,
        max_write_bytes=settings.tools.file_storage.max_write_bytes,
        allow_outside_root=settings.tools.file_storage.allow_outside_root,
    )


def _worker_prompt_cache_key(*, tool_context: ToolContext, task_id: str) -> str:
    session_id = session_identifier(tool_context.channel or "task", tool_context.chat_id)
    return f"{session_id}:task:{task_id or 'worker'}"


def _coerce_context(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("context must be an object")
    return value


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("chat_id and user_id must be integers when provided")
    return value


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value.strip()


def _build_error_metadata(exc: Exception) -> dict[str, Any]:
    metadata: dict[str, Any] = {"error_type": type(exc).__name__}
    if not isinstance(exc, ProviderHTTPError) or exc.status_code != 429:
        return metadata
    metadata.update(
        {
            "error_code": "rate_limit_exceeded",
            "retryable": True,
            "retry_after_seconds": 30,
        }
    )
    return metadata


def _stop_reason_for_error(exc: Exception) -> TaskStopReason:
    if isinstance(exc, ProviderHTTPError):
        return TaskStopReason.PROVIDER_ERROR
    return TaskStopReason.WORKER_ERROR


def _task_limits(task: dict[str, Any], settings: Settings) -> TaskLimits:
    configured_timeout = settings.tasks.worker_timeout_seconds
    configured_max_steps = _config_limit(settings.tasks.worker_max_steps)
    configured_max_tool_calls = _config_limit(settings.tasks.worker_max_tool_calls)
    raw_limits = task.get("limits")
    if not isinstance(raw_limits, dict):
        return TaskLimits(
            timeout_seconds=configured_timeout,
            max_steps=configured_max_steps,
            max_tool_calls=configured_max_tool_calls,
        )
    timeout_seconds = raw_limits.get("timeout_seconds")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        raise ValueError("task timeout_seconds must be a positive integer")
    if timeout_seconds > configured_timeout:
        raise ValueError("task timeout_seconds may not exceed the configured task-worker timeout")
    max_steps = _payload_limit(raw_limits.get("max_steps"), "max_steps")
    max_tool_calls = _payload_limit(raw_limits.get("max_tool_calls"), "max_tool_calls")
    _validate_ceiling(max_steps, configured_max_steps, "max_steps")
    _validate_ceiling(max_tool_calls, configured_max_tool_calls, "max_tool_calls")
    return TaskLimits(
        timeout_seconds=timeout_seconds,
        max_steps=max_steps,
        max_tool_calls=max_tool_calls,
    )


def _config_limit(value: int | str) -> int | None:
    return None if value == "unlimited" else int(value)


def _payload_limit(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"task {field} must be a positive integer or null")
    return value


def _validate_ceiling(value: int | None, configured_ceiling: int | None, field: str) -> None:
    if value is None and configured_ceiling is not None:
        raise ValueError(f"task {field} may not be unlimited for this task system")
    if value is not None and configured_ceiling is not None and value > configured_ceiling:
        raise ValueError(f"task {field} may not exceed the configured task-worker limit")

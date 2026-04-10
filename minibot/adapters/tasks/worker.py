from __future__ import annotations

import asyncio
import json
import logging
import re
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
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.app.response_parser import extract_answer
from minibot.app.runtime_limits import build_runtime_limits
from minibot.core.agent_runtime import AgentMessage, AgentState, MessagePart
from minibot.core.agents import AgentSpec
from minibot.llm.tools.apply_patch import ApplyPatchTool
from minibot.llm.tools.audio_transcription import AudioTranscriptionTool
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.bash import BashTool
from minibot.llm.tools.calculator import CalculatorTool
from minibot.llm.tools.code_read import CodeReadTool
from minibot.llm.tools.file_storage import FileStorageTool
from minibot.llm.tools.grep import GrepTool
from minibot.llm.tools.http_client import HTTPClientTool
from minibot.llm.tools.mcp_bridge import MCPToolBridge
from minibot.llm.tools.python_exec import HostPythonExecTool
from minibot.llm.tools.time import CurrentTimeTool
from minibot.shared.utils import session_identifier

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
_WORKER_MAX_TOOL_ITERATIONS = 8
_RATE_LIMIT_RETRY_AFTER_RE = re.compile(r"Please try again in (?P<seconds>\d+(?:\.\d+)?)s", re.IGNORECASE)


def worker_entry(pipe: Any) -> None:
    asyncio.run(_worker_async(pipe))


async def _worker_async(pipe: Any) -> None:
    async with pipe.open() as (rx, tx):
        raw = await rx.readline()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            result = {"task_id": "", "error": "invalid task payload", "metadata": {"error_type": "invalid_payload"}}
        else:
            result = await run_agent_loop(payload)
        tx.write(json.dumps(result).encode() + b"\n")


async def run_agent_loop(task: dict[str, Any]) -> dict[str, Any]:
    task_id = str(task.get("task_id") or "")
    try:
        channel = _require_string(task.get("channel"), "channel")
        prompt = _require_string(task.get("prompt"), "prompt")
        settings = load_settings()
        llm_factory = LLMClientFactory(settings)
        environment_prompt_fragment = build_environment_prompt_fragment(settings)
        spec = _resolve_task_spec(
            settings=settings,
            llm_factory=llm_factory,
            environment_prompt_fragment=environment_prompt_fragment,
            task=task,
        )
        llm_client = llm_factory.create_for_agent(spec)
        tools = _build_worker_tools(settings=settings, spec=spec)
        runtime = AgentRuntime(
            llm_client=llm_client,
            tools=tools,
            limits=build_runtime_limits(
                llm_client=llm_client,
                timeout_seconds=settings.rabbitmq.worker_timeout_seconds,
                min_timeout_seconds=30,
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
            owner_id=_resolve_owner_id(task),
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
        )
        parsed = extract_answer(generation.payload, pre_response_meta=generation.pre_response_meta)
        render = parsed.render
        text = render.text if render is not None else ""
        metadata = {
            "tool_count": sum(1 for message in generation.state.messages if message.role == "tool"),
            "model": llm_client.model_name(),
            "provider": llm_client.provider_name(),
            "agent_name": spec.name,
            "managed_files_root": settings.tools.file_storage.root_dir if settings.tools.file_storage.enabled else None,
        }
        attachments = _validate_attachments((generation.pre_response_meta or {}).get("attachments"))
        return {"task_id": task_id, "text": text, "attachments": attachments, "metadata": metadata}
    except Exception as exc:  # noqa: BLE001
        _LOGGER.exception("task worker failed", exc_info=exc, extra={"task_id": task_id or "unknown"})
        return {
            "task_id": task_id,
            "error": str(exc),
            "metadata": _build_error_metadata(exc),
        }


def _build_worker_tools(*, settings: Settings, spec: AgentSpec) -> list[ToolBinding]:
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
            bridge = MCPToolBridge(
                server_name=server.name,
                client=client,
                name_prefix=settings.tools.mcp.name_prefix,
                enabled_tools=server.enabled_tools,
                disabled_tools=server.disabled_tools,
            )
            bindings.extend(bridge.build_bindings())

    return strip_reserved_delegation_tools(filter_tools_for_agent(bindings, spec))


def _build_worker_spec(*, system_prompt: str, environment_prompt_fragment: str) -> AgentSpec:
    prompt = f"{system_prompt.strip()}\n\n{_WORKER_SYSTEM_PROMPT_SUFFIX}"
    if environment_prompt_fragment.strip():
        prompt = f"{prompt}\n\n{environment_prompt_fragment.strip()}"
    return AgentSpec(
        name="task_worker",
        description="Isolated subprocess worker for async task execution.",
        system_prompt=prompt,
        source_path=_WORKER_SPEC_PATH,
        max_tool_iterations=_WORKER_MAX_TOOL_ITERATIONS,
        tools_allow=list(_WORKER_TOOL_ALLOWLIST),
    )


def _resolve_task_spec(
    *,
    settings: Settings,
    llm_factory: LLMClientFactory,
    environment_prompt_fragment: str,
    task: dict[str, Any],
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


def _resolve_owner_id(task: dict[str, Any]) -> str:
    user_id = _coerce_int(task.get("user_id"))
    if user_id is not None:
        return str(user_id)
    chat_id = _coerce_int(task.get("chat_id"))
    if chat_id is not None:
        return str(chat_id)
    channel = str(task.get("channel") or "task")
    return session_identifier(channel, chat_id, user_id)


def _worker_prompt_cache_key(*, tool_context: ToolContext, task_id: str) -> str:
    session_id = session_identifier(tool_context.channel or "task", tool_context.chat_id, tool_context.user_id)
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
    error_text = str(exc)
    lowered = error_text.lower()
    if "http 429" not in lowered or "rate_limit_exceeded" not in lowered:
        return metadata
    retry_after_seconds = _extract_retry_after_seconds(error_text)
    metadata.update(
        {
            "error_code": "rate_limit_exceeded",
            "retryable": True,
            "retry_after_seconds": retry_after_seconds,
        }
    )
    return metadata


def _extract_retry_after_seconds(error_text: str) -> int:
    match = _RATE_LIMIT_RETRY_AFTER_RE.search(error_text)
    if match is None:
        return 30
    return max(1, int(float(match.group("seconds")) + 0.999))


def _validate_attachments(raw_attachments: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_attachments, list):
        return []
    validated: list[dict[str, Any]] = []
    for item in raw_attachments:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        file_type = item.get("type")
        if not isinstance(path, str) or not path.strip():
            continue
        if not isinstance(file_type, str) or not file_type.strip():
            continue
        attachment: dict[str, Any] = {"path": path.strip(), "type": file_type.strip()}
        caption = item.get("caption")
        if isinstance(caption, str) and caption.strip():
            attachment["caption"] = caption.strip()
        validated.append(attachment)
    return validated

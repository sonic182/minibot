from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from minibot.adapters.container import AppContainer
from minibot.llm.tools.agent_delegate import AgentDelegateTool
from minibot.llm.tools.factory import build_enabled_tools


async def _run() -> int:
    cleanup_callbacks: list[callable] = []
    raw = sys.stdin.read().strip()
    if not raw:
        sys.stdout.write(json.dumps({"ok": False, "error_code": "missing_payload", "error": "worker payload missing"}))
        sys.stdout.flush()
        return 1
    config_path = os.environ.get("MINIBOT_CONFIG", "").strip()
    if not config_path:
        sys.stdout.write(
            json.dumps(
                {
                    "ok": False,
                    "error_code": "missing_config_path",
                    "error": "worker requires MINIBOT_CONFIG to match parent configuration",
                }
            )
        )
        sys.stdout.flush()
        return 1
    try:
        payload = json.loads(raw)
        AppContainer.configure()
        await AppContainer.initialize_storage()
        settings = AppContainer.get_settings()
        memory_backend = AppContainer.get_memory_backend()
        tools = build_enabled_tools(
            settings,
            memory_backend,
            AppContainer.get_kv_memory_backend(),
            prompt_scheduler=AppContainer.get_scheduled_prompt_service(),
            agent_job_service=None,
            event_bus=AppContainer.get_event_bus(),
            agent_registry=None,
            llm_factory=None,
            cleanup_callbacks=cleanup_callbacks,
        )
        delegate_tool = AgentDelegateTool(
            registry=AppContainer.get_agent_registry(),
            llm_factory=AppContainer.get_llm_factory(),
            tools=tools,
            default_timeout_seconds=int(payload.get("timeout_seconds") or settings.jobs.default_job_timeout_seconds),
            job_service=None,
            delegated_tool_call_policy=settings.orchestration.delegated_tool_call_policy,
            environment_prompt_fragment="",
        )
        input_payload = payload.get("input_payload") or {}
        agent_name = str(payload.get("agent_name") or input_payload.get("agent_name") or "").strip()
        spec = AppContainer.get_agent_registry().get(agent_name)
        if spec is None:
            sys.stdout.write(
                json.dumps(
                    {"ok": False, "agent": agent_name, "error_code": "agent_not_found", "error": "agent not found"}
                )
            )
            sys.stdout.flush()
            return 0
        result = await delegate_tool.run_agent(
            spec=spec,
            task=str(input_payload.get("task") or "").strip(),
            details=(str(input_payload.get("context")) if input_payload.get("context") is not None else None),
            context=delegate_tool_context(input_payload),
        )
        sys.stdout.write(json.dumps(result))
        sys.stdout.flush()
        return 0
    finally:
        for cleanup in cleanup_callbacks:
            cleanup()


def delegate_tool_context(input_payload: dict[str, Any]):
    from minibot.llm.tools.base import ToolContext

    return ToolContext(
        owner_id=input_payload.get("owner_id"),
        channel=input_payload.get("channel"),
        chat_id=input_payload.get("chat_id"),
        user_id=input_payload.get("user_id"),
        session_id=input_payload.get("created_by_session_id"),
        message_id=input_payload.get("created_by_message_id"),
        agent_name="minibot",
        can_enqueue_agent_jobs=False,
    )


def main() -> None:
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()

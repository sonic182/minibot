from __future__ import annotations

from llm_async.models import Tool

from minibot.app.agent_registry import AgentRegistry
from minibot.app.llm_client_factory import LLMClientFactory
from minibot.llm.tools.arg_utils import require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import strict_object, string_field


class AgentInfoTool:
    def __init__(self, *, registry: AgentRegistry, llm_factory: LLMClientFactory) -> None:
        self._registry = registry
        self._llm_factory = llm_factory

    def bindings(self) -> list[ToolBinding]:
        return [ToolBinding(tool=self._fetch_agent_info_schema(), handler=self._fetch_agent_info)]

    def _fetch_agent_info_schema(self) -> Tool:
        return Tool(
            name="fetch_agent_info",
            description=load_tool_description("fetch_agent_info"),
            parameters=strict_object(
                properties={
                    "agent_name": string_field("Exact specialist name from the available specialists list."),
                },
                required=["agent_name"],
            ),
        )

    async def _fetch_agent_info(self, payload: dict[str, object], __: ToolContext) -> dict[str, object]:
        agent_name = require_non_empty_str(payload, "agent_name")
        spec = self._registry.get(agent_name)
        if spec is None:
            return {
                "ok": False,
                "agent": agent_name,
                "error_code": "agent_not_found",
                "error": f"agent '{agent_name}' is not available",
            }
        return {
            "ok": True,
            "agent": spec.name,
            "name": spec.name,
            "description": spec.description,
            "system_prompt": spec.system_prompt,
            "defaults": {
                "model_provider": spec.model_provider,
                "model": spec.model,
                "reasoning_effort": spec.reasoning_effort,
                "timeout_seconds": spec.timeout_seconds,
            },
            "available_providers": [option.as_payload() for option in self._llm_factory.available_providers()],
            "override_hint": (
                "spawn_task accepts model_provider, model and reasoning_effort to run this specialist on one "
                "of the providers listed above for a single task. A null default means the specialist "
                "inherits the main configured provider or model."
            ),
        }

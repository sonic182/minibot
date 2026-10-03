"""Runtime agent management: reload, create, update and delete.

Registered only when ``[orchestration.agent_management]`` turns the corresponding switch on, so the
tools do not exist at all otherwise. Every operation goes through ``AgentManagementService``, which
validates the definition and the owner's ceiling before anything is written. The tools themselves
decide nothing — a tool that could write a definition without the service would be the bypass this
design exists to prevent.
"""

from __future__ import annotations

from llm_async.models import Tool

from minibot.app.agent_management import AgentManagementOutcome, AgentManagementService
from minibot.app.extensions import ExtensionContext
from minibot.core.tools import ToolContext
from minibot.llm.tools.arg_utils import require_non_empty_str
from minibot.llm.tools.base import ToolBinding
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import empty_object_schema, strict_object, string_field


def register(mb: ExtensionContext) -> None:
    if mb.entrypoint == "worker":
        return
    service = mb.agent_management
    if service is None:
        return
    management = mb.settings.orchestration.agent_management
    package = __spec__.parent if __spec__ is not None else __name__
    bindings: list[ToolBinding] = []
    if management.reload:
        bindings.append(_reload_binding(service, package=package))
    if management.write:
        bindings.append(_write_binding(service, package=package, action="create"))
        bindings.append(_write_binding(service, package=package, action="update"))
        bindings.append(_delete_binding(service, package=package))
    mb.add_tool(bindings)


def _reload_binding(service: AgentManagementService, *, package: str) -> ToolBinding:
    schema = Tool(
        name="reload_agents",
        description=load_tool_description("reload_agents", package=package),
        parameters=empty_object_schema(),
    )

    async def handler(_: dict[str, object], __: ToolContext) -> dict[str, object]:
        return (await service.reload()).as_payload()

    return ToolBinding(tool=schema, handler=handler)


def _write_binding(service: AgentManagementService, *, package: str, action: str) -> ToolBinding:
    schema = Tool(
        name=f"{action}_agent",
        description=load_tool_description(f"{action}_agent", package=package),
        parameters=strict_object(
            properties={
                "name": string_field(
                    "Agent name: 3 to 30 characters, letters and underscores only. It must match the "
                    "name in the definition's frontmatter."
                ),
                "definition": string_field(
                    "The complete agent definition file: YAML frontmatter between --- lines, then the "
                    "system prompt body. The body must not be empty."
                ),
            },
            required=["name", "definition"],
        ),
    )

    async def handler(payload: dict[str, object], __: ToolContext) -> dict[str, object]:
        name = require_non_empty_str(payload, "name")
        definition = require_non_empty_str(payload, "definition")
        outcome: AgentManagementOutcome = (
            await service.create(name=name, content=definition)
            if action == "create"
            else await service.update(name=name, content=definition)
        )
        return outcome.as_payload()

    return ToolBinding(tool=schema, handler=handler)


def _delete_binding(service: AgentManagementService, *, package: str) -> ToolBinding:
    schema = Tool(
        name="delete_agent",
        description=load_tool_description("delete_agent", package=package),
        parameters=strict_object(
            properties={"name": string_field("Name of the managed agent to delete.")},
            required=["name"],
        ),
    )

    async def handler(payload: dict[str, object], __: ToolContext) -> dict[str, object]:
        name = require_non_empty_str(payload, "name")
        return (await service.delete(name=name)).as_payload()

    return ToolBinding(tool=schema, handler=handler)

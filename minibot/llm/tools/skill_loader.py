from __future__ import annotations

from pathlib import Path
from typing import Any

from llm_async.models import Tool

from minibot.app.skill_registry import SkillRegistry
from minibot.llm.tools.arg_utils import require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import strict_object


class SkillLoaderTool:
    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    def bindings(self) -> list[ToolBinding]:
        return [ToolBinding(tool=self._schema(), handler=self._handle)]

    def _schema(self) -> Tool:
        name_property: dict[str, Any] = {
            "type": "string",
            "description": "Exact skill name from the available skills list.",
        }
        names = self._registry.names()
        if names:
            name_property["enum"] = names
        return Tool(
            name="activate_skill",
            description=load_tool_description("activate_skill"),
            parameters=strict_object(
                properties={"name": name_property},
                required=["name"],
            ),
        )

    async def _handle(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        name = require_non_empty_str(payload, "name")
        spec = self._registry.get(name)
        if spec is None:
            return {
                "ok": False,
                "skill": name,
                "error_code": "skill_not_found",
                "error": f"skill '{name}' is not available",
            }
        resources = _list_resources(spec.skill_dir)
        return {
            "ok": True,
            "skill": name,
            "instructions": f"<skill-instructions>\n{spec.body}\n</skill-instructions>",
            "skill_dir": spec.skill_dir.as_posix(),
            "resources": resources,
        }


def _list_resources(skill_dir: Path) -> list[str]:
    return sorted(
        p.relative_to(skill_dir).as_posix() for p in skill_dir.rglob("*") if p.is_file() and p.name != "SKILL.md"
    )

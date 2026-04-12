from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from llm_async.models import Tool

from minibot.app.skill_registry import SkillRegistry
from minibot.core.skills import SkillSpec
from minibot.llm.tools.arg_utils import optional_str, require_non_empty_str
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.description_loader import load_tool_description
from minibot.llm.tools.schema_utils import strict_object

_LIST_SKILLS_LIMIT = 8


class SkillLoaderTool:
    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._list_schema(), handler=self._list_skills),
            ToolBinding(tool=self._activate_schema(), handler=self._handle),
        ]

    def _activate_schema(self) -> Tool:
        return Tool(
            name="activate_skill",
            description=load_tool_description("activate_skill"),
            parameters=strict_object(
                properties={
                    "name": {
                        "type": "string",
                        "description": "Exact skill name returned by list_skills.",
                    }
                },
                required=["name"],
            ),
        )

    def _list_schema(self) -> Tool:
        return Tool(
            name="list_skills",
            description=load_tool_description("list_skills"),
            parameters=strict_object(
                properties={
                    "query": {
                        "type": "string",
                        "description": "Optional search text to filter and rank skills by name or description.",
                    }
                },
                required=[],
            ),
        )

    async def _list_skills(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        self._registry.refresh_if_stale()
        query = optional_str(payload.get("query"), error_message="query must be a string")
        skills = self._registry.all()
        matches, used_fuzzy = _match_skills(skills, query)
        return {
            "ok": True,
            "query": query,
            "total_available": len(skills),
            "returned": len(matches),
            "used_fuzzy_fallback": used_fuzzy,
            "matches": [
                {
                    "name": spec.name,
                    "description": spec.description.strip() or "No description provided.",
                }
                for spec in matches
            ],
        }

    async def _handle(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        self._registry.refresh_if_stale()
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


def _match_skills(skills: list[SkillSpec], query: str | None) -> tuple[list[SkillSpec], bool]:
    if query is None:
        return sorted(skills, key=lambda spec: spec.name)[:_LIST_SKILLS_LIMIT], False

    normalized_query = query.casefold()
    ranked = [
        (_skill_match_key(spec, normalized_query), spec)
        for spec in skills
    ]
    non_fuzzy = [entry for entry in ranked if entry[0][0] < 4]
    if non_fuzzy:
        ordered = [spec for _key, spec in sorted(non_fuzzy, key=lambda item: item[0])]
        return ordered[:_LIST_SKILLS_LIMIT], False

    fuzzy = [entry for entry in ranked if entry[0][0] == 4 and entry[0][1] < 0]
    ordered = [spec for _key, spec in sorted(fuzzy, key=lambda item: item[0])]
    return ordered[:_LIST_SKILLS_LIMIT], bool(ordered)


def _skill_match_key(spec: SkillSpec, normalized_query: str) -> tuple[int, float, str]:
    normalized_name = spec.name.casefold()
    normalized_description = spec.description.casefold()
    if normalized_name == normalized_query:
        return (0, 0.0, normalized_name)
    if normalized_name.startswith(normalized_query):
        return (1, float(len(normalized_name)), normalized_name)
    if normalized_query in normalized_name:
        return (2, float(normalized_name.index(normalized_query)), normalized_name)
    if normalized_query in normalized_description:
        return (3, float(normalized_description.index(normalized_query)), normalized_name)
    similarity = max(
        SequenceMatcher(None, normalized_query, normalized_name).ratio(),
        SequenceMatcher(None, normalized_query, normalized_description).ratio(),
    )
    return (4, -similarity, normalized_name)


def _list_resources(skill_dir: Path) -> list[str]:
    try:
        return sorted(
            p.relative_to(skill_dir).as_posix() for p in skill_dir.rglob("*") if p.is_file() and p.name != "SKILL.md"
        )
    except OSError:
        return []

from __future__ import annotations

from minibot.app.skill_registry import SkillRegistry
from minibot.core.files import FileStorage
from minibot.llm.tools.base import ToolBinding
from minibot.llm.tools.skill_loader import SkillLoaderTool


def build_skill_loader_bindings(
    registry: SkillRegistry,
    storage: FileStorage | None,
    bash_enabled: bool,
) -> list[ToolBinding]:
    return SkillLoaderTool(registry, storage, bash_enabled).bindings()

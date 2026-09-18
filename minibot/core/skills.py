from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class SkillSource(StrEnum):
    """Where a skill was discovered, in precedence order: lower rank wins."""

    PROJECT = "project"
    USER = "user"
    NATIVE = "native"


SKILL_SOURCE_RANK: dict[SkillSource, int] = {
    SkillSource.PROJECT: 0,
    SkillSource.USER: 1,
    SkillSource.NATIVE: 2,
}


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    body: str
    skill_dir: Path
    source: SkillSource = SkillSource.PROJECT
    compatibility: str = ""

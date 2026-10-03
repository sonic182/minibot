from __future__ import annotations

import logging
import re
from pathlib import Path

from pydantic import ValidationError

from minibot.config.schema import AgentDefinitionConfig, Settings
from minibot.core.agents import AgentSpec
from minibot.shared.frontmatter import parse_frontmatter, split_frontmatter

logger = logging.getLogger("minibot.agent_definitions_loader")
_NAME_RE = re.compile(r"^[a-zA-Z_]{3,30}$")
_DESCRIPTION_MAX_CHARS = 1000


def load_agent_specs(directory: str) -> list[AgentSpec]:
    """Load every enabled definition in one directory, rejecting duplicate names.

    Two files claiming the same agent name used to last-win silently, which made the roster depend
    on glob order. A collision is now an error, because a managed definition must never quietly
    replace an owner-authored one.
    """
    root = Path(directory)
    if not root.exists() or not root.is_dir():
        return []
    specs: list[AgentSpec] = []
    seen: dict[str, Path] = {}
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        frontmatter, body = split_frontmatter(text)
        if frontmatter is None:
            continue
        payload = parse_frontmatter(frontmatter)
        if not isinstance(payload, dict):
            raise ValueError(f"{path}: frontmatter must be a YAML object")
        try:
            cfg = AgentDefinitionConfig.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"{path}: invalid agent frontmatter: {exc}") from exc
        if not cfg.enabled:
            continue
        system_prompt = body.strip()
        if not system_prompt:
            raise ValueError(f"{path}: agent body prompt cannot be empty")
        if not _NAME_RE.fullmatch(cfg.name):
            logger.warning(
                "agent name does not match expected pattern",
                extra={"agent_name": cfg.name, "pattern": _NAME_RE.pattern, "source": str(path)},
            )
        if len(cfg.description) > _DESCRIPTION_MAX_CHARS:
            logger.warning(
                "agent description exceeds recommended length",
                extra={
                    "agent_name": cfg.name,
                    "length": len(cfg.description),
                    "max": _DESCRIPTION_MAX_CHARS,
                    "source": str(path),
                },
            )
        previous = seen.get(cfg.name)
        if previous is not None:
            raise ValueError(f"duplicate agent name '{cfg.name}': {path} and {previous} both define it")
        seen[cfg.name] = path
        specs.append(
            AgentSpec(
                name=cfg.name,
                description=cfg.description,
                system_prompt=system_prompt,
                source_path=path,
                model_provider=cfg.model_provider,
                model=cfg.model,
                temperature=cfg.temperature,
                omit_temperature=cfg.omit_temperature,
                max_new_tokens=cfg.max_new_tokens,
                reasoning_effort=cfg.reasoning_effort,
                max_tool_iterations=cfg.max_tool_iterations,
                timeout_seconds=cfg.timeout_seconds,
                tools_allow=list(cfg.tools_allow),
                tools_deny=list(cfg.tools_deny),
                mcp_servers=list(cfg.mcp_servers),
                openrouter_provider_overrides=dict(cfg.openrouter_provider_overrides),
                openrouter_reasoning_enabled=cfg.openrouter_reasoning_enabled,
            )
        )
    return specs


def load_active_agent_specs(settings: Settings) -> list[AgentSpec]:
    """Load every definition directory the current switches activate.

    Owner definitions come from ``[orchestration].directory`` and model-authored ones from
    ``[orchestration.agent_management].directory``. With ``[orchestration.specialists].enabled``
    off this returns nothing, so a broken file in either directory cannot fail a startup that has
    no use for a roster. A managed name that collides with an owner name is rejected rather than
    replacing it.
    """
    if not settings.orchestration.specialists.enabled:
        return []
    specs = load_agent_specs(settings.orchestration.directory)
    management = settings.orchestration.agent_management
    if not management.active:
        return specs
    owner_names = {spec.name for spec in specs}
    for spec in load_agent_specs(management.directory):
        if spec.name in owner_names:
            raise ValueError(
                f"managed agent '{spec.name}' in {spec.source_path} collides with an owner-authored agent; "
                "rename one of them"
            )
        owner_names.add(spec.name)
        specs.append(spec)
    return specs

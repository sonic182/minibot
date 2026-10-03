from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import replace
from pathlib import Path

from pydantic import ValidationError

from minibot.app.managed_agent_policy import ManagedAgentPolicy
from minibot.config.schema import AgentDefinitionConfig, Settings
from minibot.core.agents import AgentSpec
from minibot.shared.frontmatter import parse_frontmatter, split_frontmatter

logger = logging.getLogger("minibot.agent_definitions_loader")
AGENT_NAME_RE = re.compile(r"^[a-zA-Z_]{3,30}$")
_DESCRIPTION_MAX_CHARS = 1000


def parse_agent_definition(*, source_path: Path, text: str, strict_name: bool = False) -> AgentSpec | None:
    """Parse one definition, returning ``None`` when it is disabled.

    ``strict_name`` turns the name-pattern warning into an error. An owner-authored file gets the
    warning because it is already on disk and refusing to start is worse than a warning; a
    model-authored write gets the error, because the definition must be loadable under the name it
    claims before it is persisted.
    """
    frontmatter, body = split_frontmatter(text)
    if frontmatter is None:
        raise ValueError(f"{source_path}: missing frontmatter")
    payload = parse_frontmatter(frontmatter)
    if not isinstance(payload, dict):
        raise ValueError(f"{source_path}: frontmatter must be a YAML object")
    try:
        cfg = AgentDefinitionConfig.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"{source_path}: invalid agent frontmatter: {exc}") from exc
    if not cfg.enabled:
        return None
    system_prompt = body.strip()
    if not system_prompt:
        raise ValueError(f"{source_path}: agent body prompt cannot be empty")
    if not AGENT_NAME_RE.fullmatch(cfg.name):
        if strict_name:
            raise ValueError(f"{source_path}: agent name '{cfg.name}' must match {AGENT_NAME_RE.pattern}")
        logger.warning(
            "agent name does not match expected pattern",
            extra={"agent_name": cfg.name, "pattern": AGENT_NAME_RE.pattern, "source": str(source_path)},
        )
    if len(cfg.description) > _DESCRIPTION_MAX_CHARS:
        logger.warning(
            "agent description exceeds recommended length",
            extra={
                "agent_name": cfg.name,
                "length": len(cfg.description),
                "max": _DESCRIPTION_MAX_CHARS,
                "source": str(source_path),
            },
        )
    return AgentSpec(
        name=cfg.name,
        description=cfg.description,
        system_prompt=system_prompt,
        source_path=source_path,
        revision=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
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


def load_agent_specs(directory: str, *, strict_name: bool = False) -> list[AgentSpec]:
    """Load every enabled definition in one directory, rejecting duplicate names.

    Two files claiming the same agent name used to last-win silently, which made the roster depend
    on glob order. A collision is now an error, because a managed definition must never quietly
    replace an owner-authored one. ``strict_name`` turns the name-pattern warning into an error for
    directories whose files must stay addressable by name, such as the managed one.
    """
    root = Path(directory)
    if not root.exists() or not root.is_dir():
        return []
    specs: list[AgentSpec] = []
    seen: dict[str, Path] = {}
    for path in sorted(root.glob("*.md")):
        spec = parse_agent_definition(
            source_path=path,
            text=path.read_text(encoding="utf-8"),
            strict_name=strict_name,
        )
        if spec is None:
            continue
        previous = seen.get(spec.name)
        if previous is not None:
            raise ValueError(f"duplicate agent name '{spec.name}': {path} and {previous} both define it")
        seen[spec.name] = path
        specs.append(spec)
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
    policy = ManagedAgentPolicy.from_settings(settings)
    owner_names = {spec.name for spec in specs}
    for spec in load_agent_specs(management.directory, strict_name=True):
        if spec.source_path.stem != spec.name:
            raise ValueError(
                f"managed agent '{spec.name}' in {spec.source_path} must be defined in "
                f"'{spec.name}.md'; the frontmatter name has to match the file name so the "
                "management tools can address it"
            )
        if spec.name in owner_names:
            raise ValueError(
                f"managed agent '{spec.name}' in {spec.source_path} collides with an owner-authored agent; "
                "rename one of them"
            )
        policy.authorize(spec)
        owner_names.add(spec.name)
        specs.append(replace(spec, managed=True))
    return specs

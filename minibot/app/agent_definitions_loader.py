from __future__ import annotations

import logging
import re
from pathlib import Path

from pydantic import ValidationError

from minibot.adapters.config.schema import AgentDefinitionConfig
from minibot.core.agents import AgentSpec
from minibot.shared.frontmatter import parse_frontmatter, split_frontmatter

logger = logging.getLogger("minibot.agent_definitions_loader")
_NAME_RE = re.compile(r"^[a-zA-Z_]{3,30}$")
_DESCRIPTION_MAX_CHARS = 1000


def load_agent_specs(directory: str) -> list[AgentSpec]:
    root = Path(directory)
    if not root.exists() or not root.is_dir():
        return []
    specs: list[AgentSpec] = []
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
        specs.append(
            AgentSpec(
                name=cfg.name,
                description=cfg.description,
                system_prompt=system_prompt,
                source_path=path,
                model_provider=cfg.model_provider,
                model=cfg.model,
                temperature=cfg.temperature,
                max_new_tokens=cfg.max_new_tokens,
                reasoning_effort=cfg.reasoning_effort,
                max_tool_iterations=cfg.max_tool_iterations,
                tools_allow=list(cfg.tools_allow),
                tools_deny=list(cfg.tools_deny),
                mcp_servers=list(cfg.mcp_servers),
                openrouter_provider_overrides=dict(cfg.openrouter_provider_overrides),
                openrouter_reasoning_enabled=cfg.openrouter_reasoning_enabled,
            )
        )
    return specs

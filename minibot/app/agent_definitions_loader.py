from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from minibot.adapters.config.schema import AgentDefinitionConfig
from minibot.core.agents import AgentSpec


def load_agent_specs(directory: str) -> list[AgentSpec]:
    root = Path(directory)
    if not root.exists() or not root.is_dir():
        return []
    specs: list[AgentSpec] = []
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        frontmatter, body = _split_frontmatter(text)
        if frontmatter is None:
            continue
        payload = _parse_frontmatter(frontmatter)
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


def _split_frontmatter(content: str) -> tuple[str | None, str]:
    if not content.startswith("---"):
        return None, content
    lines = content.splitlines()
    if not lines:
        return None, content
    closing_idx = -1
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            closing_idx = idx
            break
    if closing_idx < 0:
        raise ValueError("invalid agent frontmatter: missing closing ---")
    frontmatter = "\n".join(lines[1:closing_idx])
    body = "\n".join(lines[closing_idx + 1 :])
    return frontmatter, body


def _parse_frontmatter(frontmatter: str) -> dict[str, object]:
    result: dict[str, object] = {}
    current_parent: str | None = None
    current_kind: str | None = None
    for raw_line in frontmatter.splitlines():
        if not raw_line.strip():
            continue
        line = raw_line.rstrip()
        if line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0:
            current_parent = None
            current_kind = None
            if ":" not in stripped:
                raise ValueError(f"invalid frontmatter line: {raw_line}")
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()
            if not value:
                current_parent = key
                current_kind = None
                continue
            result[key] = _parse_scalar(value)
            continue
        if indent == 2 and current_parent:
            if stripped.startswith("- "):
                if current_kind is None:
                    current_kind = "list"
                    result[current_parent] = []
                if current_kind != "list":
                    raise ValueError(f"mixed frontmatter container types for key {current_parent}")
                entry = stripped[2:].strip()
                cast_list = result[current_parent]
                if isinstance(cast_list, list):
                    cast_list.append(str(_parse_scalar(entry)))
                continue
            if ":" in stripped:
                if current_kind is None:
                    current_kind = "dict"
                    result[current_parent] = {}
                if current_kind != "dict":
                    raise ValueError(f"mixed frontmatter container types for key {current_parent}")
                child_key, child_value = stripped.split(":", 1)
                child_key = child_key.strip()
                child_value = child_value.strip()
                cast_dict = result[current_parent]
                if isinstance(cast_dict, dict):
                    cast_dict[child_key] = _parse_scalar(child_value)
                continue
        raise ValueError(f"unsupported frontmatter structure: {raw_line}")
    return result


def _parse_scalar(value: str) -> object:
    text = value.strip()
    if not text:
        return ""
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text

from __future__ import annotations


def split_frontmatter(content: str) -> tuple[str | None, str]:
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
        raise ValueError("invalid frontmatter: missing closing ---")
    frontmatter = "\n".join(lines[1:closing_idx])
    body = "\n".join(lines[closing_idx + 1 :])
    return frontmatter, body


def parse_frontmatter(frontmatter: str) -> dict[str, object]:
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
            result[key] = parse_scalar(value)
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
                    cast_list.append(str(parse_scalar(entry)))
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
                    cast_dict[child_key] = parse_scalar(child_value)
                continue
        raise ValueError(f"unsupported frontmatter structure: {raw_line}")
    return result


def parse_scalar(value: str) -> object:
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

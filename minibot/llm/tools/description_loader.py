from __future__ import annotations

from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=None)
def load_tool_description(name: str) -> str:
    package = files("minibot.llm.tools.descriptions")
    resource = package.joinpath(f"{name}.txt")
    try:
        text = resource.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, TypeError) as exc:
        raise FileNotFoundError(f"Missing tool description file: descriptions/{name}.txt") from exc
    if not text:
        raise ValueError(f"Empty tool description file: descriptions/{name}.txt")
    return text

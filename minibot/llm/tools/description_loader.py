from __future__ import annotations

from functools import cache
from importlib.resources import files


@cache
def load_tool_description(name: str, package: str = "minibot.llm.tools") -> str:
    """Read ``<name>.txt`` from ``package``, which defaults to the core tool package.

    A tool that lives elsewhere — an extension, say — passes its own ``__spec__.parent`` so the
    description file travels with the module instead of staying behind in core.
    """
    resource = files(package).joinpath(f"{name}.txt")
    try:
        text = resource.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, TypeError) as exc:
        raise FileNotFoundError(f"Missing tool description file: {package}/{name}.txt") from exc
    if not text:
        raise ValueError(f"Empty tool description file: {package}/{name}.txt")
    return text

from __future__ import annotations

import sys
from typing import Any
import re

try:
    from rich.console import Console as RichConsole
    from rich.panel import Panel as RichPanel
    from rich.markdown import Markdown as RichMarkdown
    from rich.prompt import Prompt as RichPrompt
    from rich.text import Text as RichText
except Exception:  # noqa: BLE001
    RichConsole = None
    RichPanel = None
    RichMarkdown = None
    RichPrompt = None
    RichText = None


class CompatConsole:
    def __init__(self) -> None:
        if RichConsole is not None:
            self._inner = RichConsole()
        else:
            self._inner = None

    def print(self, value: Any) -> None:
        if self._inner is not None:
            self._inner.print(value)
            return
        sys.stdout.write(f"{value}\n")
        sys.stdout.flush()


def render_markdown(text: str) -> Any:
    if RichMarkdown is not None:
        return RichMarkdown(text)
    return text


def format_assistant_output(kind: str, text: str) -> Any:
    if RichPanel is None:
        return f"assistant: {text}"
    if kind == "markdown_v2" and RichMarkdown is not None:
        body: Any = RichMarkdown(text)
    elif RichText is not None:
        body = RichText(text)
    else:
        body = text
    return RichPanel(
        body,
        title="assistant",
        border_style="cyan",
        padding=(0, 1),
    )


def prompt_input(label: str) -> str:
    if RichPrompt is not None:
        return RichPrompt.ask(label)
    plain_label = re.sub(r"\[[^\]]+\]", "", label)
    return input(plain_label)

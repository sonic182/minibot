"""Minimal Textual TUI for the console channel.

A markdown transcript on top and a multiline prompt pinned to the bottom,
in the spirit of opencode / claude code / codex but intentionally tiny.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Footer, Header, LoadingIndicator, MarkdownViewer, Static, TextArea

from minibot.adapters.messaging.console.service import ConsoleResponse, ConsoleService
from minibot.core.memory import MemoryEntry

_SEPARATOR = "\n\n---\n\n"


@dataclass(frozen=True)
class _Turn:
    """One transcript entry: its markdown body plus optional thinking text."""

    markdown: str
    thinking: str | None = None


class NoopConsole:
    """Console that discards output; the TUI renders responses itself."""

    def print(self, value: object) -> None:  # noqa: A002
        return None


class Prompt(TextArea):
    """Multiline prompt: Enter submits, Ctrl+J inserts a newline."""

    BINDINGS = [Binding("ctrl+j", "newline", "New line", show=False)]

    class Submitted(Message):
        """Posted when the user submits the prompt with Enter."""

        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    def __init__(self, *, placeholder: str = "", id: str | None = None) -> None:
        super().__init__(placeholder=placeholder, id=id)

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.text))
            return
        await super()._on_key(event)

    def action_newline(self) -> None:
        self.insert("\n")


class ConsoleTui(App[None]):
    """Interactive console app: markdown transcript + bottom multiline input."""

    TITLE = "minibot"
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+t", "toggle_thinking", "Thinking"),
    ]
    CSS = """
    #input-area {
        dock: bottom;
        width: 100%;
        padding: 0 1 1 1;
        height: auto;
    }
    #prompt {
        height: 5;
    }
    #thinking {
        height: 1;
    }
    #thinking-status {
        height: 1;
        color: $text-muted;
        text-style: italic;
    }
    """

    def __init__(
        self,
        service: ConsoleService,
        *,
        timeout_seconds: float,
        history: list[MemoryEntry] | None = None,
    ) -> None:
        super().__init__()
        self.theme = "ansi-dark"
        self._service = service
        self._timeout_seconds = timeout_seconds
        self._turns: list[_Turn] = self._history_turns(history)
        self._show_thinking = True
        self._live_thinking: list[str] = []

    @staticmethod
    def _history_turns(history: list[MemoryEntry] | None) -> list[_Turn]:
        if not history:
            return []
        turns: list[_Turn] = []
        for entry in history:
            if entry.role == "user":
                turns.append(_Turn(markdown=f"**You:**\n\n{entry.content}"))
            elif entry.role == "assistant":
                turns.append(_Turn(markdown=entry.content, thinking=entry.reasoning))
        return turns

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield MarkdownViewer(id="transcript", show_table_of_contents=False)
        with Vertical(id="input-area"):
            yield LoadingIndicator(id="thinking")
            yield Static("", id="thinking-status")
            yield Prompt(
                placeholder="Message minibot… (Enter send · Ctrl+J newline · Ctrl+T thinking)",
                id="prompt",
            )
        yield Footer(show_command_palette=False)

    async def on_mount(self) -> None:
        self.query_one("#thinking", LoadingIndicator).display = False
        self._update_thinking_status()
        if self._turns:
            await self._refresh()
        self.query_one("#prompt", Prompt).focus()

    def on_prompt_submitted(self, event: Prompt.Submitted) -> None:
        text = event.text.strip()
        self.query_one("#prompt", Prompt).clear()
        if not text:
            return
        if text.lower() in {"quit", "exit"}:
            self.exit()
            return
        self.run_worker(self._run_turn(text), group="turn", exclusive=True, exit_on_error=False)

    def action_toggle_thinking(self) -> None:
        self._show_thinking = not self._show_thinking
        self._update_thinking_status()
        self.run_worker(self._refresh(), group="render")

    def _update_thinking_status(self) -> None:
        state = "shown" if self._show_thinking else "hidden"
        self.query_one("#thinking-status", Static).update(f"Thinking: {state}  (Ctrl+T to toggle)")

    async def _run_turn(self, text: str) -> None:
        self._set_busy(True)
        self._turns.append(_Turn(markdown=f"**You:**\n\n{text}"))
        await self._refresh()
        self._service.drain_reasoning()
        self._live_thinking = []
        live_reasoning = asyncio.create_task(self._stream_reasoning())
        try:
            await self._service.publish_user_message(text)
            result = await self._service.wait_for_response(self._timeout_seconds)
            self._turns.append(_Turn(markdown=result.rendered_text, thinking=_response_reasoning(result)))
        except TimeoutError:
            self._turns.append(
                _Turn(markdown=f"*Timed out after {int(self._timeout_seconds)}s — the request may still be running.*")
            )
        finally:
            live_reasoning.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await live_reasoning
            # The finished turn carries every step's reasoning, so keeping the live copy would double it.
            self._live_thinking = []
            await self._refresh()
            self._set_busy(False)

    async def _stream_reasoning(self) -> None:
        while True:
            chunk = await self._service.next_reasoning()
            if chunk not in self._live_thinking:
                self._live_thinking.append(chunk)
                await self._refresh()

    def _set_busy(self, busy: bool) -> None:
        self.query_one("#thinking", LoadingIndicator).display = busy
        prompt = self.query_one("#prompt", Prompt)
        prompt.disabled = busy
        if not busy:
            prompt.focus()

    async def _refresh(self) -> None:
        blocks: list[str] = []
        for turn in self._turns:
            if self._show_thinking and turn.thinking:
                blocks.append(f"**Thinking:**\n\n> {turn.thinking}")
            blocks.append(turn.markdown)
        if self._show_thinking and self._live_thinking:
            blocks.append("**Thinking…**\n\n> " + "\n>\n> ".join(self._live_thinking))
        viewer = self.query_one("#transcript", MarkdownViewer)
        await viewer.document.update(_SEPARATOR.join(blocks))
        viewer.scroll_end(animate=False)


def _response_reasoning(result: ConsoleResponse) -> str | None:
    metadata = getattr(result.response, "metadata", None) or {}
    reasoning = metadata.get("reasoning")
    return reasoning if isinstance(reasoning, str) and reasoning.strip() else None

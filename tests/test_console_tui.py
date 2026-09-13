from __future__ import annotations

from unittest.mock import patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Markdown

from minibot.adapters.messaging.console.tui import Transcript


class _Demo(App):
    def compose(self) -> ComposeResult:
        yield Transcript(id="transcript", show_table_of_contents=False)


@pytest.mark.asyncio
async def test_clicking_a_link_never_navigates_the_transcript() -> None:
    """MarkdownViewer resolves hrefs as local files; an https link used to crash the app.

    Driven through a real app on purpose: Textual dispatches to the handler of every class in
    the MRO, so a subclass override alone does not stop the base implementation from running.
    """
    app = _Demo()
    async with app.run_test() as pilot:
        viewer = app.query_one("#transcript", Transcript)
        await viewer.document.update("[docs](https://example.com/docs)")
        await pilot.pause()

        opened: list[str] = []
        with patch.object(App, "open_url", lambda self, url, new_tab=True: opened.append(url)):
            viewer.post_message(Markdown.LinkClicked(viewer.document, "https://example.com/docs"))
            await pilot.pause()
            await pilot.pause()
            viewer.post_message(Markdown.LinkClicked(viewer.document, "./does-not-exist.md"))
            await pilot.pause()
            await pilot.pause()

        assert opened == ["https://example.com/docs"]
        assert app.is_running

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Markdown

from minibot.adapters.messaging.console.service import ConsoleService
from minibot.adapters.messaging.console.tui import ConsoleTui, NoopConsole, Transcript
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelResponse, RenderableResponse
from minibot.core.events import OutboundEvent, TurnStopRequestedEvent


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


async def _until(condition, pilot) -> None:
    for _ in range(200):
        if condition():
            return
        await pilot.pause(0.01)
    raise AssertionError("condition never became true")


@pytest.mark.asyncio
async def test_escape_stops_the_running_turn_and_warns_when_the_stop_came_too_early() -> None:
    bus = EventBus()
    service = ConsoleService(bus, chat_id=3, user_id=4, console=NoopConsole())
    await service.start()
    stops = bus.subscribe(types=(TurnStopRequestedEvent,))
    stop_requests: list[TurnStopRequestedEvent] = []

    async def dispatcher_stand_in() -> None:
        async for event in stops:
            stop_requests.append(event)
            # The first stop lands before the turn starts, so it is refused, as the dispatcher would.
            text, metadata = (
                ("Nothing is running.", {"turn_stop": "nothing_running"})
                if len(stop_requests) == 1
                else ("Stopped.", {})
            )
            response = ChannelResponse(
                channel="console",
                chat_id=3,
                text=text,
                render=RenderableResponse(kind="text", text=text),
                metadata=metadata,
            )
            await bus.publish(OutboundEvent(response=response))

    responder = asyncio.create_task(dispatcher_stand_in())
    app = ConsoleTui(service, timeout_seconds=30)
    warnings: list[str] = []
    try:
        async with app.run_test() as pilot:
            with patch.object(
                app,
                "notify",
                lambda message, **kwargs: warnings.append(message) if kwargs.get("severity") == "warning" else None,
            ):
                await pilot.press("escape")
                await pilot.press("h", "i", "enter")
                await _until(lambda: app._busy, pilot)
                await pilot.press("escape")
                await _until(lambda: warnings, pilot)
                await pilot.press("escape")
                await _until(lambda: not app._busy, pilot)
    finally:
        await stops.close()
        responder.cancel()
        await service.stop()

    assert [(event.channel, event.chat_id, event.user_id) for event in stop_requests] == [("console", 3, 4)] * 2
    assert warnings == ["Nothing to stop yet; press Esc again."]
    assert app._turns[-1].markdown == "Stopped."

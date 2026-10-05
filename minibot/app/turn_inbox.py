from __future__ import annotations

from collections import deque

from minibot.core.events import MessageEvent


class TurnInbox:
    """Messages the owner sent to a chat while its turn is running, in arrival order."""

    def __init__(self) -> None:
        self._events: deque[MessageEvent] = deque()
        self.consumed: list[str] = []
        self.user_message_recorded = False
        self.answer_ready = False

    def put(self, event: MessageEvent) -> None:
        self._events.append(event)

    def has_pending(self) -> bool:
        return bool(self._events)

    def take_all(self) -> list[MessageEvent]:
        events = list(self._events)
        self._events.clear()
        return events

    def mark_consumed(self, event_id: str) -> None:
        self.consumed.append(event_id)

    def requeue(self, events: list[MessageEvent]) -> None:
        self._events.extendleft(reversed(events))

    def leftover(self) -> list[MessageEvent]:
        events = list(self._events)
        self._events.clear()
        return events

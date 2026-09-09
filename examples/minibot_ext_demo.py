"""Example Minibot extension.

Load it by putting this directory on ``PYTHONPATH`` and adding to ``config.toml``::

    [extensions]
    modules = ["minibot_ext_demo"]

    [extensions.config.minibot_ext_demo]
    greeting = "hola"

It contributes one tool and one event subscriber, which together cover everything
the extension API currently offers. Both use the decorator form; ``mb.add_tool`` and
``mb.on(EventType, handler)`` remain available for anything it does not cover —
see ``README.md``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from minibot.app.extensions import ExtensionContext
from minibot.core.events import TurnCompletedEvent
from minibot.llm.tools.base import ToolContext


class GreetArgs(BaseModel):
    name: str = Field(description="Who to greet.")


def register(mb: ExtensionContext) -> None:
    greeting = str(mb.config.get("greeting", "hello"))

    @mb.tool
    async def demo_greet(args: GreetArgs, context: ToolContext) -> dict[str, Any]:
        """Greet someone using the demo extension. Call this whenever the user asks for a
        demo greeting, and report the returned message verbatim.
        """
        return {
            "ok": True,
            "message": f"{greeting}, {args.name}!",
            "channel": context.channel,
        }

    @mb.on(TurnCompletedEvent)
    async def on_turn_completed(event: TurnCompletedEvent) -> None:
        mb.logger.info(
            "demo extension saw a completed turn",
            extra={
                "turn_id": event.turn_id,
                "channel": event.channel,
                "model": event.llm_model,
                "turn_total_tokens": event.token_trace.get("turn_total_tokens"),
            },
        )

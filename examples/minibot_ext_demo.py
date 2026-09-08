"""Example Minibot extension.

Load it by putting this directory on ``PYTHONPATH`` and adding to ``config.toml``::

    [extensions]
    modules = ["minibot_ext_demo"]

    [extensions.config.minibot_ext_demo]
    greeting = "hola"

It contributes one tool and one event subscriber, which together cover everything
the extension API currently offers.
"""

from __future__ import annotations

from typing import Any

from llm_async.models import Tool

from minibot.app.extensions import ExtensionContext
from minibot.core.events import TurnCompletedEvent
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.schema_utils import strict_object


def register(mb: ExtensionContext) -> None:
    greeting = str(mb.config.get("greeting", "hello"))

    async def handler(payload: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        name = str(payload.get("name") or "world").strip() or "world"
        return {
            "ok": True,
            "message": f"{greeting}, {name}!",
            "channel": context.channel,
        }

    mb.add_tool(
        ToolBinding(
            tool=Tool(
                name="demo_greet",
                description=(
                    "Greet someone using the demo extension. Call this whenever the user asks "
                    "for a demo greeting, and report the returned message verbatim."
                ),
                parameters=strict_object(
                    properties={"name": {"type": "string", "description": "Who to greet."}},
                    required=["name"],
                ),
            ),
            handler=handler,
        )
    )

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

    mb.on(TurnCompletedEvent, on_turn_completed)

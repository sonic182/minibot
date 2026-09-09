from __future__ import annotations

from minibot.adapters.messaging.telegram.service import TelegramService
from minibot.app.extensions import ExtensionContext


def register(mb: ExtensionContext) -> None:
    if mb.entrypoint != "daemon":
        # Console is single-channel. Constructing the service is what matters, not starting
        # it: TelegramService subscribes to the bus in __init__ with a blocking subscription
        # and only drains it in start(), so a built-but-unstarted one fills its queue and
        # stalls every publish.
        return
    config = mb.settings.channels.telegram
    if not (config.enabled and config.bot_token):
        return
    mb.add_service(TelegramService(config, mb.event_bus, mb.settings.tools.file_storage))

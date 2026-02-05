import asyncio
import asyncio
import logging
import signal
from contextlib import asynccontextmanager
from typing import Any

from ..adapters.container import AppContainer
from ..adapters.messaging.telegram.service import TelegramService
from .dispatcher import Dispatcher


async def run() -> None:
    AppContainer.configure()
    logger = AppContainer.get_logger()
    logger.info("booting minibot", extra={"component": "daemon"})
    event_bus = AppContainer.get_event_bus()
    dispatcher = Dispatcher(event_bus)
    telegram_config = AppContainer.get_telegram_config()
    telegram_service = None
    if telegram_config.enabled and telegram_config.bot_token:
        telegram_service = TelegramService(telegram_config, event_bus)

    services: list[Any] = [dispatcher]
    if telegram_service is not None:
        services.append(telegram_service)

    async with _graceful_shutdown(services, logger) as stop_event:
        await AppContainer.initialize_storage()
        logger.info("starting dispatcher", extra={"component": "dispatcher"})
        await dispatcher.start()
        if telegram_service is not None:
            logger.info("starting telegram service", extra={"component": "telegram"})
            await telegram_service.start()
        logger.info("daemon running in foreground", extra={"component": "daemon"})
        await stop_event.wait()


@asynccontextmanager
async def _graceful_shutdown(services: list, logger: logging.Logger):
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    handles: list[int] = []

    def _on_signal(_: int) -> None:
        logger.info("received stop signal")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _on_signal, sig)

    try:
        yield stop_event
    finally:
        logger.info("shutting down services", extra={"component": "daemon"})
        for service in services:
            await service.stop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


if __name__ == "__main__":
    import asyncio

    asyncio.run(run())

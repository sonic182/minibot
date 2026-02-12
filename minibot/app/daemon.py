import asyncio
import logging
import signal
from contextlib import asynccontextmanager
from typing import Any

from minibot.adapters.container import AppContainer
from minibot.adapters.messaging.telegram.service import TelegramService
from minibot.app.dispatcher import Dispatcher


async def run() -> None:
    AppContainer.configure()
    logger = AppContainer.get_logger()
    settings = AppContainer.get_settings()
    enabled_tools = []
    if settings.tools.kv_memory.enabled:
        enabled_tools.append("kv_memory")
    if settings.tools.http_client.enabled:
        enabled_tools.append("http_client")
    if settings.tools.time.enabled:
        enabled_tools.append("time")
    if settings.tools.calculator.enabled:
        enabled_tools.append("calculator")
    if settings.tools.python_exec.enabled:
        enabled_tools.append("python_exec")
    if settings.tools.file_storage.enabled:
        enabled_tools.append("file_storage")
    if settings.scheduler.prompts.enabled:
        enabled_tools.append("scheduler")
    logger.info(
        "tool configuration loaded",
        extra={"tools_enabled": enabled_tools or ["none"]},
    )
    logger.info("booting minibot", extra={"component": "daemon"})
    event_bus = AppContainer.get_event_bus()
    dispatcher = Dispatcher(event_bus)
    scheduler_service = AppContainer.get_scheduled_prompt_service()
    telegram_config = AppContainer.get_telegram_config()
    telegram_service = None
    if telegram_config.enabled and telegram_config.bot_token:
        telegram_service = TelegramService(telegram_config, event_bus, settings.tools.file_storage)

    services: list[Any] = [dispatcher]
    if telegram_service is not None:
        services.append(telegram_service)
    if scheduler_service is not None:
        services.append(scheduler_service)

    async with _graceful_shutdown(services, logger) as stop_event:
        await AppContainer.initialize_storage()
        logger.info("starting dispatcher", extra={"component": "dispatcher"})
        await dispatcher.start()
        if scheduler_service is not None:
            logger.info("starting scheduler service", extra={"component": "scheduler"})
            await scheduler_service.start()
        if telegram_service is not None:
            logger.info("starting telegram service", extra={"component": "telegram"})
            await telegram_service.start()
        logger.info("daemon running in foreground", extra={"component": "daemon"})
        await stop_event.wait()


@asynccontextmanager
async def _graceful_shutdown(services: list, logger: logging.Logger):
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

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


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()

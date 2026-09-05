import argparse
import asyncio
import logging
import signal
import sys
from contextlib import asynccontextmanager
from typing import Any

from minibot.adapters.container import AppContainer
from minibot.adapters.messaging.telegram.service import TelegramService
from minibot.app.console import main as console_main
from minibot.app.dispatcher import Dispatcher
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage
from minibot.core.events import MessageEvent
from minibot.llm.tools.factory import configured_tool_labels
from minibot.shared.utils import summarize_items

try:
    from minibot.adapters.messaging.rabbitmq.service import RabbitMQConsumerService
except ModuleNotFoundError:
    RabbitMQConsumerService = None


async def run() -> None:
    AppContainer.configure()
    await AppContainer.initialize_storage()
    logger = AppContainer.get_logger()
    settings = AppContainer.get_settings()
    strip_logs = bool(getattr(getattr(settings, "llm", None), "strip_logs", False))
    enabled_tools = configured_tool_labels(settings)
    tool_log_extra: dict[str, Any] = {"tools_enabled": enabled_tools or ["none"]}
    if strip_logs:
        tool_summary = summarize_items(enabled_tools)
        tool_log_extra = {
            "tools_enabled_count": tool_summary["count"],
            "tools_enabled_preview": tool_summary["preview"],
        }
    logger.info(
        "tool configuration loaded",
        extra=tool_log_extra,
    )
    logger.info("booting minibot", extra={"component": "daemon"})
    event_bus = AppContainer.get_event_bus()
    dispatcher = Dispatcher(event_bus)
    scheduler_service = AppContainer.get_scheduled_prompt_service()
    telegram_config = AppContainer.get_telegram_config()
    telegram_service = None
    if telegram_config.enabled and telegram_config.bot_token:
        telegram_service = TelegramService(telegram_config, event_bus, settings.tools.file_storage)

    rabbitmq_config = settings.rabbitmq
    rabbitmq_service = None
    if rabbitmq_config.enabled:
        rabbitmq_service_cls = RabbitMQConsumerService
        if rabbitmq_service_cls is None:
            from minibot.adapters.messaging.rabbitmq.service import RabbitMQConsumerService as rabbitmq_service_cls
        task_manager = AppContainer.get_task_manager()
        rabbitmq_service = rabbitmq_service_cls(rabbitmq_config, event_bus, task_manager)

    services: list[Any] = [dispatcher]
    if telegram_service is not None:
        services.append(telegram_service)
    if scheduler_service is not None:
        services.append(scheduler_service)
    if rabbitmq_service is not None:
        services.append(rabbitmq_service)

    async with _graceful_shutdown(services, logger) as stop_event:
        logger.info("starting dispatcher", extra={"component": "dispatcher"})
        await dispatcher.start()
        await _replay_pending_turns(event_bus, logger)
        if scheduler_service is not None:
            logger.info("starting scheduler service", extra={"component": "scheduler"})
            await scheduler_service.start()
        if telegram_service is not None:
            logger.info("starting telegram service", extra={"component": "telegram"})
            await telegram_service.start()
        if rabbitmq_service is not None:
            logger.info("starting rabbitmq consumer", extra={"component": "rabbitmq"})
            await rabbitmq_service.start()
        logger.info("daemon running in foreground", extra={"component": "daemon"})
        await stop_event.wait()


async def _replay_pending_turns(event_bus: EventBus, logger: logging.Logger) -> None:
    """Re-deliver message turns that never finished before the last shutdown/crash.

    ``Dispatcher._handle_message`` marks a turn pending before processing and clears it once the
    turn finishes (success or a handled exception); a row that survives to boot means the process
    died mid-turn, so we replay it through the normal event bus path.
    """
    store = AppContainer.get_pending_turn_store()
    pending = await store.list_pending()
    if not pending:
        return
    logger.warning(
        "replaying pending message turns from previous run",
        extra={"component": "daemon", "count": len(pending)},
    )
    for event_id, message_json in pending:
        message = ChannelMessage.model_validate_json(message_json)
        await event_bus.publish(MessageEvent(event_id=event_id, message=message))


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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minibot")
    parser.add_subparsers(dest="command").add_parser("console", add_help=False, help="Run the console channel.")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["console"]:
        console_main(args[1:])
        return
    if args:
        build_arg_parser().parse_args(args)
    asyncio.run(run())


if __name__ == "__main__":
    main()

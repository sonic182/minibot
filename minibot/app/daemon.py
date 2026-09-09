import argparse
import asyncio
import logging
import signal
import sys
from contextlib import asynccontextmanager
from typing import Any

from minibot.adapters.container import AppContainer
from minibot.app.console import main as console_main
from minibot.app.dispatcher import Dispatcher
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage
from minibot.core.events import MessageEvent
from minibot.shared.utils import summarize_items


async def run() -> None:
    AppContainer.configure()
    await AppContainer.initialize_storage()
    logger = AppContainer.get_logger()
    settings = AppContainer.get_settings()
    event_bus = AppContainer.get_event_bus()
    dispatcher = Dispatcher(event_bus)
    strip_logs = bool(getattr(getattr(settings, "llm", None), "strip_logs", False))
    enabled_tools = dispatcher.main_agent_tool_names or ["none"]
    tool_log_extra: dict[str, Any] = {"tools_enabled": enabled_tools}
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
    extensions = AppContainer.get_extensions()

    services: list[Any] = [dispatcher]
    if not extensions.is_empty():
        services.append(extensions)

    async with _graceful_shutdown(services, logger) as stop_event:
        logger.info("starting dispatcher", extra={"component": "dispatcher"})
        await dispatcher.start()
        if not extensions.is_empty():
            logger.info("starting extensions", extra={"component": "extensions", "extensions": extensions.names()})
            await extensions.start()
        await _replay_pending_turns(event_bus, logger)
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
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("console", add_help=False, help="Run the console channel.")
    commands.add_parser("configure", add_help=False, help="Configure Minibot interactively.")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["console"]:
        console_main(args[1:])
        return
    if args[:1] == ["configure"]:
        from minibot.adapters.config.configurator import main as configure_main

        configure_main(args[1:])
        return
    if args:
        build_arg_parser().parse_args(args)
    asyncio.run(run())


if __name__ == "__main__":
    main()

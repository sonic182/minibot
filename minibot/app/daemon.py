import argparse
import asyncio
import logging
import secrets
import signal
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from minibot import __version__
from minibot.adapters.container import AppContainer
from minibot.adapters.messaging.web import WebChannelService
from minibot.app.console import main as console_main
from minibot.app.dispatcher import Dispatcher
from minibot.app.event_bus import EventBus
from minibot.core.channels import ChannelMessage
from minibot.core.events import MessageEvent
from minibot.shared.utils import summarize_items


async def run() -> None:
    started_at = datetime.now(UTC)
    AppContainer.configure()
    await AppContainer.initialize_storage()
    logger = AppContainer.get_logger()
    settings = AppContainer.get_settings()
    event_bus = AppContainer.get_event_bus()
    dispatcher = Dispatcher(event_bus)
    strip_logs = bool(getattr(getattr(settings, "llm", None), "strip_logs", False))
    enabled_tools = dispatcher.main_agent_tool_names or ["none"]
    tool_summary = summarize_items(enabled_tools)
    logger.info(
        "tool configuration loaded",
        extra={
            "tools_enabled_count": tool_summary["count"],
            "tools_enabled_preview": tool_summary["preview"],
        },
    )
    if not strip_logs:
        logger.debug("tools enabled", extra={"tools_enabled": enabled_tools})
    logger.info("booting minibot", extra={"component": "daemon"})
    extensions = AppContainer.get_extensions()

    web_channel = WebChannelService(event_bus) if settings.http.enabled else None
    http_server = _build_http_server(settings, dispatcher, extensions, started_at, logger, web_channel)

    services: list[Any] = [dispatcher]
    if not extensions.is_empty():
        services.append(extensions)
    if web_channel is not None:
        services.append(web_channel)
    if http_server is not None:
        services.append(http_server)

    async with _graceful_shutdown(services, logger) as stop_event:
        logger.info("starting dispatcher", extra={"component": "dispatcher"})
        await dispatcher.start()
        if not extensions.is_empty():
            logger.info("starting extensions", extra={"component": "extensions", "extensions": extensions.names()})
            await extensions.start()
        if web_channel is not None:
            await web_channel.start()
        if http_server is not None:
            await http_server.start()
        await _replay_pending_turns(event_bus, logger)
        logger.info("daemon running in foreground", extra={"component": "daemon"})
        await stop_event.wait()


def _build_http_server(
    settings: Any,
    dispatcher: Dispatcher,
    extensions: Any,
    started_at: datetime,
    logger: logging.Logger,
    web_channel: WebChannelService | None = None,
) -> Any:
    """Build the HTTP server, or warn about the routes nobody will serve when it is off."""
    routes = extensions.routes
    if not settings.http.enabled:
        if routes:
            logger.warning(
                "extensions registered http routes but [http] enabled is false",
                extra={"component": "http", "routes": [path for path, _, _ in routes]},
            )
        return None
    # Imported here so the starlette/uvicorn extra is only required when the server is switched on.
    from minibot.adapters.http import (
        DashboardData,
        HttpServer,
        build_chat_route,
        build_chat_socket,
        build_dashboard_route,
        build_history_route,
        set_nav_entries,
    )
    from minibot.app.token_limits_autoconfig import effective_base_url
    from minibot.llm.services.provider_target import resolve_target_provider

    pending_turn_store = AppContainer.get_pending_turn_store()

    async def _count_pending_turns() -> int:
        return len(await pending_turn_store.list_pending())

    # settings.llm.provider is the wire protocol (e.g. "openai_responses"), not the backend
    # actually serving the model when a custom base_url routes through it (e.g. opencode-go) --
    # resolve_target_provider is the same lookup the token auto-config uses to hit the right
    # models.dev catalog entry, so it doubles as the honest answer to "who's really serving this".
    base_url = effective_base_url(settings, provider_name=settings.llm.provider)
    real_provider = resolve_target_provider(provider_name=settings.llm.provider, base_url=base_url)

    memory = AppContainer.get_memory_backend()
    history_route = build_history_route(memory)
    socket_token = secrets.token_urlsafe(32) if web_channel is not None else None
    chat_route = build_chat_route(socket_token) if socket_token is not None else None
    extra_routes = [history_route, *([chat_route] if chat_route is not None else []), *routes]
    nav_entries = [("/", "Home"), ("/history", "History")]
    if chat_route is not None:
        nav_entries.append(("/chat", "Chat"))
    nav_entries.extend(extensions.pages())
    set_nav_entries(nav_entries)

    dashboard_route = build_dashboard_route(
        DashboardData(
            extensions=extensions.summaries(),
            tool_names=dispatcher.main_agent_tool_names,
            routes=extra_routes,
            started_at=started_at,
            llm_provider=real_provider,
            llm_model=settings.llm.model,
            telegram_enabled=settings.channels.telegram.enabled,
            pending_turns=_count_pending_turns,
        )
    )
    websockets = []
    if web_channel is not None and socket_token is not None:
        websockets.append(build_chat_socket(web_channel, memory, socket_token))
    return HttpServer(
        settings.http,
        [dashboard_route, *extra_routes],
        websockets,
        environment=settings.runtime.environment,
    )


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
    parser.add_argument("--version", action="version", version=f"minibot {__version__}")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("console", add_help=False, help="Run the console channel.")
    commands.add_parser("configure", add_help=False, help="Configure Minibot interactively.")
    commands.add_parser("codex", add_help=False, help="Codex subscription utilities (e.g. `minibot codex login`).")
    commands.add_parser("vault", add_help=False, help="Manage the encrypted credential vault.")
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
    if args[:1] == ["vault"]:
        from minibot.adapters.vault.cli import main as vault_main

        vault_main(args[1:])
        return
    if args[:1] == ["codex"]:
        if args[1:2] == ["login"]:
            from minibot.app.codex_login import main as codex_login_main

            codex_login_main(args[2:])
            return
        raise SystemExit("Usage: minibot codex login [--device-code] [--auth-file PATH]")
    if args:
        build_arg_parser().parse_args(args)
    asyncio.run(run())


if __name__ == "__main__":
    main()

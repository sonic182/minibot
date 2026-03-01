from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import sys
from typing import Optional

from minibot.adapters.container import AppContainer
from minibot.adapters.messaging.console.service import ConsoleService
from minibot.app.dispatcher import Dispatcher
from minibot.shared.console_compat import CompatConsole, prompt_input


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="minibot-console")
    parser.add_argument("--once", type=str, default=None, help="Send one message and exit. Use '-' to read stdin.")
    parser.add_argument("--chat-id", type=int, default=1)
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--config", type=str, default=None, help="Optional config.toml path.")
    return parser


async def run(
    *,
    once: str | None,
    chat_id: int,
    user_id: int,
    timeout_seconds: float,
    config_path: str | None,
) -> None:
    console = CompatConsole()
    effective_timeout_seconds = max(120.0, float(timeout_seconds))
    resolved_config_path = Path(config_path).expanduser() if config_path else None
    AppContainer.configure(resolved_config_path)
    logger = AppContainer.get_logger()
    _configure_console_file_only_logging(logger)
    event_bus = AppContainer.get_event_bus()
    dispatcher = Dispatcher(event_bus)
    logger.info(
        "console tool configuration loaded",
        extra={"main_agent_tools_enabled": dispatcher.main_agent_tool_names or ["none"]},
    )
    console_service = ConsoleService(event_bus, chat_id=chat_id, user_id=user_id, console=console)
    await AppContainer.initialize_storage()
    await dispatcher.start()
    await console_service.start()

    try:
        if once is not None:
            message = once
            if message == "-":
                message = sys.stdin.read()
            stripped = message.strip()
            if not stripped:
                raise ValueError("empty input for --once")
            await console_service.publish_user_message(stripped)
            responded = await _wait_for_response_or_warn(
                console_service=console_service,
                timeout_seconds=effective_timeout_seconds,
                logger=logger,
                console=console,
            )
            if not responded:
                return
            return

        confirm_exit = False
        while True:
            try:
                user_text = prompt_input("[bold cyan]you[/]")
            except KeyboardInterrupt:
                if confirm_exit:
                    return
                console.print("[yellow]Press Ctrl+C again to exit, or type 'exit'.[/]")
                confirm_exit = True
                continue
            stripped = user_text.strip()
            if not stripped:
                continue
            confirm_exit = False
            if stripped.lower() in {"quit", "exit"}:
                return
            await console_service.publish_user_message(stripped)
            await _wait_for_response_or_warn(
                console_service=console_service,
                timeout_seconds=effective_timeout_seconds,
                logger=logger,
                console=console,
            )
    finally:
        await console_service.stop()
        await dispatcher.stop()


def main(argv: Optional[list[str]] = None) -> None:
    args = build_arg_parser().parse_args(argv)
    try:
        asyncio.run(
            run(
                once=args.once,
                chat_id=args.chat_id,
                user_id=args.user_id,
                timeout_seconds=args.timeout_seconds,
                config_path=args.config,
            )
        )
    except KeyboardInterrupt:
        return


async def _wait_for_response_or_warn(
    *,
    console_service: ConsoleService,
    timeout_seconds: float,
    logger: logging.Logger,
    console: CompatConsole,
) -> bool:
    try:
        await console_service.wait_for_response(timeout_seconds)
        return True
    except asyncio.TimeoutError:
        logger.warning("timed out waiting for console response", extra={"timeout_seconds": timeout_seconds})
        console.print(
            f"[yellow]Timeout getting response after {int(timeout_seconds)}s. "
            "The request may still be running; try again in a moment.[/]"
        )
        return False


if __name__ == "__main__":
    main()


def _configure_console_file_only_logging(logger: object) -> None:
    if not isinstance(logger, logging.Logger):
        return
    file_handler = next(
        (handler for handler in logger.handlers if isinstance(handler, logging.FileHandler)),
        None,
    )
    if file_handler is None:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "minibot.log")
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        file_handler.setFormatter(formatter)
    logger.handlers = [file_handler]

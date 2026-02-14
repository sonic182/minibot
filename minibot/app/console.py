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
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
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
    resolved_config_path = Path(config_path).expanduser() if config_path else None
    AppContainer.configure(resolved_config_path)
    logger = AppContainer.get_logger()
    _configure_console_file_only_logging(logger)
    event_bus = AppContainer.get_event_bus()
    dispatcher = Dispatcher(event_bus)
    console_service = ConsoleService(event_bus, chat_id=chat_id, user_id=user_id, console=console)
    await AppContainer.initialize_storage()
    await dispatcher.start()
    await console_service.start()

    try:
        if once is not None:
            message = once
            if message == "-":
                message = await asyncio.to_thread(sys.stdin.read)
            stripped = message.strip()
            if not stripped:
                raise ValueError("empty input for --once")
            await console_service.publish_user_message(stripped)
            await console_service.wait_for_response(timeout_seconds)
            return

        while True:
            user_text = await asyncio.to_thread(prompt_input, "[bold cyan]you[/]")
            stripped = user_text.strip()
            if not stripped:
                continue
            if stripped.lower() in {"quit", "exit"}:
                return
            await console_service.publish_user_message(stripped)
            await console_service.wait_for_response(timeout_seconds)
    except asyncio.TimeoutError as exc:
        logger.error("timed out waiting for console response")
        raise RuntimeError("timed out waiting for console response") from exc
    finally:
        await console_service.stop()
        await dispatcher.stop()


def main(argv: Optional[list[str]] = None) -> None:
    args = build_arg_parser().parse_args(argv)
    asyncio.run(
        run(
            once=args.once,
            chat_id=args.chat_id,
            user_id=args.user_id,
            timeout_seconds=args.timeout_seconds,
            config_path=args.config,
        )
    )


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

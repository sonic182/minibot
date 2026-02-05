from __future__ import annotations

import logging
from pathlib import Path

from logfmter import Logfmter

from minibot.adapters.config.schema import LoggingConfig


def configure_logging(config: LoggingConfig) -> logging.Logger:
    if config.logfmt_enabled:
        formatter = Logfmter(keys=["levelname", "name", "msg"])
    else:
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    logger = logging.getLogger("minibot")
    level = getattr(logging, getattr(config, "log_level", "INFO").upper(), logging.INFO)
    logger.setLevel(level)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    file_handler = logging.FileHandler(log_dir / "minibot.log")
    file_handler.setFormatter(formatter)

    logger.handlers = [stream_handler, file_handler]
    logger.propagate = False
    return logger

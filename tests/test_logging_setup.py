from __future__ import annotations

from pathlib import Path

from minibot.adapters.config.schema import LoggingConfig
from minibot.adapters.logging.setup import configure_logging


def test_configure_logging_sets_handlers_and_level(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    config = LoggingConfig(logfmt_enabled=False, log_level="DEBUG")

    logger = configure_logging(config)

    assert logger.level == 10
    assert len(logger.handlers) == 2
    assert (tmp_path / "logs" / "minibot.log").exists()

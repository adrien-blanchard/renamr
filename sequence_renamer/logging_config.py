"""Application logging suitable for a windowed executable."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .settings import compatibility_state_directory


LOGGER_NAME = "sequence_renamer"


def configure_logging(directory: Path | None = None) -> tuple[logging.Logger, Path]:
    """Configure a rotating UTF-8 log and return the logger and log path."""

    # Retain the legacy hidden state root so upgrades share one operational
    # identity and never strand transaction diagnostics.
    log_directory = directory or (compatibility_state_directory() / "logs")
    log_directory.mkdir(parents=True, exist_ok=True)
    log_path = log_directory / "renamr.log"

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not any(
        isinstance(handler, RotatingFileHandler)
        and Path(getattr(handler, "baseFilename", "")) == log_path
        for handler in logger.handlers
    ):
        handler = RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        )
        logger.addHandler(handler)

    return logger, log_path

"""Shared logging setup for backend runtime modules."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
import os
from pathlib import Path

DEFAULT_LOG_LEVEL = "INFO"
FILE_HANDLER_NAME = "tabuflow-file"
LOG_LEVEL_ENV = "TABUFLOW_LOG_LEVEL"
LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
REPO_ROOT = Path(__file__).resolve().parents[1]
STARTED_AT = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def configure_logging(level: str | int | None = None) -> None:
    """Configure process logging once while respecting existing harness handlers."""
    configured_level = level or os.getenv(LOG_LEVEL_ENV) or DEFAULT_LOG_LEVEL
    if isinstance(configured_level, int):
        resolved_level = configured_level
    else:
        resolved_level = logging.getLevelNamesMapping().get(configured_level.strip().upper())
        if resolved_level is None:
            raise ValueError(f"Unknown log level: {configured_level}")

    root_logger = logging.getLogger()
    formatter = logging.Formatter(LOG_FORMAT)

    if not root_logger.handlers:
        logging.basicConfig(level=resolved_level, format=LOG_FORMAT)
    else:
        root_logger.setLevel(resolved_level)
        for handler in root_logger.handlers:
            handler.setLevel(resolved_level)
            if handler.formatter is None:
                handler.setFormatter(formatter)

    log_dir = REPO_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{STARTED_AT}.log"
    if all(handler.get_name() != FILE_HANDLER_NAME for handler in root_logger.handlers):
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.set_name(FILE_HANDLER_NAME)
        file_handler.setLevel(resolved_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    logging.getLogger("src").setLevel(resolved_level)

"""Structured logging setup for LokDocSQR.

This module must not import core.safe_fs (architectural invariant).
Only the standard library logging machinery is used here.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from lokdocsqr.core.config import AppConfig


_LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
_LOGGER_NAME = "lokdocsqr"
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5


def setup_logging(config: AppConfig | None = None) -> logging.Logger:
    """Configure and return the application-wide logger.

    Adds a StreamHandler always, and a RotatingFileHandler when config is
    provided and its logs_dir is available. Existing handlers are removed
    to make this function idempotent and safe to call repeatedly.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # pragma: no cover - defensive
            pass

    formatter = logging.Formatter(_LOG_FORMAT)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(logging.INFO)
    logger.addHandler(stream_handler)

    if config is not None and getattr(config, "logs_dir", None):
        logs_dir = Path(config.logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / "lokdocsqr.log"
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)

    return logger

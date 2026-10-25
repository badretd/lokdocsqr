"""Tests for lokdocsqr.core.logging.setup_logging."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

import pytest

from lokdocsqr.core import logging as lok_logging
from lokdocsqr.core.config import AppConfig


@pytest.fixture()
def isolated_logger():
    """Capture and restore the global ``lokdocsqr`` logger."""
    logger = logging.getLogger("lokdocsqr")
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    logger.handlers.clear()
    yield logger
    for handler in list(logger.handlers):
        try:
            handler.close()
        except Exception:  # pragma: no cover - defensive
            pass
    logger.handlers.clear()
    logger.handlers.extend(saved_handlers)
    logger.setLevel(saved_level)
    logger.propagate = saved_propagate


def _stream_handlers(logger):
    return [
        h for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
    ]


def _file_handlers(logger):
    return [h for h in logger.handlers if isinstance(h, logging.FileHandler)]


def test_setup_logging_returns_named_logger(isolated_logger) -> None:
    returned = lok_logging.setup_logging()
    assert returned is isolated_logger
    assert returned.name == "lokdocsqr"


def test_setup_logging_sets_level_and_propagate(isolated_logger) -> None:
    logger = lok_logging.setup_logging()
    assert logger.level == logging.DEBUG
    assert logger.propagate is False


def test_setup_logging_without_config_adds_stream_only(isolated_logger) -> None:
    logger = lok_logging.setup_logging()
    stream = _stream_handlers(logger)
    assert len(stream) == 1
    assert stream[0].level == logging.INFO
    assert _file_handlers(logger) == []


def test_setup_logging_with_config_adds_file_handler(
    isolated_logger, tmp_path: Path
) -> None:
    config = AppConfig(base_dir=tmp_path)
    logger = lok_logging.setup_logging(config)

    file_handlers = _file_handlers(logger)
    assert len(file_handlers) == 1
    handler = file_handlers[0]
    assert isinstance(handler, logging.handlers.RotatingFileHandler)
    assert handler.level == logging.DEBUG
    assert handler.maxBytes == lok_logging._MAX_BYTES
    assert handler.backupCount == lok_logging._BACKUP_COUNT


def test_setup_logging_creates_logs_dir(isolated_logger, tmp_path: Path) -> None:
    logs_dir = tmp_path / "custom_logs"
    config = AppConfig(base_dir=tmp_path, logs_dir=logs_dir)
    assert not logs_dir.exists()
    lok_logging.setup_logging(config)
    assert logs_dir.is_dir()
    assert (logs_dir / "lokdocsqr.log").exists()


def test_setup_logging_is_idempotent(isolated_logger, tmp_path: Path) -> None:
    config = AppConfig(base_dir=tmp_path)
    lok_logging.setup_logging(config)
    first = sorted(type(h).__name__ for h in isolated_logger.handlers)
    lok_logging.setup_logging(config)
    second = sorted(type(h).__name__ for h in isolated_logger.handlers)
    assert first == second
    assert len(isolated_logger.handlers) == 2  # stream + file


def test_setup_logging_replaces_previous_handlers(
    isolated_logger, tmp_path: Path
) -> None:
    lok_logging.setup_logging()
    assert _file_handlers(isolated_logger) == []
    config = AppConfig(base_dir=tmp_path)
    lok_logging.setup_logging(config)
    assert len(_file_handlers(isolated_logger)) == 1


def test_setup_logging_writes_to_file(isolated_logger, tmp_path: Path) -> None:
    config = AppConfig(base_dir=tmp_path)
    logger = lok_logging.setup_logging(config)
    logger.info("hello-from-test")
    for handler in logger.handlers:
        handler.flush()
    log_file = Path(config.logs_dir) / "lokdocsqr.log"
    assert log_file.exists()
    assert "hello-from-test" in log_file.read_text(encoding="utf-8")

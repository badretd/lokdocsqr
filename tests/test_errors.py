"""Verify the exception hierarchy for LokDocSQR."""
from __future__ import annotations

import pytest

from lokdocsqr.core import errors

EXPECTED_ALL = {
    "ConfigError",
    "DryRunInterrupt",
    "GraphParseError",
    "GraphValidationError",
    "LokError",
    "NodeExecutionError",
    "PolicyViolation",
    "SafeFsError",
    "ScopeViolation",
}

SUBCLASSES = sorted(EXPECTED_ALL - {"LokError"})


def test_errors_all_surface() -> None:
    """errors.__all__ must exactly declare the public surface."""
    assert set(errors.__all__) == EXPECTED_ALL
    missing = [name for name in EXPECTED_ALL if not hasattr(errors, name)]
    assert not missing, f"errors missing attributes: {missing}"


def test_lok_error_is_exception() -> None:
    """LokError is the root of the hierarchy."""
    assert issubclass(errors.LokError, Exception)


def test_all_custom_errors_inherit_lok_error() -> None:
    """Every named error must subclass LokError."""
    not_subclasses = [
        name for name in SUBCLASSES
        if not issubclass(getattr(errors, name), errors.LokError)
    ]
    assert not not_subclasses, f"not LokError subclasses: {not_subclasses}"


def test_errors_raise_and_catch_via_lok_error() -> None:
    """Each error can be raised and caught as LokError."""
    for name in SUBCLASSES:
        cls = getattr(errors, name)
        with pytest.raises(errors.LokError):
            raise cls("boom")

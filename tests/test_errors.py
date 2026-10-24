"""Verify the exception hierarchy for LokDocSQR."""
from __future__ import annotations

import inspect

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


def test_errors_all_declares_full_surface() -> None:
    """`errors.__all__` must match the public surface exactly."""
    assert set(errors.__all__) == EXPECTED_ALL
    for name in EXPECTED_ALL:
        assert hasattr(errors, name), f"errors missing attribute: {name}"


def test_lok_error_is_exception() -> None:
    """LokError is the root of the hierarchy."""
    assert issubclass(errors.LokError, Exception)


@pytest.mark.parametrize(
    "name",
    sorted(EXPECTED_ALL - {"LokError"}),
)
def test_error_inherits_lok_error(name: str) -> None:
    """Every named error must be a LokError subclass."""
    cls = getattr(errors, name)
    assert issubclass(cls, errors.LokError), f"{name} is not a LokError"


@pytest.mark.parametrize(
    "name",
    sorted(EXPECTED_ALL - {"LokError"}),
)
def test_error_directly_derives_from_lok_error(name: str) -> None:
    """Each error must subclass LokError directly (no deep tree)."""
    cls = getattr(errors, name)
    assert cls.__bases__ == (errors.LokError,), (
        f"{name} bases are {cls.__bases__}, expected (LokError,)"
    )


@pytest.mark.parametrize(
    "name",
    sorted(EXPECTED_ALL),
)
def test_error_has_docstring(name: str) -> None:
    """Every exception class must document itself."""
    cls = getattr(errors, name)
    assert inspect.getdoc(cls), f"{name} has no docstring"


def test_errors_can_be_raised_and_caught() -> None:
    """Each error can be raised and caught via LokError."""
    for name in sorted(EXPECTED_ALL - {"LokError"}):
        cls = getattr(errors, name)
        with pytest.raises(errors.LokError):
            raise cls("boom")


def test_raising_lok_error_directly_works() -> None:
    """The base class is instantiable."""
    with pytest.raises(errors.LokError):
        raise errors.LokError("boom")

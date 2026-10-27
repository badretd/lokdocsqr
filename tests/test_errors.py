"""Verify the exception hierarchy for LokDocSQR."""
from __future__ import annotations

import pytest

from lokdocsqr.core import errors

# Phase 7a added the PolicyError family. All of them are children of LokError
# (PolicyConfigError / PolicyBlockedError / PolicyTimeoutError /
# ProposalValidationError are direct children of PolicyError, which itself
# derives from LokError). They all appear in ``errors.__all__``.
EXPECTED_ALL = {
    "ConfigError",
    "DryRunInterrupt",
    "GraphParseError",
    "GraphValidationError",
    "LokError",
    "NodeExecutionError",
    "PolicyBlockedError",
    "PolicyConfigError",
    "PolicyError",
    "PolicyTimeoutError",
    "PolicyViolation",
    "ProposalValidationError",
    "SafeFsError",
}

# Every non-root error. `PolicyError` itself lives here too — it must still
# be a `LokError` subclass, and it must still be raisable/catchable as such.
SUBCLASSES = sorted(EXPECTED_ALL - {"LokError"})

# Children of PolicyError — catchable with a single `except PolicyError`.
POLICY_FAMILY = (
    "PolicyConfigError",
    "PolicyBlockedError",
    "PolicyTimeoutError",
    "ProposalValidationError",
)


def test_errors_all_surface() -> None:
    """errors.__all__ must exactly declare the public surface."""
    assert set(errors.__all__) == EXPECTED_ALL
    missing = [name for name in EXPECTED_ALL if not hasattr(errors, name)]
    assert not missing, f"errors missing attributes: {missing}"


def test_lok_error_is_exception() -> None:
    """LokError is the root of the hierarchy."""
    assert issubclass(errors.LokError, Exception)


def test_all_custom_errors_inherit_lok_error() -> None:
    """Every named error must subclass LokError (directly or transitively)."""
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


def test_policy_error_family_inherits_policy_error() -> None:
    """Policy sub-errors must be catchable via ``except PolicyError`` (7c)."""
    assert issubclass(errors.PolicyError, errors.LokError)
    for name in POLICY_FAMILY:
        cls = getattr(errors, name)
        assert issubclass(cls, errors.PolicyError), (
            f"{name} is not a PolicyError subclass"
        )
        with pytest.raises(errors.PolicyError):
            raise cls("policy boom")
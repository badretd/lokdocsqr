"""Shared fixtures for the unit test suite."""

from __future__ import annotations

import pytest

from lokdocsqr.core.node_api import registry


@pytest.fixture(autouse=True)
def _clean_node_registry():
    """Isolate tests that touch the global node registry."""
    registry.clear()
    yield
    registry.clear()

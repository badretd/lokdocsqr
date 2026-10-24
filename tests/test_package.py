"""Verify the LokDocSQR package and its subpackages import cleanly."""
from __future__ import annotations

import importlib

import pytest

SUBPACKAGES = (
    "lokdocsqr",
    "lokdocsqr.core",
    "lokdocsqr.core.graph",
    "lokdocsqr.core.executor",
    "lokdocsqr.core.node_api",
    "lokdocsqr.nodes",
    "lokdocsqr.nodes.triggers",
    "lokdocsqr.nodes.access",
    "lokdocsqr.nodes.fs_ops",
    "lokdocsqr.nodes.planning",
    "lokdocsqr.nodes.flow",
    "lokdocsqr.nodes.llm",
    "lokdocsqr.nodes.utils",
    "lokdocsqr.gui",
    "lokdocsqr.gui.views",
    "lokdocsqr.gui.viewmodels",
    "lokdocsqr.gui.widgets",
    "lokdocsqr.cli",
)


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_package_imports(name: str) -> None:
    """Each scaffolded subpackage must import without side effects."""
    importlib.import_module(name)


def test_version_metadata() -> None:
    """Package metadata must match the Phase-0 contract."""
    import lokdocsqr

    assert lokdocsqr.__version__ == "0.1.0"
    assert lokdocsqr.__all__ == ["__version__"]

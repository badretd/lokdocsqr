"""Verify the LokDocSQR package and its subpackages import cleanly."""
from __future__ import annotations

import importlib

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


def test_all_packages_importable() -> None:
    """Every scaffolded subpackage must import without side effects."""
    failures: list[str] = []
    for name in SUBPACKAGES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - collect and report
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    assert not failures, "import failures:\n" + "\n".join(failures)


def test_version_metadata() -> None:
    """Package metadata must match the Phase-0 contract."""
    import lokdocsqr

    assert lokdocsqr.__version__ == "0.1.0"
    assert lokdocsqr.__all__ == ["__version__"]

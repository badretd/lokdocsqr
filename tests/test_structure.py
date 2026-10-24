"""Verify the Phase-0 scaffold produced the expected file tree."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

EXPECTED_FILES = (
    "docs/CONTRACT.md",
    "docs/PHASE_STATE.md",
    "src/lokdocsqr/__init__.py",
    "src/lokdocsqr/core/__init__.py",
    "src/lokdocsqr/core/graph/__init__.py",
    "src/lokdocsqr/core/executor/__init__.py",
    "src/lokdocsqr/core/node_api/__init__.py",
    "src/lokdocsqr/core/models.py",
    "src/lokdocsqr/core/errors.py",
    "src/lokdocsqr/nodes/__init__.py",
    "src/lokdocsqr/nodes/triggers/__init__.py",
    "src/lokdocsqr/nodes/access/__init__.py",
    "src/lokdocsqr/nodes/fs_ops/__init__.py",
    "src/lokdocsqr/nodes/planning/__init__.py",
    "src/lokdocsqr/nodes/flow/__init__.py",
    "src/lokdocsqr/nodes/llm/__init__.py",
    "src/lokdocsqr/nodes/utils/__init__.py",
    "src/lokdocsqr/gui/__init__.py",
    "src/lokdocsqr/gui/views/__init__.py",
    "src/lokdocsqr/gui/viewmodels/__init__.py",
    "src/lokdocsqr/gui/widgets/__init__.py",
    "src/lokdocsqr/gui/app.py",
    "src/lokdocsqr/cli/__init__.py",
    "src/lokdocsqr/cli/main.py",
    "tests/test_architecture.py",
)


@pytest.mark.parametrize("relpath", EXPECTED_FILES)
def test_expected_file_exists(relpath: str) -> None:
    """Every scaffold file listed by the installer must exist on disk."""
    path = ROOT / relpath
    assert path.exists(), f"missing scaffold file: {relpath}"


def test_empty_init_files_are_trivial() -> None:
    """All __init__.py files except the top-level package must be empty."""
    top_level = ROOT / "src" / "lokdocsqr" / "__init__.py"
    for path in (ROOT / "src").rglob("__init__.py"):
        if path == top_level:
            continue
        content = path.read_text(encoding="utf-8").strip()
        assert content == "", (
            f"{path.relative_to(ROOT)} is not empty: {content!r}"
        )


def test_top_level_init_declares_version() -> None:
    """The top-level package __init__ must set __version__ and __all__."""
    text = (ROOT / "src/lokdocsqr/__init__.py").read_text(encoding="utf-8")
    assert '__version__ = "0.1.0"' in text
    assert '__all__ = ["__version__"]' in text

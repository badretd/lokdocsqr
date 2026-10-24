"""Verify the Phase-0 scaffold produced the expected file tree."""
from __future__ import annotations

from pathlib import Path

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


def test_expected_files_exist() -> None:
    """Every scaffold file must exist on disk."""
    missing = [name for name in EXPECTED_FILES if not (ROOT / name).is_file()]
    assert not missing, f"missing scaffold files: {missing}"


def test_init_files_are_empty() -> None:
    """All __init__.py files except the package root must be empty."""
    top_level = ROOT / "src" / "lokdocsqr" / "__init__.py"
    non_empty = []
    for path in (ROOT / "src").rglob("__init__.py"):
        if path == top_level:
            continue
        if path.read_text(encoding="utf-8").strip():
            non_empty.append(str(path.relative_to(ROOT)))
    assert not non_empty, f"unexpected content in: {non_empty}"


def test_top_level_init_metadata() -> None:
    """The package root must declare __version__ and __all__."""
    text = (ROOT / "src/lokdocsqr/__init__.py").read_text(encoding="utf-8")
    assert '__version__ = "0.1.0"' in text
    assert '__all__ = ["__version__"]' in text

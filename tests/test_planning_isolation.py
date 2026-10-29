"""Phase 7b — subprocess import isolation."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _run_python(code):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "subprocess failed\nSTDOUT:\n"
            + proc.stdout
            + "\nSTDERR:\n"
            + proc.stderr
        )
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert lines, "subprocess produced no JSON output"
    return json.loads(lines[-1])


def test_preview_planning_import_no_gui_nodes_qt():
    code = """
import json, sys
import lokdocsqr.core.preview
import lokdocsqr.core.planning.builder
import lokdocsqr.core.planning.validator
bad = sorted(
    m for m in sys.modules
    if m in ("lokdocsqr.gui", "lokdocsqr.nodes", "gui", "nodes")
    or m.startswith("lokdocsqr.gui.")
    or m.startswith("lokdocsqr.nodes.")
    or m.startswith("gui.")
    or m.startswith("nodes.")
    or m.startswith("PySide6")
    or m.startswith("PyQt5")
    or m.startswith("PyQt6")
)
print(json.dumps(bad))
"""
    assert _run_python(code) == []


def test_preview_planning_import_no_optional_deps():
    code = """
import json, sys
import lokdocsqr.core.preview
import lokdocsqr.core.planning.builder
import lokdocsqr.core.planning.validator
bad = sorted(
    m for m in sys.modules
    if m.split(".")[0] in {"pypdfium2", "docx", "pytesseract", "PIL"}
)
print(json.dumps(bad))
"""
    assert _run_python(code) == []


def test_safe_fs_docstring_matches_readonly_caller_list():
    """L4 (phase 8d-a): the ``safe_fs`` module docstring must document
    exactly the 7 known read-only callers from
    ``tests/test_architecture.py`` — ``READONLY_FS_ALLOWED`` plus the
    single ``MUTATION_FS_ALLOWED`` exemption — and must **not** list
    ``core/preview.py``.

    ``core/preview.py`` uses the public ``safe_fs.read_bytes`` API and
    therefore is not a read-only-caller exemption under invariant 9. This
    test pins the docstring against silent drift from the two lists in
    ``test_architecture.py`` (which are the source of truth).
    """
    text = (SRC / "lokdocsqr" / "core" / "safe_fs.py").read_text(encoding="utf-8")

    for module in (
        "core/config.py",
        "core/logging.py",
        "core/paths.py",
        "core/scope.py",
        "core/audit.py",
        "core/graph/validator.py",
        "core/executor/context.py",
    ):
        assert module in text, (
            f"{module} missing from safe_fs docstring — the docstring must "
            "match READONLY_FS_ALLOWED ∪ MUTATION_FS_ALLOWED in "
            "tests/test_architecture.py"
        )

    assert "core/preview.py" not in text, (
        "core/preview.py must not be listed as a read-only caller in the "
        "safe_fs docstring (L4): preview uses the public "
        "safe_fs.read_bytes API, not an invariant-9 exemption. Update the "
        "docstring or add preview to READONLY_FS_ALLOWED, not both."
    )
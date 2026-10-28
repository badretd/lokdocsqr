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


def test_safe_fs_docstring_lists_preview_read_only_caller():
    text = (SRC / "lokdocsqr" / "core" / "safe_fs.py").read_text(encoding="utf-8")
    assert "preview" in text

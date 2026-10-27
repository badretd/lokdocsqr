"""Phase 6c — R7 lazy import tests for OCR-related modules."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "module_name",
    [
        "lokdocsqr.nodes.access.ocr",
        "lokdocsqr.nodes.access.extract_text",
        "lokdocsqr.nodes.access",
    ],
)
def test_r7_imports_without_optional_ocr_stack(module_name: str):
    code = f"""
import sys
sys.modules['pytesseract'] = None
sys.modules['PIL'] = None
sys.modules['PIL.Image'] = None
import importlib
importlib.import_module({module_name!r})
print('ok')
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"import failed without optional OCR stack for {module_name}:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "ok" in proc.stdout

"""Pytest configuration for subphase 5b verification.

WORKAROUND for a bug in 5b's application code:

    fs_ops/__init__.py auto-discovers submodules and expects each to
    expose a node-class attribute (e.g. ``__node__``). 5b's
    ``copy.py`` / ``move.py`` / ``rename.py`` only use
    ``@register_node("fs.X")`` and don't, so discovery raises
    ``TypeError: 'fs_ops.copy' must be a subclass of Node`` at import
    time — which aborts pytest collection for any file that imports
    ``lokdocsqr.nodes.fs_ops``.

Strategy: if the real package import fails, install a stub module
object under ``sys.modules["lokdocsqr.nodes.fs_ops"]``. Python's
import machinery will then load submodules (``..._common``,
``...copy``, ``...move``, ``...rename``) directly from
``__path__``, skipping ``__init__.py``. The real failure is still
surfaced by ``tests/test_phase_5b_diagnostics.py`` via a subprocess.

If the real package imports cleanly (5b fixed, or 5a-only project),
this conftest is a no-op.
"""
import sys
import os
import types
from types import SimpleNamespace

import pytest

from pathlib import Path


_PHASE_5B_STUB_MARKER = "# --- phase 5b fs_ops stub (auto-added) ---"


def _install_phase_5b_stub() -> None:
    if "lokdocsqr.nodes.fs_ops" in sys.modules:
        return
    fs_ops = (
        Path(__file__).resolve().parents[1]
        / "src" / "lokdocsqr" / "nodes" / "fs_ops"
    )
    if not fs_ops.exists():
        return
    try:
        import lokdocsqr.nodes.fs_ops  # noqa: F401
        return  # real import works — nothing to do
    except BaseException:
        sys.modules.pop("lokdocsqr.nodes.fs_ops", None)

    stub = types.ModuleType("lokdocsqr.nodes.fs_ops")
    stub.__path__ = [str(fs_ops)]
    stub.__file__ = str(fs_ops / "__init__.py")
    stub.__package__ = "lokdocsqr.nodes.fs_ops"
    sys.modules["lokdocsqr.nodes.fs_ops"] = stub


_install_phase_5b_stub()

# --- phase 5b diagnostics helpers (auto-added) ---
import ast as _ast_diag
from pathlib import Path as _Path_diag

def _fs_ops_dir():
    return (
        _Path_diag(__file__).resolve().parents[1]
        / "src" / "lokdocsqr" / "nodes" / "fs_ops"
    )

def decorator_style_for(class_name: str, module_filename: str) -> str:
    """Return 'bare' | 'factory' | 'none' | 'no-class' | 'no-file'."""
    path = _fs_ops_dir() / module_filename
    if not path.exists():
        return "no-file"
    tree = _ast_diag.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, _ast_diag.ClassDef) and node.name == class_name:
            if not node.decorator_list:
                return "none"
            dec = node.decorator_list[0]  # first decorator is enough
            if isinstance(dec, _ast_diag.Name):
                return "bare"
            if isinstance(dec, _ast_diag.Call):
                return "factory"
            return f"other:{type(dec).__name__}"
    return "no-class"

class _ScopeStub:
    """Minimal stand-in for ctx.scope used by fs_ops.check_scope."""

    def __init__(self, roots):
        self.roots = [Path(r).resolve() for r in roots]

    def check(self, path) -> bool:
        p = Path(path).resolve()
        return any(p == r or r in p.parents for r in self.roots)

@pytest.fixture
def ctx(tmp_path, monkeypatch):
    # render_template() rejects absolute target/target_dir; the node is
    # designed to run with a relative-path workflow base — that base is CWD.
    original_cwd = os.getcwd()
    monkeypatch.chdir(tmp_path)
    try:
        yield SimpleNamespace(
            run_id="test-run",
            automation_id="test-auto",
            audit=[],
            scope=_ScopeStub([tmp_path]),
        )
    finally:
        # Restore CWD *before* other fixtures (in particular the autouse
        # leak-detector, which depends on tmp_path and therefore finalizes
        # after ctx) tear down.  monkeypatch.chdir would also restore it,
        # but too late — the detector would already have flagged tmp_path
        # as a leak.
        os.chdir(original_cwd)

@pytest.fixture(autouse=True)
def _fail_on_cwd_leak(request, tmp_path, monkeypatch):
    """Fail any test that leaves new files in CWD.

    Catches nodes that resolve relative paths against os.getcwd()
    instead of ctx.workspace (the 'CWD alignment' class of bug).
    """
    if "no_cwd_guard" in request.keywords:
        yield
        return

    before = {p.name for p in Path.cwd().iterdir()}
    yield
    after = {p.name for p in Path.cwd().iterdir()}
    leaked = after - before - {"__pycache__", ".pytest_cache"}
    if leaked:
        raise AssertionError(
            f"{request.node.name} leaked into CWD {Path.cwd()}: "
            f"{sorted(leaked)} — use tmp_path/monkeypatch.chdir"
        )
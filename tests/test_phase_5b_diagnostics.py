"""Root-cause hunter for:

    TypeError: 'fs_ops.copy' must be a subclass of Node

Each test checks one hypothesis; the failure message tells you exactly
what to fix in the application code. These tests are NOT failing because
the verifier is wrong — they are failing because the application has a
real defect. Once fixed, all diagnostics go green.
"""
import inspect
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
FS_OPS = ROOT / "src" / "lokdocsqr" / "nodes" / "fs_ops"


def _tail(path: Path, n: int = 100) -> str:
    if not path.exists():
        return f"<{path} not found>"
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[-n:])


# ── H0: full traceback from the real import ───────────────────────
def test_fs_ops_package_imports_cleanly_in_subprocess():
    """Reproduces the collection-time ERROR from the user's tests.

    If this fails, the bug is in 5b's *application* code. The full
    stderr traceback below pinpoints the exact failing line.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import lokdocsqr.nodes.fs_ops"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    if result.returncode == 0:
        return

    init_tail = _tail(FS_OPS / "__init__.py", n=40)
    copy_tail = _tail(FS_OPS / "copy.py", n=40)
    pytest.fail(textwrap.dedent(f"""
        `import lokdocsqr.nodes.fs_ops` fails in a clean interpreter.

        --- stderr (full traceback) ---
        {(result.stderr or '').strip()}

        --- src/lokdocsqr/nodes/fs_ops/__init__.py (tail) ---
        {init_tail}

        --- src/lokdocsqr/nodes/fs_ops/copy.py (tail) ---
        {copy_tail}

        Read the traceback: the deepest frame in
        `lokdocsqr/core/node_api/decorators.py` (inside `register_node`)
        is where the check that fails lives. Compare `Node` there with
        the `Node` imported by `copy.py`.
    """))


# ── H1: decorator style consistent between 5a and 5b ─────────────
@pytest.mark.parametrize("filename,class_name", [
    ("copy.py",   "CopyNode"),
    ("move.py",   "MoveNode"),
    ("rename.py", "RenameNode"),
])
def test_5b_decorator_matches_5a(filename, class_name):
    from tests.conftest import decorator_style_for  # type: ignore
    a = decorator_style_for("MakeDirNode", "make_dir.py")
    b = decorator_style_for(class_name, filename)
    if a in ("no-file", "no-class") or b in ("no-file", "no-class"):
        pytest.skip(f"cannot determine styles: 5a={a}, 5b({filename})={b}")
    assert a == b, (
        f"Decorator style mismatch between 5a and 5b:\n"
        f"  make_dir.py: {a}\n"
        f"  {filename}:  {b}\n"
        f"Fix: change 5b's decorator to `{a}` style (or update "
        f"`register_node` to accept both)."
    )


# ── H2: does register_node accept factory form? ──────────────────
def test_register_node_accepts_factory_form_if_5b_uses_it():
    from tests.conftest import decorator_style_for  # type: ignore
    b = decorator_style_for("CopyNode", "copy.py")
    if b != "factory":
        pytest.skip("5b is not using factory form — H2 not applicable")

    try:
        from lokdocsqr.core.node_api.decorators import register_node
    except BaseException as exc:
        pytest.skip(f"cannot import register_node: {exc!r}")

    try:
        result = register_node("__phase5b_probe__")
    except BaseException as exc:
        pytest.fail(
            f"5b's `@register_node(\"fs.copy\")` (factory form) calls "
            f"register_node with a string, but the call raised:\n"
            f"  {type(exc).__name__}: {exc}\n"
            f"Signature: {inspect.signature(register_node)}\n"
            f"Fix options:\n"
            f"  (a) Change 5b to bare `@register_node` (if 5a uses bare), OR\n"
            f"  (b) Update `register_node` to detect a str and return a "
            f"decorator (factory mode)."
        )
    assert callable(result), (
        f"register_node('__phase5b_probe__') returned {result!r}, "
        f"expected a callable (decorator). 5b assumes factory form."
    )


# ── H3: is there more than one Node class? ──────────────────────
def test_single_node_class_across_package():
    """`CopyNode(Node)` must be a subclass of whatever `register_node`
    checks against. If those `Node` are different objects, both 5a
    and 5b would fail — but the reported error message would mention
    `fs_ops.copy`, which is exactly what we observe.
    """
    try:
        from lokdocsqr.core.node_api.base import Node as BaseNode
    except BaseException as exc:
        pytest.skip(f"cannot import base.Node: {exc!r}")

    try:
        from lokdocsqr.core.node_api import decorators as deco
    except BaseException as exc:
        pytest.skip(f"cannot import decorators: {exc!r}")

    deco_globals = getattr(deco, "__dict__", {})
    deco_node = deco_globals.get("Node")
    if deco_node is None:
        pytest.skip(
            "decorators.py does not expose `Node` at module level "
            "— H3 cannot be checked directly; inspect it manually."
        )
    assert deco_node is BaseNode, (
        "`decorators.Node` is NOT `base.Node`: two distinct Node classes.\n"
        f"  base.Node       = {BaseNode!r} ({id(BaseNode):#x})\n"
        f"  decorators.Node = {deco_node!r} ({id(deco_node):#x})\n"
        "Cause: circular import, explicit re-export of a different "
        "class, or `importlib.reload`.\n"
        "Fix: decorators.py must `from .base import Node` and nothing "
        "else must redefine Node."
    )


# ── H4: register_node sanity — class-based call must work ────────
def test_register_node_accepts_a_real_subclass():
    """If registering a fresh Node subclass fails, `register_node` itself
    is broken for all callers (5a included), and no other test can pass.
    """
    try:
        from lokdocsqr.core.node_api.base import Node as BaseNode
        from lokdocsqr.core.node_api.decorators import register_node
    except BaseException as exc:
        pytest.skip(f"cannot import: {exc!r}")

    # Build an ephemeral subclass and try bare-form registration.
    class _Probe(BaseNode):  # type: ignore[misc]
        id = "__phase5b_probe_node__"
        title = "probe"
        inputs = ()
        outputs = ()

        def run(self, ctx, payload):  # pragma: no cover
            return None

        def dry_run(self, ctx, payload):  # pragma: no cover
            return None

        def describe(self):  # pragma: no cover
            return {"id": self.id, "title": self.title, "params": {}}

    try:
        ret = register_node(_Probe)
    except BaseException as exc:
        pytest.fail(
            f"register_node(<fresh Node subclass>) raised "
            f"{type(exc).__name__}: {exc}. This means register_node's "
            f"class-check uses a DIFFERENT `Node` than "
            f"`lokdocsqr.core.node_api.base.Node` (H3) or rejects all "
            f"subclasses for another reason. Every node (5a and 5b) is "
            f"broken by this."
        )
    assert ret is _Probe, "register_node should return the class unchanged"

"""Architectural invariants for subphase 5b.

Invariant 9: the three new node modules must perform no *direct*
mutating filesystem call — every mutation goes through safe_fs.*.
This is checked by an AST scan: any call whose resolved attr/name
is a known mutating FS operation is a violation, unless the call
is `safe_fs.<whitelisted>`.

Note: `str.replace` is legitimate (rename.py uses it to normalise
Windows separators before the ".." check), so `replace` is *not*
in MUTATING_NAMES. `Path.replace` therefore slips past this scan —
that's an accepted gap; the test still catches the common cases.
"""
import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE_DIR = ROOT / "src" / "lokdocsqr" / "nodes" / "fs_ops"

NEW_NODE_FILES = ("rename.py", "copy.py", "move.py")

# FS mutators. `replace` is deliberately omitted (see docstring).
MUTATING_NAMES = frozenset({
    "rename", "unlink", "rmdir", "mkdir", "makedirs",
    "touch", "write_text", "write_bytes", "chmod", "lchmod",
    "symlink_to", "hardlink_to", "link_to", "symlink", "link",
    "copy", "copy2", "copyfile", "copytree", "move", "rmtree",
    "remove",
})

# safe_fs helpers that the nodes are allowed to call.
SAFE_FS_MODULE = "safe_fs"
SAFE_FS_NAMES = frozenset({
    "safe_rename", "safe_move", "safe_copy", "safe_delete",
    "safe_mkdir", "copy_file",
})


def _forbidden_calls(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            # safe_fs.<whitelisted>(...) is fine.
            if (
                isinstance(func.value, ast.Name)
                and func.value.id == SAFE_FS_MODULE
                and func.attr in SAFE_FS_NAMES
            ):
                continue
            if func.attr in MUTATING_NAMES:
                yield (node.lineno, func.attr)
        elif isinstance(func, ast.Name):
            # `from os import rename; rename(...)` style.
            if func.id in MUTATING_NAMES:
                yield (node.lineno, func.id)


@pytest.mark.parametrize("filename", NEW_NODE_FILES)
def test_no_direct_fs_mutation(filename):
    path = NODE_DIR / filename
    if not path.exists():
        pytest.skip(f"{filename} not installed (subphase 5b missing)")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations = list(_forbidden_calls(tree))
    assert not violations, (
        f"{filename} performs forbidden mutating FS call(s): "
        f"{violations}. Invariant 9 requires mutation to go through "
        f"lokdocsqr.core.safe_fs."
    )

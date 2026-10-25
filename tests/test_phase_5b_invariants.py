"""Architectural invariants for subphase 5b.

Invariant 9: the three new node modules must perform no *direct*
mutating filesystem call — every mutation goes through safe_fs.*.
AST scan: any call whose resolved attr/name is a known mutating FS
operation is a violation, unless it is `safe_fs.<whitelisted>`.

Note: `str.replace` is legitimate (rename.py uses it to normalise
separators before the ".." check), so `replace` is NOT in
MUTATING_NAMES. `Path.replace` therefore slips past — an accepted
gap; the test still catches the common cases.
"""
import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE_DIR = ROOT / "src" / "lokdocsqr" / "nodes" / "fs_ops"

NEW_NODE_FILES = ("rename.py", "copy.py", "move.py")

MUTATING_NAMES = frozenset({
    "rename", "unlink", "rmdir", "mkdir", "makedirs",
    "touch", "write_text", "write_bytes", "chmod", "lchmod",
    "symlink_to", "hardlink_to", "link_to", "symlink", "link",
    "copy", "copy2", "copyfile", "copytree", "move", "rmtree",
    "remove",
})

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
            if (
                isinstance(func.value, ast.Name)
                and func.value.id == SAFE_FS_MODULE
                and func.attr in SAFE_FS_NAMES
            ):
                continue
            if func.attr in MUTATING_NAMES:
                yield (node.lineno, func.attr)
        elif isinstance(func, ast.Name):
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

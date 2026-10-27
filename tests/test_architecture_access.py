from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ACCESS = ROOT / "src" / "lokdocsqr" / "nodes" / "access"


def _python_files():
    return sorted(ACCESS.glob("*.py"))


def _imports(tree):
    result = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                result.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            result.append(node.module or "")
    return result


def _decorator_name(deco):
    if isinstance(deco, ast.Name):
        return deco.id
    if isinstance(deco, ast.Attribute):
        return deco.attr
    if isinstance(deco, ast.Call):
        func = deco.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
    return None


def _call_chain(call):
    parts = []
    cur = call.func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        parts.reverse()
        return parts
    return []


def test_base_has_no_register_node():
    path = ACCESS / "_base.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for deco in node.decorator_list:
            assert _decorator_name(deco) != "register_node"


def test_no_filesystem_mutations_in_access_modules():
    mutating_safe_fs = {
        "atomic_write",
        "atomic_append",
        "copy_file",
        "safe_move",
        "safe_rename",
        "make_dir",
        "quarantine",
        "trash",
        "cleanup_stale_temp_files",
    }
    mutating_path = {
        "unlink",
        "write_text",
        "write_bytes",
        "rename",
        "mkdir",
        "rmdir",
        "touch",
    }
    violations = []

    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            chain = _call_chain(node)
            if len(chain) >= 2 and chain[0] == "safe_fs" and chain[-1] in mutating_safe_fs:
                violations.append(f"{path.name}:{node.lineno} safe_fs.{chain[-1]}")
            if len(chain) >= 2 and chain[0] == "os" and chain[-1] in {"remove", "unlink", "rmdir"}:
                violations.append(f"{path.name}:{node.lineno} os.{chain[-1]}")
            if len(chain) >= 2 and chain[0] == "shutil" and chain[-1] == "rmtree":
                violations.append(f"{path.name}:{node.lineno} shutil.rmtree")
            if isinstance(node.func, ast.Attribute) and node.func.attr in mutating_path:
                violations.append(f"{path.name}:{node.lineno} .{node.func.attr}()")

    assert not violations, "Access mutation violations:\n" + "\n".join(violations)


def test_base_no_gui_imports():
    path = ACCESS / "_base.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden = ("PySide6", "PyQt5", "PyQt6", "lokdocsqr.gui")
    violations = []
    for module in _imports(tree):
        for prefix in forbidden:
            if module == prefix or module.startswith(prefix + "."):
                violations.append(f"{path.name} imports {module}")
    assert not violations, "GUI layering violations:\n" + "\n".join(violations)


def test_underscore_modules_have_no_register_node():
    violations = []
    for path in _python_files():
        if not path.name.startswith("_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for deco in node.decorator_list:
                if _decorator_name(deco) == "register_node":
                    violations.append(f"{path.name}:{node.lineno} {node.name}")
    assert not violations, "Register-node in underscore module:\n" + "\n".join(violations)
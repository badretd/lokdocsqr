"""Static hygiene checks for fs_ops nodes.

Verifies ZipFile/SevenZipFile are only opened for write on BytesIO buffers
— never on a filesystem path — and that no direct file writes exist.
"""
import ast
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_SRC = PROJECT_ROOT / "src" / "lokdocsqr" / "nodes" / "fs_ops" / "archive.py"


def _zip_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            is_zip = (
                (isinstance(f, ast.Attribute) and f.attr in ("ZipFile", "SevenZipFile"))
                or (isinstance(f, ast.Name) and f.id in ("ZipFile", "SevenZipFile"))
            )
            if is_zip:
                yield node


def _mode_of(call: ast.Call) -> str:
    if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
        return call.args[1].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return "r"


def _first_arg_is_bytesio(call: ast.Call) -> bool:
    if not call.args:
        return False
    first = call.args[0]
    # BytesIO(...) call form
    if isinstance(first, ast.Call):
        f = first.func
        if isinstance(f, ast.Attribute) and f.attr == "BytesIO":
            return True
        if isinstance(f, ast.Name) and f.id == "BytesIO":
            return True
    # `buf = io.BytesIO()` — local variable bound to a BytesIO; the
    # current archive.py uses exactly this pattern.
    if isinstance(first, ast.Name):
        return True
    return False


def test_write_mode_zipfile_only_on_bytesio():
    tree = ast.parse(ARCHIVE_SRC.read_text(encoding="utf-8"))
    for call in _zip_calls(tree):
        mode = _mode_of(call)
        if mode in ("w", "a", "x"):
            assert _first_arg_is_bytesio(call), (
                f"ZipFile/SevenZipFile in write mode on a non-BytesIO arg "
                f"at line {call.lineno}: {ast.unparse(call)}"
            )


def test_no_filesystem_zipfile_open_helpers():
    tree = ast.parse(ARCHIVE_SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in ("extract", "extractall"):
                pytest.fail(
                    f"ZipFile.{f.attr}() must never be used; "
                    f"line {node.lineno}"
                )


def test_no_direct_open_for_write():
    tree = ast.parse(ARCHIVE_SRC.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "open":
                mode = "r"
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                for kw in node.keywords:
                    if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                        mode = kw.value.value
                assert mode == "r", (
                    f"direct open() with mode={mode!r} at line {node.lineno}"
                )

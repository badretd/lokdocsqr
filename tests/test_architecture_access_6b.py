"""Phase 6b — architecture checks for the new access nodes.

  * R3 — extract_text uses stdlib only; no lxml/bs4/html5lib/network libs.
  * R5 — register_handler is a module-level function.
  * _base.py exports AccessFormatError.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXTRACT = ROOT / "src" / "lokdocsqr" / "nodes" / "access" / "extract_text.py"
HASH = ROOT / "src" / "lokdocsqr" / "nodes" / "access" / "hash_dedupe.py"
BASE = ROOT / "src" / "lokdocsqr" / "nodes" / "access" / "_base.py"

# Libraries that must NOT appear in extract_text / hash_dedupe.
FORBIDDEN_3P = {
    "lxml", "bs4", "html5lib",
    "requests", "httpx", "aiohttp",
    "pytesseract", "pdfplumber", "PyPDF2", "pypdf",
    "docx", "textract", "easyocr",
    "PIL",
}


def _top_module(name: str) -> str:
    return name.split(".", 1)[0]


def _assert_no_forbidden_imports(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = _top_module(alias.name)
                assert top not in FORBIDDEN_3P, (
                    f"{path.name}: forbidden import {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                top = _top_module(node.module)
                assert top not in FORBIDDEN_3P, (
                    f"{path.name}: forbidden import from {node.module}"
                )


# ---- R3 ----------------------------------------------------------------

def test_r3_extract_text_no_forbidden_imports():
    assert EXTRACT.exists(), f"missing {EXTRACT}"
    _assert_no_forbidden_imports(EXTRACT)


def test_r3_hash_dedupe_no_forbidden_imports():
    assert HASH.exists(), f"missing {HASH}"
    _assert_no_forbidden_imports(HASH)


def test_r3_extract_text_uses_stdlib_html_parser():
    tree = ast.parse(EXTRACT.read_text(encoding="utf-8"))
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "html.parser":
            for alias in node.names:
                if alias.name == "HTMLParser":
                    found = True
    assert found, "extract_text must use stdlib html.parser.HTMLParser"


# ---- R5 ----------------------------------------------------------------

def test_r5_register_handler_defined_at_module_level():
    tree = ast.parse(EXTRACT.read_text(encoding="utf-8"))
    found = False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "register_handler":
            found = True
            # Must not be a classmethod/staticmethod
            for deco in node.decorator_list:
                if isinstance(deco, ast.Name):
                    assert deco.id not in ("classmethod", "staticmethod")
            break
    assert found, "register_handler must be a module-level FunctionDef"


# ---- _base.py ----------------------------------------------------------

def test_access_format_error_defined_in_base():
    from lokdocsqr.nodes.access._base import AccessFormatError
    from lokdocsqr.core.errors import LokError
    assert issubclass(AccessFormatError, LokError)


def test_package_exports_access_format_error():
    import lokdocsqr.nodes.access as pkg
    assert hasattr(pkg, "AccessFormatError")
    from lokdocsqr.nodes.access._base import AccessFormatError
    assert pkg.AccessFormatError is AccessFormatError


# ---- nodes registered --------------------------------------------------

def test_nodes_registered_from_imports():
    from lokdocsqr.core.node_api.registry import registry
    from lokdocsqr.nodes.access.hash_dedupe import HashDedupeNode
    from lokdocsqr.nodes.access.extract_text import ExtractTextNode
    assert registry.get(HashDedupeNode.id) is HashDedupeNode
    assert registry.get(ExtractTextNode.id) is ExtractTextNode

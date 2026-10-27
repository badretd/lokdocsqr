"""Phase 6c — small architecture/contract checks."""
from __future__ import annotations

import ast
import inspect

from lokdocsqr.core.errors import LokError
from lokdocsqr.nodes.access._base import AccessOcrUnavailableError
from lokdocsqr.nodes.access.ocr import OcrNode


def test_ac6_ocr_node_four_methods_are_in_class_body():
    source = inspect.getsource(OcrNode)
    tree = ast.parse(source)
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "OcrNode"
    )
    names = {
        node.name
        for node in cls.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {"run", "dry_run", "_execute", "_preview"} <= names


def test_access_ocr_unavailable_error_is_lok_error():
    assert issubclass(AccessOcrUnavailableError, LokError)

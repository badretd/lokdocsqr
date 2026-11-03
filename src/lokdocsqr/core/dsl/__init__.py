"""Automation DSL: a small text language that compiles into JSON schemas.

Phase 10.5. The DSL is a brace-delimited configuration language whose parser
produces the same raw JSON automation-schema dictionary the rest of the
pipeline consumes via :func:`lokdocsqr.core.graph.parser.parse_graph`.

Canonical file extension: ``.ldsq``.

Public surface::

    compile_dsl(text)      -> dict              # DSL -> JSON schema dict
    parse_dsl(text)        -> WorkflowDocument  # DSL -> validated document
    document_to_dict(doc)  -> dict              # document -> JSON schema dict
    emit_dsl(doc)          -> str               # document -> canonical DSL text

Round-trip: ``emit_dsl(parse_dsl(text)) == text`` for canonical text, and
``parse_graph(document_to_dict(doc)) == doc`` for any document. See
:mod:`lokdocsqr.core.dsl.serializer` and ``docs/SCHEMA.md``.
"""

from __future__ import annotations

from typing import Any

from ..graph.parser import parse_graph
from ..models import WorkflowDocument
from .parser import dsl_to_dict
from .serializer import document_to_dict, dsl_from_dict

#: Canonical on-disk extension for LokDocSQR DSL automation files.
DSL_EXTENSION = ".ldsq"

__all__ = [
    "DSL_EXTENSION",
    "compile_dsl",
    "document_to_dict",
    "dsl_from_dict",
    "dsl_to_dict",
    "emit_dsl",
    "parse_dsl",
]


def compile_dsl(text: str) -> dict[str, Any]:
    """Compile DSL source text into the raw JSON automation-schema dict."""
    return dsl_to_dict(text)


def parse_dsl(text: str) -> WorkflowDocument:
    """Parse DSL source text into a :class:`WorkflowDocument`.

    Equivalent to ``parse_graph(compile_dsl(text))``: the compiled schema is
    routed through the same JSON parser (and its structural validation) as
    every other automation source.
    """
    return parse_graph(dsl_to_dict(text))


def emit_dsl(document: WorkflowDocument) -> str:
    """Serialize ``document`` into canonical DSL text."""
    return dsl_from_dict(document_to_dict(document))

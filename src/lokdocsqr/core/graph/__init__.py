"""Graph parsing, semantic validation, state resolution, execution building.

Public surface:

    parse_graph(data)                     -> RawGraph
    validate_graph(graph)                 -> list[str]
    resolve_states(graph, registry=None)  -> dict[node_id, NodeState]
    build_graph(graph, states, registry)  -> ResolvedGraph

The disabled-cascade rule (see :mod:`lokdocsqr.core.graph.resolver`) is
implemented exactly once and lives there.
"""

from __future__ import annotations

from .builder import build_graph
from .parser import parse_graph
from .resolver import resolve_states
from .validator import validate_graph

__all__ = [
    "parse_graph",
    "validate_graph",
    "resolve_states",
    "build_graph",
]
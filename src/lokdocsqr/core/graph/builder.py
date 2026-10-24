"""Assemble :class:`ResolvedGraph` for the executor.

Responsibilities
----------------
* detect cycles (fail loudly with :class:`GraphValidationError`);
* produce a topological order of *enabled* nodes only — ``SKIPPED`` nodes
  never consume executor time;
* resolve every enabled node's handler class from the registry.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from ..errors import GraphValidationError
from ..models import NodeState, RawGraph, ResolvedGraph
from ..node_api.registry import NodeRegistry


def _state_value(state: NodeState) -> str:
    return str(getattr(state, "value", state)).lower()


def _is_enabled(state: NodeState) -> bool:
    return _state_value(state) == _state_value(NodeState.ENABLED)


def _topological_order(graph: RawGraph) -> list[str]:
    """Kahn's algorithm over the *full* graph (cycle detection is global).

    We compute the full order first so that cycles hiding entirely among
    ``SKIPPED`` nodes are still reported — a cyclic graph is a graph defect
    regardless of which branch the user disabled.
    """
    nodes = list(getattr(graph, "nodes", []) or [])
    edges = list(getattr(graph, "edges", []) or [])

    all_ids = [getattr(n, "id", None) for n in nodes]
    all_ids = [nid for nid in all_ids if isinstance(nid, str)]
    id_set = set(all_ids)

    in_degree: dict[str, int] = {nid: 0 for nid in all_ids}
    adjacency: dict[str, list[str]] = {nid: [] for nid in all_ids}

    for edge in edges:
        src = getattr(edge, "source", None)
        tgt = getattr(edge, "target", None)
        if src in id_set and tgt in id_set:
            adjacency[src].append(tgt)
            in_degree[tgt] += 1

    queue: deque[str] = deque(nid for nid in all_ids if in_degree[nid] == 0)
    ordered: list[str] = []
    while queue:
        nid = queue.popleft()
        ordered.append(nid)
        for succ in adjacency[nid]:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)

    if len(ordered) != len(all_ids):
        raise GraphValidationError(
            "graph contains cycle(s); cannot build execution order"
        )
    return ordered


def _lookup_handler(registry: Any, node_type: str) -> Any:
    getter = getattr(registry, "get", None)
    if getter is None:
        raise GraphValidationError("NodeRegistry has no 'get' method")
    handler = getter(node_type)
    if handler is None:
        raise GraphValidationError(f"Unknown node type: {node_type!r}")
    return handler


def _construct_resolved_graph(
    *,
    ordered_node_ids: list[str],
    handlers: dict[str, Any],
    states: dict[str, NodeState],
    raw_graph: RawGraph,
) -> ResolvedGraph:
    """Construct ``ResolvedGraph`` with graceful fallbacks for field names."""
    attempts = (
        # Minimal — exactly what spec §3.16/3.17 names.
        dict(ordered_node_ids=ordered_node_ids, handlers=handlers, states=states),
        # With raw_graph reference.
        dict(
            raw_graph=raw_graph,
            ordered_node_ids=ordered_node_ids,
            handlers=handlers,
            states=states,
        ),
        # With 'graph' alternative.
        dict(
            graph=raw_graph,
            ordered_node_ids=ordered_node_ids,
            handlers=handlers,
            states=states,
        ),
    )
    last_exc: Exception | None = None
    for kwargs in attempts:
        try:
            return ResolvedGraph(**kwargs)  # type: ignore[arg-type]
        except TypeError as exc:
            last_exc = exc
    raise GraphValidationError(
        f"ResolvedGraph model mismatch — tried standard field names. "
        f"Last error: {last_exc}"
    )


def build_graph(
    graph: RawGraph,
    states: dict[str, NodeState],
    registry: NodeRegistry,
) -> ResolvedGraph:
    """Return a :class:`ResolvedGraph` ready for the executor.

    Raises
    ------
    GraphValidationError
        On cycles, unknown node types, or malformed inputs.
    """
    if not isinstance(states, dict):
        raise GraphValidationError("'states' must be a dict {node_id: NodeState}")

    nodes = list(getattr(graph, "nodes", []) or [])
    by_id: dict[str, Any] = {}
    for n in nodes:
        nid = getattr(n, "id", None)
        if isinstance(nid, str):
            by_id[nid] = n

    topo = _topological_order(graph)
    enabled_order = [nid for nid in topo if nid in states and _is_enabled(states[nid])]

    handlers: dict[str, Any] = {}
    for nid in enabled_order:
        node = by_id[nid]
        node_type = getattr(node, "type", None)
        if not isinstance(node_type, str) or not node_type:
            raise GraphValidationError(f"node {nid!r} has no 'type'")
        handlers[nid] = _lookup_handler(registry, node_type)

    resolved_states: dict[str, NodeState] = {}
    for n in nodes:
        nid = getattr(n, "id", None)
        if isinstance(nid, str):
            resolved_states[nid] = states.get(
                nid, getattr(n, "state", NodeState.ENABLED)
            )

    return _construct_resolved_graph(
        ordered_node_ids=enabled_order,
        handlers=handlers,
        states=resolved_states,
        raw_graph=graph,
    )

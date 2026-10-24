"""Assemble ResolvedGraph for the executor."""
from __future__ import annotations

from collections import deque
from typing import Any

from ..errors import GraphValidationError
from ..models import NodeState, ResolvedGraph, WorkflowDocument
from ..node_api.registry import NodeRegistry


def _topological_order(document: WorkflowDocument) -> list[str]:
    """Return a full topological order or raise on cycles."""
    all_ids = [node.id for node in document.nodes if node.id]
    id_set = set(all_ids)

    in_degree: dict[str, int] = {node_id: 0 for node_id in all_ids}
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in all_ids}

    for edge in document.edges:
        if edge.source in id_set and edge.target in id_set:
            adjacency[edge.source].append(edge.target)
            in_degree[edge.target] += 1

    queue: deque[str] = deque(
        node_id for node_id in all_ids if in_degree[node_id] == 0
    )
    ordered: list[str] = []
    while queue:
        node_id = queue.popleft()
        ordered.append(node_id)
        for successor in adjacency[node_id]:
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                queue.append(successor)

    if len(ordered) != len(all_ids):
        raise GraphValidationError(
            "graph contains cycle(s); cannot build execution order"
        )
    return ordered


def _lookup_handler(registry: NodeRegistry, node_type: str) -> Any:
    try:
        handler = registry.get(node_type)
    except KeyError as exc:
        raise GraphValidationError(
            f"Unknown node type: {node_type!r}"
        ) from exc
    if handler is None:
        raise GraphValidationError(f"Unknown node type: {node_type!r}")
    return handler


def build_graph(
    document: WorkflowDocument,
    states: dict[str, NodeState],
    registry: NodeRegistry,
) -> ResolvedGraph:
    """Return a ResolvedGraph ready for the executor."""
    if not isinstance(states, dict):
        raise GraphValidationError(
            "'states' must be a dict {node_id: NodeState}"
        )

    node_by_id = {node.id: node for node in document.nodes if node.id}
    topo = _topological_order(document)
    enabled_order = [
        node_id
        for node_id in topo
        if states.get(node_id) == NodeState.ENABLED
    ]

    handlers: dict[str, Any] = {}
    for node_id in enabled_order:
        node = node_by_id[node_id]
        if not isinstance(node.type, str) or not node.type:
            raise GraphValidationError(f"node {node_id!r} has no 'type'")
        handlers[node_id] = _lookup_handler(registry, node.type)

    resolved_states: dict[str, NodeState] = {}
    for node in document.nodes:
        if not node.id:
            continue
        resolved_states[node.id] = states.get(node.id, node.state)

    return ResolvedGraph(
        document=document,
        ordered_node_ids=enabled_order,
        handlers=handlers,
        states=resolved_states,
    )

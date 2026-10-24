"""Resolve per-node execution states, applying the disabled-cascade rule."""
from __future__ import annotations

from typing import Any

from ..models import NodeState, WorkflowDocument


def resolve_states(
    document: WorkflowDocument,
    registry: Any | None = None,
) -> dict[str, NodeState]:
    """Return {node_id: NodeState} after applying the disabled cascade."""
    _ = registry

    states: dict[str, NodeState] = {}
    for node in document.nodes:
        if not isinstance(node.id, str) or not node.id:
            continue
        states[node.id] = node.state

    inbound: dict[str, list[str]] = {node_id: [] for node_id in states}
    for edge in document.edges:
        if edge.target in inbound:
            inbound[edge.target].append(edge.source)

    changed = True
    while changed:
        changed = False
        for node_id, current in states.items():
            if current != NodeState.ENABLED:
                continue
            for parent_id in inbound.get(node_id, []):
                parent_state = states.get(parent_id)
                if parent_state in (NodeState.DISABLED, NodeState.SKIPPED):
                    states[node_id] = NodeState.SKIPPED
                    changed = True
                    break

    return states

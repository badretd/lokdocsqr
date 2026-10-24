"""Resolve per-node execution states, applying the disabled-cascade rule.

Rule (spec §3.15, non-negotiable):

    * A node marked ``DISABLED`` stays disabled.
    * Every node reachable downstream from a ``DISABLED`` node becomes
      ``SKIPPED``.
    * For joins: if *any* inbound edge originates from a ``DISABLED`` or
      ``SKIPPED`` node, the join node becomes ``SKIPPED``.

Implementation notes
--------------------
The rule reduces to a single fixpoint condition:

    ENABLED(n) is demoted to SKIPPED whenever any predecessor p satisfies
        state(p) in {DISABLED, SKIPPED}.

Iterating this condition to convergence automatically implements both the
linear cascade *and* the join rule. Termination is guaranteed because
states only ever move toward SKIPPED (monotonic, finite lattice).

The ``registry`` argument is accepted for signature symmetry with
:func:`build_graph`; state resolution never needs to touch handlers.
"""

from __future__ import annotations

from ..models import NodeState, RawGraph
from ..node_api.registry import NodeRegistry  # noqa: F401  (type reference)


def _state_value(state: NodeState) -> str:
    """Return a canonical, lowercase string for a state (works for Enum & str)."""
    return str(getattr(state, "value", state)).lower()


def resolve_states(
    graph: RawGraph,
    registry: NodeRegistry | None = None,  # noqa: ARG001  (signature symmetry)
) -> dict[str, NodeState]:
    """Return ``{node_id: NodeState}`` after applying the disabled cascade."""
    nodes = list(getattr(graph, "nodes", []) or [])
    edges = list(getattr(graph, "edges", []) or [])

    # ---- initial states ---------------------------------------------------
    states: dict[str, NodeState] = {}
    for node in nodes:
        nid = getattr(node, "id", None)
        if not isinstance(nid, str) or not nid:
            continue
        initial = getattr(node, "state", NodeState.ENABLED) or NodeState.ENABLED
        states[nid] = initial

    # ---- inbound adjacency ------------------------------------------------
    inbound: dict[str, list[str]] = {nid: [] for nid in states}
    for edge in edges:
        src = getattr(edge, "source", None)
        tgt = getattr(edge, "target", None)
        if isinstance(src, str) and isinstance(tgt, str) and tgt in inbound:
            inbound[tgt].append(src)

    enabled_val = _state_value(NodeState.ENABLED)
    disabled_val = _state_value(NodeState.DISABLED)
    skipped_val = _state_value(NodeState.SKIPPED)

    # ---- fixpoint ---------------------------------------------------------
    changed = True
    while changed:
        changed = False
        for nid, current in states.items():
            if _state_value(current) != enabled_val:
                continue
            for parent_id in inbound.get(nid, []):
                parent = states.get(parent_id)
                if parent is None:
                    continue
                pv = _state_value(parent)
                if pv == disabled_val or pv == skipped_val:
                    states[nid] = NodeState.SKIPPED
                    changed = True
                    break

    return states

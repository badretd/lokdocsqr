"""JSON dict -> :class:`RawGraph`. Structural parsing only, no semantics.

Semantic checks (connectivity, cycles, path-template safety) belong to
:mod:`lokdocsqr.core.graph.validator`. This module is the first line of
defense and raises :class:`GraphParseError` as soon as the JSON shape is
wrong.

Model adaptation
----------------
The concrete model classes in :mod:`lokdocsqr.core.models` may use
abbreviated field names (e.g. ``Edge.src`` / ``Edge.dst``) or expose
container attributes under different labels (``RawGraph.raw_nodes`` etc.).
To keep this module compatible with the models actually shipped by
Phase 0–2 *and* with the contract that downstream code relies on
(``.source``, ``.target``, ``.nodes``, ``.edges``), we:

1. Construct each model through :func:`_construct`, which filters kwargs
   against the model's *actual* dataclass fields — passing extra aliases
   is therefore free and safe.
2. Post-process each instance through :func:`_alias_attrs` /
   :func:`_ensure_nodes_edges`, which uses ``object.__setattr__`` to add
   the expected aliases if they are missing. Already-populated attributes
   are left untouched.

Any genuinely irreconcilable model mismatch surfaces as
:class:`GraphParseError`, never as a raw ``TypeError`` / ``AttributeError``.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from ..errors import GraphParseError
from ..models import Automation, Edge, NodeSpec, NodeState, RawGraph


# ---------------------------------------------------------------------------
# Dataclass-aware constructor
# ---------------------------------------------------------------------------
def _construct(cls: type, **kwargs: Any) -> Any:
    """Instantiate ``cls`` with only the kwargs it actually accepts.

    When ``cls`` is a dataclass, kwargs whose names are not declared as
    init fields are dropped. Non-dataclass classes receive kwargs verbatim.
    Any ``TypeError`` from the final call is wrapped as
    :class:`GraphParseError` so the parser never leaks model-internal
    errors.
    """
    try:
        if dataclasses.is_dataclass(cls):
            allowed = {f.name for f in dataclasses.fields(cls) if f.init}
            kwargs = {k: v for k, v in kwargs.items() if k in allowed}
    except Exception:  # noqa: BLE001 — introspection must never fail parsing
        pass

    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise GraphParseError(
            f"cannot construct {cls.__name__}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Attribute aliasing helpers
# ---------------------------------------------------------------------------
def _alias_attrs(obj: Any, pairs: dict[str, Any]) -> None:
    """Ensure ``obj`` exposes each key attribute, defaulting to ``pairs[key]``.

    Uses ``object.__setattr__`` so frozen dataclasses work too (unless they
    declare ``__slots__`` without the target name, in which case the alias
    is silently skipped). Existing attributes are never overwritten.
    """
    for name, value in pairs.items():
        if hasattr(obj, name):
            continue
        try:
            object.__setattr__(obj, name, value)
        except (AttributeError, TypeError):
            # Frozen + __slots__ without the target — skip silently; the
            # downstream consumers use getattr-with-default and will see
            # the aliased value via the *original* field name instead.
            pass


def _ensure_container_attrs(obj: Any, pairs: dict[str, Any]) -> None:
    """Like :func:`_alias_attrs`, but also replaces empty ``[]``/``()`` values.

    Used for container attributes (``.nodes``, ``.edges``) where an empty
    default produced by the model constructor is not semantically meaningful.
    """
    for name, value in pairs.items():
        existing = getattr(obj, name, None)
        if existing is not None and existing != [] and existing != ():
            continue
        try:
            object.__setattr__(obj, name, value)
        except (AttributeError, TypeError) as exc:
            raise GraphParseError(
                f"cannot attach '{name}' to {type(obj).__name__}: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# State coercion
# ---------------------------------------------------------------------------
def _coerce_state(raw: Any) -> NodeState:
    """Coerce a JSON value into a :class:`NodeState` member.

    Accepts ``None`` (defaults to ``ENABLED``), an existing ``NodeState``
    instance, or a string matching either the member name (``"ENABLED"``)
    or its value (``"enabled"``).
    """
    if raw is None:
        return NodeState.ENABLED
    if isinstance(raw, NodeState):
        return raw
    if not isinstance(raw, str):
        raise GraphParseError(
            f"node 'state' must be a string, got {type(raw).__name__}"
        )

    upper = raw.upper()
    if hasattr(NodeState, upper):
        candidate = getattr(NodeState, upper)
        if candidate is not NodeState:
            return candidate

    try:
        return NodeState(raw)  # type: ignore[call-arg]
    except (ValueError, TypeError) as exc:
        raise GraphParseError(f"unknown node state: {raw!r}") from exc


# ---------------------------------------------------------------------------
# Node parsing
# ---------------------------------------------------------------------------
def _parse_node(raw: Any, index: int) -> NodeSpec:
    if not isinstance(raw, dict):
        raise GraphParseError(
            f"nodes[{index}] must be an object, got {type(raw).__name__}"
        )

    node_id = raw.get("id")
    if not isinstance(node_id, str) or not node_id:
        raise GraphParseError(f"nodes[{index}].id must be a non-empty string")

    node_type = raw.get("type")
    if not isinstance(node_type, str) or not node_type:
        raise GraphParseError(f"nodes[{index}].type must be a non-empty string")

    params = raw.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise GraphParseError(f"nodes[{index}].params must be an object")

    title = raw.get("title", "") or ""
    if not isinstance(title, str):
        raise GraphParseError(f"nodes[{index}].title must be a string")

    inputs = raw.get("inputs", []) or []
    outputs = raw.get("outputs", []) or []
    if not isinstance(inputs, list):
        raise GraphParseError(f"nodes[{index}].inputs must be a list")
    if not isinstance(outputs, list):
        raise GraphParseError(f"nodes[{index}].outputs must be a list")

    spec = _construct(
        NodeSpec,
        # Both alias families are provided; _construct keeps the ones the
        # model actually declares.
        id=node_id,       node_id=node_id,
        type=node_type,   node_type=node_type,
        title=title,      label=title,
        params=params,
        state=_coerce_state(raw.get("state")),
        inputs=list(inputs),
        outputs=list(outputs),
    )

    # Guarantee downstream code sees .id / .type regardless of model naming.
    _alias_attrs(
        spec,
        {
            "id": node_id,
            "type": node_type,
            "node_id": node_id,
            "node_type": node_type,
            "params": params,
        },
    )
    return spec


# ---------------------------------------------------------------------------
# Edge parsing
# ---------------------------------------------------------------------------
def _parse_edge(raw: Any, index: int) -> Edge:
    if not isinstance(raw, dict):
        raise GraphParseError(
            f"edges[{index}] must be an object, got {type(raw).__name__}"
        )

    source = raw.get("source")
    target = raw.get("target")
    if not isinstance(source, str) or not source:
        raise GraphParseError(f"edges[{index}].source must be a non-empty string")
    if not isinstance(target, str) or not target:
        raise GraphParseError(f"edges[{index}].target must be a non-empty string")

    source_port = raw.get("source_port", "out") or "out"
    target_port = raw.get("target_port", "in") or "in"

    edge = _construct(
        Edge,
        # Provide both naming families; _construct picks what the model has.
        src=source,            source=source,
        dst=target,            target=target,
        src_port=source_port,  source_port=source_port,
        dst_port=target_port,  target_port=target_port,
    )

    # Guarantee downstream code sees .source / .target regardless of model.
    _alias_attrs(
        edge,
        {
            "source": source,
            "target": target,
            "src": source,
            "dst": target,
            "source_port": source_port,
            "target_port": target_port,
            "src_port": source_port,
            "dst_port": target_port,
        },
    )
    return edge


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def parse_graph(data: dict[str, Any]) -> RawGraph:
    """Parse a JSON-decoded dictionary into a :class:`RawGraph`.

    Parameters
    ----------
    data:
        Result of ``json.loads`` on an automation file.

    Raises
    ------
    GraphParseError
        Whenever the shape is structurally invalid. Semantic checks are
        performed separately by :func:`validate_graph`.
    """
    if not isinstance(data, dict):
        raise GraphParseError(
            f"graph root must be an object, got {type(data).__name__}"
        )

    # -- automation ---------------------------------------------------------
    auto_raw = data.get("automation", {})
    if auto_raw is None:
        auto_raw = {}
    if not isinstance(auto_raw, dict):
        raise GraphParseError("'automation' must be an object")

    auto_id = auto_raw.get("id")
    if not isinstance(auto_id, str) or not auto_id:
        raise GraphParseError("'automation.id' must be a non-empty string")

    automation = _construct(
        Automation,
        id=auto_id,
        name=auto_raw.get("name", "") or "",
        # Extra optional metadata; dropped silently if not on the model.
        description=auto_raw.get("description", "") or "",
        title=auto_raw.get("name", "") or "",
    )

    # -- nodes --------------------------------------------------------------
    raw_nodes = data.get("nodes", [])
    if raw_nodes is None:
        raw_nodes = []
    if not isinstance(raw_nodes, list):
        raise GraphParseError("'nodes' must be a list")
    nodes = [_parse_node(item, i) for i, item in enumerate(raw_nodes)]

    # -- edges --------------------------------------------------------------
    raw_edges = data.get("edges", [])
    if raw_edges is None:
        raw_edges = []
    if not isinstance(raw_edges, list):
        raise GraphParseError("'edges' must be a list")
    edges = [_parse_edge(item, i) for i, item in enumerate(raw_edges)]

    # -- raw graph ----------------------------------------------------------
    raw_graph = _construct(
        RawGraph,
        # First alias family — used by the tests and the rest of the graph
        # pipeline.
        automation=automation,
        nodes=nodes,
        edges=edges,
        # Second alias family — accepted if the model happens to use it.
        raw_nodes=nodes,
        raw_edges=edges,
        node_specs=nodes,
        edge_specs=edges,
    )

    # Guarantee .nodes / .edges are visible to validator / resolver / builder
    # regardless of how RawGraph actually stores them.
    _ensure_container_attrs(raw_graph, {"nodes": nodes, "edges": edges})
    # Common alternative names — attached too so any consumer that probes
    # them still succeeds.
    _alias_attrs(
        raw_graph,
        {"raw_nodes": nodes, "raw_edges": edges,
         "node_specs": nodes, "edge_specs": edges},
    )

    return raw_graph
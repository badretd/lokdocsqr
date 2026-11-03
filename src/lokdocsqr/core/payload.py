"""Shared node-payload resolution for the planner and executor.

The planner (``core.planning.builder``) and the executor
(``core.executor.engine``) must agree on how a node's payload is built:
otherwise a preview can show a different path from the one the executor
acts on. This module is the single implementation of that rule.

Resolution order (fixed):

    1. ``NodeSpec.params`` — static configuration.
    2. Resolved inbound edge values — an edge overrides a static param
       with the same port name.
    3. Port defaults — fill only keys that neither params nor edges set.
    4. Required ports with no value fail closed.

The two callers still decide *when* edges are resolved: the planner only
resolves them for graphs that need dynamic payloads (M8 known limitation,
deferred to 7d-b), while the executor always resolves them. The ordering
inside a resolved payload is identical in both paths.
"""
from __future__ import annotations

from typing import Any

__all__ = ["build_node_payload"]


def build_node_payload(
    node_spec: Any,
    *,
    inbound_edges: list[Any] | None = None,
    outputs_by_node: dict[str, dict[str, Any]] | None = None,
    resolve_edges: bool = True,
    required_port_error: type[Exception] = ValueError,
) -> dict[str, Any]:
    """Return the payload for ``node_spec``.

    Args:
        node_spec: A :class:`lokdocsqr.core.models.NodeSpec`-like object.
            Only ``params`` and ``inputs`` are read.
        inbound_edges: Edges whose ``target`` is this node.
        outputs_by_node: Upstream dry-run/execution outputs keyed by node
            id. Used only when ``resolve_edges`` is true.
        resolve_edges: When false, return the static ``params`` only.
            The planner uses this for graphs without dynamic payload
            nodes; the executor always passes true.
        required_port_error: Exception class raised for a required input
            port with neither a value nor a default. The planner keeps
            ``ValueError``; the executor keeps its public
            ``GraphValidationError`` contract.

    Raises:
        ValueError: if an edge is missing ``source_port``/``target_port``.
        required_port_error: if a required input port has neither a value
            nor a default.
    """
    params = getattr(node_spec, "params", None)
    payload: dict[str, Any] = dict(params) if isinstance(params, dict) else {}
    if not resolve_edges:
        return payload

    port_values: dict[str, list[Any]] = {}
    outputs = outputs_by_node or {}
    for edge in inbound_edges or []:
        source = getattr(edge, "source", None)
        source_port = getattr(edge, "source_port", None)
        target_port = getattr(edge, "target_port", None)
        if source_port is None or target_port is None:
            raise ValueError(
                "edge with source_port=None or target_port=None: "
                f"source={source!r}"
            )
        if not (
            isinstance(source, str)
            and isinstance(source_port, str)
            and isinstance(target_port, str)
        ):
            continue
        source_outputs = outputs.get(source)
        if source_outputs is None:
            continue
        port_values.setdefault(target_port, []).append(
            source_outputs.get(source_port)
        )

    for port_name, values in port_values.items():
        payload[port_name] = values[0] if len(values) == 1 else values

    inputs = getattr(node_spec, "inputs", None)
    if isinstance(inputs, list):
        for port_spec in inputs:
            name = getattr(port_spec, "name", None)
            if not isinstance(name, str) or name in payload:
                continue
            default = getattr(port_spec, "default", None)
            if default is not None:
                payload[name] = default
                continue
            required = getattr(port_spec, "required", True)
            if required:
                node_id = getattr(node_spec, "id", None)
                prefix = (
                    f"node {node_id!r} "
                    if isinstance(node_id, str) and node_id
                    else ""
                )
                raise required_port_error(
                    f"{prefix}required input port {name!r} "
                    "has no edge and no default"
                )

    return payload

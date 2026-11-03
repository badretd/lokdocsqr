"""Shared condition parser and payload helpers for flow nodes.

Internal helper module — **not** a registered node.  All internals are
underscore-prefixed; the names intended for use by sibling node
modules are listed in ``__all__``.

Phase 8f: inline-graph resolution is delegated via the ``build_inline``
callback of :func:`_resolve_child_graph`.  This lets ``flow.subflow``
keep its own ``_build_inline`` / ``parse_graph`` / ... names in its
module namespace so the 8e regression can patch them (D8f.1 does not
override D8e tests).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from lokdocsqr.core.conditions import (
    _compare as _core_compare,
)
from lokdocsqr.core.conditions import (
    _CondError,
)
from lokdocsqr.core.conditions import (
    _evaluate as _core_evaluate,
)
from lokdocsqr.core.conditions import (
    _lookup as _core_lookup,
)
from lokdocsqr.core.conditions import (
    _Parser as _CoreConditionParser,
)
from lokdocsqr.core.conditions import (
    _tokenize as _core_tokenize,
)
from lokdocsqr.core.errors import GraphValidationError, NodeExecutionError
from lokdocsqr.core.graph.builder import build_graph
from lokdocsqr.core.graph.parser import parse_graph
from lokdocsqr.core.graph.resolver import resolve_states
from lokdocsqr.core.graph.validator import validate_graph
from lokdocsqr.core.models import ResolvedGraph
from lokdocsqr.core.node_api.registry import registry


def _tokenize(expr: str) -> list[tuple[str, str]]:
    return _core_tokenize(expr)


class _Parser(_CoreConditionParser):
    """Compatibility subclass of the core condition parser."""


def _lookup(name: str, mapping: dict[str, Any]) -> Any:
    return _core_lookup(name, mapping)


def _compare(left: Any, op: str, right: Any) -> bool:
    return _core_compare(left, op, right)


def _evaluate(condition: Any, mapping: dict[str, Any]) -> bool:
    return _core_evaluate(condition, mapping)


def _base_mapping(ctx: Any, payload: Any) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    variables = getattr(ctx, "variables", None)
    if isinstance(variables, dict):
        mapping.update(variables)

    if isinstance(payload, dict):
        extra = payload.get("variables")
        if isinstance(extra, dict):
            mapping.update(extra)
        for key, value in payload.items():
            if key in (
                "items",
                "files",
                "file_refs",
                "input",
                "data",
                "condition",
                "variables",
            ):
                continue
            mapping[key] = value
    return mapping


def _extract_items(payload: Any) -> tuple[list[Any], bool]:
    if isinstance(payload, list):
        return payload, True
    if isinstance(payload, tuple):
        return list(payload), True
    if isinstance(payload, dict):
        for key in ("items", "files", "file_refs", "input", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value, False
    return [], False


# ── nested-graph resolution (Phase 8f) ────────────────────────────────
_DEFAULT_MAX_DEPTH = 4

BuildInline = Callable[[dict[str, Any]], ResolvedGraph]


def _resolve_max_depth(ctx: Any, payload: dict[str, Any]) -> Any:
    """Pick max_depth source: payload when present, else config, else 4.

    Validation is a separate step so that an invalid payload value
    surfaces with the original ``subflow: max_depth must be a positive
    integer`` message (regression 8e).
    """
    if "max_depth" in payload:
        return payload.get("max_depth")
    config = getattr(ctx, "config", None)
    flow = getattr(config, "flow", None)
    return getattr(flow, "max_subflow_depth", _DEFAULT_MAX_DEPTH)


def _current_depth(ctx: Any) -> int:
    value = getattr(ctx, "flow_depth", 0)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def _subflow_stack(ctx: Any) -> tuple[str, ...]:
    value = getattr(ctx, "subflow_stack", ())
    if isinstance(value, tuple):
        return value
    try:
        return tuple(value)
    except TypeError:
        return ()


def _build_inline_default(graph: dict[str, Any]) -> ResolvedGraph:
    """Default inline builder (used by flow.loop_batch).

    ``flow.subflow`` deliberately does NOT use this — it defines its own
    module-level ``_build_inline`` so the 8e regression can patch it.
    """
    document = parse_graph(graph)
    errors = validate_graph(document)
    if errors:
        raise GraphValidationError("; ".join(errors))
    states = resolve_states(document)
    return build_graph(document, states, registry)


def _resolve_child_graph(
    ctx: Any,
    payload: dict[str, Any],
    *,
    build_inline: BuildInline | None = None,
) -> tuple[str | None, ResolvedGraph]:
    """Resolve a child graph for nested nodes (subflow, loop_batch).

    Order of checks (fail-closed, R26):

      1. exactly-one of ``graph_id`` / ``graph``;
      2. resolve ``max_depth`` (payload value if present, else
         ``AppConfig.flow.max_subflow_depth``, else 4);
      3. validate ``max_depth`` as a positive int (booleans rejected);
      4. filesystem-path guard for ``graph_id``;
      5. depth guard against ``ctx.flow_depth`` (max inclusive, D8e.6);
      6. ``graph_id`` branch: cycle guard against ``ctx.subflow_stack``,
         then ``ctx.automations.get(graph_id)`` (already ``ResolvedGraph``);
      7. inline branch: delegate to ``build_inline`` (default:
         :func:`_build_inline_default`);
      8. return ``(graph_id, resolved_graph)`` — ``graph_id`` is ``None``
         for inline graphs.

    ``build_inline`` lets the caller keep parse/validate/resolve/build
    in its own module namespace; ``flow.subflow`` uses this so that
    monkeypatching ``subflow.parse_graph`` etc. is honoured (8e contract).
    """
    if not isinstance(payload, dict):
        payload = {}

    graph_id_raw = payload.get("graph_id")
    graph_id = (
        graph_id_raw
        if isinstance(graph_id_raw, str) and graph_id_raw
        else None
    )
    graph_raw = payload.get("graph")
    graph = (
        graph_raw
        if isinstance(graph_raw, dict) and graph_raw
        else None
    )

    if (graph_id is None) == (graph is None):
        raise NodeExecutionError(
            "subflow: exactly one of 'graph_id' or 'graph' is required"
        )

    max_depth = _resolve_max_depth(ctx, payload)
    if (
        not isinstance(max_depth, int)
        or isinstance(max_depth, bool)
        or max_depth <= 0
    ):
        raise NodeExecutionError(
            f"subflow: max_depth must be a positive integer, got {max_depth!r}"
        )

    current_depth = _current_depth(ctx)
    if current_depth >= max_depth:
        raise NodeExecutionError(
            f"subflow: max depth exceeded: current={current_depth}, "
            f"limit={max_depth}"
        )

    if graph_id is not None:
        if (
            "/" in graph_id
            or "\\" in graph_id
            or graph_id.endswith(".json")
        ):
            raise NodeExecutionError(
                "subflow: filesystem paths are not supported; "
                "use AutomationCatalog graph_id"
            )
        if graph_id in _subflow_stack(ctx):
            raise NodeExecutionError(
                f"subflow: cycle detected: {graph_id}"
            )
        catalog = getattr(ctx, "automations", None)
        if catalog is None:
            raise NodeExecutionError(
                "subflow: AutomationCatalog not configured in ctx.automations"
            )
        built = catalog.get(graph_id)
        if built is None:
            raise NodeExecutionError(
                f"subflow: graph_id not found in catalog: {graph_id}"
            )
        return graph_id, built

    if not isinstance(graph, dict):
        raise NodeExecutionError(
            "subflow: exactly one of graph_id or graph is required"
        )
    builder = build_inline if build_inline is not None else _build_inline_default
    resolved = builder(graph)
    return None, resolved


__all__ = [
    "BuildInline",
    "_CondError",
    "_base_mapping",
    "_build_inline_default",
    "_evaluate",
    "_extract_items",
    "_resolve_child_graph",
]

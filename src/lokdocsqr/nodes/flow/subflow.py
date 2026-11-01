"""flow.subflow — invoke a nested automation or an inline graph.

Payload contract — exactly one of::

    {"graph_id": "<id>", "max_depth": int?}
    {"graph": {<RawGraph dict>}, "max_depth": int?}

``graph_id`` is looked up in ``ctx.automations`` (AutomationCatalog).  The
catalog stores ``ResolvedGraph`` objects, so the catalog path is already
built and does not re-run parser/validator/resolver/builder.  An inline
``graph`` is a raw Workflow v1.0 dict and is built through
``parse_graph -> validate_graph -> resolve_states -> build_graph``.

Validation order in ``_execute``:

  1. exactly-one-of graph_id/graph;
  2. resolve max_depth (payload value when present, else
     ``ctx.config.flow.max_subflow_depth``, else 4);
  3. validate max_depth as a positive int (booleans rejected); fail closed;
  4. depth guard against ``ctx.flow_depth``;
  5. graph_id: filesystem-path rejection, catalog presence, catalog lookup,
     cycle guard against ``ctx.subflow_stack``;
  6. child context via ``dataclasses.replace``;
  7. child executor ``Executor(child_ctx).execute(built)``.

Inline ``graph`` does not participate in the cycle stack — only the depth
guard applies.  Parser/validator/resolver/builder errors and child executor
exceptions propagate as-is; subflow is transparent to child failure.

``_preview`` is overridden: nested graph execution is not supported by the
action plan builder and must fail closed.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from lokdocsqr.core.errors import GraphValidationError, NodeExecutionError
from lokdocsqr.core.executor.engine import Executor
from lokdocsqr.core.graph.builder import build_graph
from lokdocsqr.core.graph.parser import parse_graph
from lokdocsqr.core.graph.resolver import resolve_states
from lokdocsqr.core.graph.validator import validate_graph
from lokdocsqr.core.models import ResolvedGraph
from lokdocsqr.core.node_api.decorators import register_node
from lokdocsqr.core.node_api.registry import registry

from ._base import _FlowBase

__all__ = ["SubflowNode"]

_DEFAULT_MAX_DEPTH = 4


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


def _resolve_max_depth(ctx: Any, payload: dict[str, Any]) -> Any:
    if "max_depth" in payload:
        return payload.get("max_depth")

    config = getattr(ctx, "config", None)
    flow = getattr(config, "flow", None)
    return getattr(flow, "max_subflow_depth", _DEFAULT_MAX_DEPTH)


def _reject_fs_path(graph_id: str) -> None:
    if "/" in graph_id or "\\" in graph_id or graph_id.endswith(".json"):
        raise NodeExecutionError(
            "subflow: filesystem paths are not supported; "
            "use AutomationCatalog graph_id"
        )


def _build_inline(graph: dict[str, Any]) -> ResolvedGraph:
    document = parse_graph(graph)
    errors = validate_graph(document)
    if errors:
        raise GraphValidationError("; ".join(errors))
    states = resolve_states(document)
    return build_graph(document, states, registry)


@register_node("flow.subflow")
class SubflowNode(_FlowBase):
    """Subflow node: execute a nested automation or inline graph.

    ``max_depth`` semantics (D8e.6):
        ``max_depth`` is the maximum allowed *depth of the child graph*,
        inclusive.  ``ctx.flow_depth`` is the depth of the *current* node;
        the child receives ``flow_depth + 1``.  The node fails closed with
        ``NodeExecutionError`` when ``ctx.flow_depth >= max_depth``, so
        ``max_depth=10`` allows ``flow_depth`` up to 9 (child depth up to
        10) and rejects the 11th level.  Non-int or non-positive
        ``max_depth`` values are rejected with ``NodeExecutionError``
        (R26, fail-closed).  The value resolves from
        ``payload["max_depth"]`` when it is a positive ``int``, otherwise
        from ``AppConfig.flow.max_subflow_depth`` (fallback 4).
    """

    id = "flow.subflow"
    retry_safe = False

    @property
    def title(self) -> str:
        return "Subflow"

    @property
    def inputs(self) -> dict[str, str]:
        return {"graph_id": "string", "graph": "dict", "max_depth": "int"}

    @property
    def outputs(self) -> dict[str, str]:
        return {
            "result": "any",
            "graph_id": "string",
            "depth": "int",
            "errors": "any[]",
            "error_count": "int",
        }

    def run(self, ctx: Any, payload: Any) -> Any:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        return self._dry_run_template(ctx, payload)

    def _preview(self, ctx: Any, payload: Any) -> Any:
        raise NodeExecutionError(
            "subflow: nested graph is not supported by the action plan builder"
        )

    def _execute(self, ctx: Any, payload: Any) -> Any:
        payload = payload if isinstance(payload, dict) else {}

        graph_id = (
            payload.get("graph_id")
            if isinstance(payload.get("graph_id"), str)
            and payload.get("graph_id")
            else None
        )
        graph = (
            payload.get("graph")
            if isinstance(payload.get("graph"), dict) and payload.get("graph")
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
            _reject_fs_path(graph_id)
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
            if graph_id in _subflow_stack(ctx):
                raise NodeExecutionError(
                    f"subflow: cycle detected: {graph_id}"
                )
        else:
            built = _build_inline(graph)

        child_ctx = replace(
            ctx,
            flow_depth=current_depth + 1,
            subflow_stack=_subflow_stack(ctx)
            + ((graph_id,) if graph_id is not None else ()),
        )
        result = Executor(child_ctx).execute(built)

        return {
            "result": result,
            "graph_id": graph_id,
            "depth": current_depth + 1,
            "errors": [],
            "error_count": 0,
        }

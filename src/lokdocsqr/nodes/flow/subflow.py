"""flow.subflow — invoke a nested automation or an inline graph.

Payload contract — exactly one of::

    {"graph_id": "<id>", "max_depth": int?}
    {"graph": {<RawGraph dict>}, "max_depth": int?}

``graph_id`` is looked up in ``ctx.automations`` (AutomationCatalog).  The
catalog stores ``ResolvedGraph`` objects, so the catalog path is already
built and does not re-run parser/validator/resolver/builder.  An inline
``graph`` is a raw Workflow v1.0 dict and is built through
``parse_graph -> validate_graph -> resolve_states -> build_graph``.

Shared guards (exactly-one, ``max_depth``, FS-path guard, depth guard,
cycle guard, catalog lookup) live in
:func:`lokdocsqr.nodes.flow._common._resolve_child_graph` (Phase 8f,
D8f.1).  The inline build itself is a *callback*: this module supplies
its own module-level ``_build_inline`` (plus the four graph-building
imports) so the 8e regression can patch them via ``monkeypatch.setattr``
on this module.  Do **not** move these names to ``_common`` — the 8e
tests depend on them living here.

``_execute`` returns a ``dict`` — **not** ``RunResult`` — so downstream
nodes and the 8e regression see the same envelope:

    {"result": RunResult, "graph_id": str|None, "depth": int,
     "errors": [], "error_count": 0}

``_preview`` is overridden: nested graph execution is not supported by the
action plan builder and must fail closed.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from lokdocsqr.core.errors import GraphValidationError, LokError, NodeExecutionError
from lokdocsqr.core.executor.engine import Executor
from lokdocsqr.core.graph.builder import build_graph
from lokdocsqr.core.graph.parser import parse_graph
from lokdocsqr.core.graph.resolver import resolve_states
from lokdocsqr.core.graph.validator import validate_graph
from lokdocsqr.core.models import ResolvedGraph
from lokdocsqr.core.node_api.decorators import register_node
from lokdocsqr.core.node_api.registry import registry

from ._base import _FlowBase
from ._common import _resolve_child_graph

__all__ = ["SubflowNode", "_build_inline"]


def _build_inline(graph: dict) -> ResolvedGraph:
    """Build an inline raw graph.

    Kept at module level so the 8e regression can patch
    ``subflow._build_inline`` / ``subflow.parse_graph`` / ... and observe
    calls.  ``_resolve_child_graph`` is passed this callable as
    ``build_inline=`` from ``_execute``.
    """
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
        (R26, fail-closed).
    """

    id = "flow.subflow"
    retry_safe = False
    _preview_fail_closed = True

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
        if isinstance(payload, dict) and payload.get("recursive_preview") is True:
            callback = getattr(ctx, "planning_callback", None)
            if callback is None:
                raise NodeExecutionError(
                    "subflow: recursive_preview requires ctx.planning_callback"
                )
            payload = payload if isinstance(payload, dict) else {}
            graph_id, child_graph = _resolve_child_graph(
                ctx, payload, build_inline=_build_inline
            )
            flow_vars = {
                "flow.run_id": ctx.run_id,
                "flow.automation_id": graph_id,
                "flow.depth": ctx.flow_depth + 1,
                "flow.parent_run_id": ctx.run_id,
            }
            child_ctx = replace(
                ctx,
                variables={**ctx.variables, **flow_vars},
                flow_depth=ctx.flow_depth + 1,
                subflow_stack=ctx.subflow_stack
                + ((graph_id,) if graph_id is not None else ()),
            )
            child_plan = callback(child_ctx, child_graph, graph_id)
            child_errors = tuple(getattr(child_plan, "errors", ()) or ())
            return {
                "result": child_plan,
                "graph_id": graph_id,
                "depth": ctx.flow_depth + 1,
                "errors": [
                    getattr(err, "error", str(err)) for err in child_errors
                ],
                "error_count": len(child_errors),
            }
        raise NodeExecutionError(
            "subflow: nested graph is not supported by the action plan builder"
        )

    def _execute(self, ctx: Any, payload: Any) -> Any:
        payload = payload if isinstance(payload, dict) else {}
        on_child_error = payload.get("on_child_error", "warn")
        if on_child_error not in ("continue", "fail", "warn"):
            raise NodeExecutionError(
                f"subflow: invalid on_child_error {on_child_error!r}, "
                f"expected 'continue', 'fail' or 'warn'"
            )

        # Pass our own ``_build_inline`` so 8e monkeypatching of this
        # module's parse/validate/resolve/build names is honoured.
        graph_id, child_graph = _resolve_child_graph(
            ctx, payload, build_inline=_build_inline
        )
        flow_vars = {
            "flow.run_id": ctx.run_id,
            "flow.automation_id": graph_id,
            "flow.depth": ctx.flow_depth + 1,
            "flow.parent_run_id": ctx.run_id,
        }
        child_ctx = replace(
            ctx,
            variables={**ctx.variables, **flow_vars},
            flow_depth=ctx.flow_depth + 1,
            subflow_stack=ctx.subflow_stack + (
                (graph_id,) if graph_id is not None else ()
            ),
        )

        errors: list[dict[str, Any]] = []
        warnings: list[str] = []
        try:
            result = Executor(child_ctx).execute(child_graph)
        except LokError as exc:
            if on_child_error == "fail":
                raise
            errors.append(
                {
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            )
            if on_child_error == "warn":
                warnings.append(self._warning(ctx, exc))
            result = None
        else:
            if not result.success:
                detail = "; ".join(result.errors) or "child run failed"
                errors.append(
                    {
                        "error": detail,
                        "error_type": "NodeExecutionError",
                    }
                )
                if on_child_error == "warn":
                    warnings.append(
                        self._warning(ctx, NodeExecutionError(detail))
                    )

        envelope: dict[str, Any] = {
            "result": result,
            "graph_id": graph_id,
            "depth": ctx.flow_depth + 1,
            "errors": errors,
            "error_count": len(errors),
        }
        if warnings:
            envelope["warnings"] = warnings
        if on_child_error == "warn" and errors:
            envelope["_force_failed"] = True
        return envelope

    def _warning(self, ctx: Any, exc: BaseException) -> str:
        node_id = getattr(ctx, "node_id", None) or self.id
        return (
            f"subflow node '{node_id}' failed: "
            f"{type(exc).__name__}: {exc}"
        )

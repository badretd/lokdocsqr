"""flow.loop_batch — execute a child graph once per item or per batch.

Payload::

    {"items": list|tuple|None, "mode": "loop"|"batch",
     "batch_size": int?, "graph_id"|"graph": ..., "max_depth": int?}

``_execute`` returns a dict with all declared outputs present in every
branch (empty-item no-op included).  Per-item child errors are captured
as ``{"index", "error", "error_type"}`` entries in ``errors``; results
stay aligned with ``units`` (``None`` for failed items).

D-deviations (Phase 8f):

  * D8f.2 — per-item fail-soft only for ``LokError``.  A non-``LokError``
    re-raises: it is a programming bug, not a child-node failure.
  * D8f.3 — child variables are merged: ``{**ctx.variables, item, index,
    batch}``.  Unlike ``flow.subflow`` (which does not merge), loop_batch
    must expose ``item``/``index``/``batch`` to downstream templates
    while keeping parent variables visible.
  * D8f.4 — in ``batch`` mode ``item`` is ``None`` (fail-loud downstream
    if a template references it); the current chunk is under ``batch``.
  * D8f.5 — ``items`` is validated strictly (not via ``_extract_items``):
    ``str``/``int``/``dict`` → ``NodeExecutionError``.
  * D8f.6 — child graph is resolved *always*, even when ``items == []``
    (fail-closed).
  * D8f.7 — ``batch_size`` is ignored (not validated) in ``loop`` mode;
    the output ``batch_size`` is 1.

``_preview`` is overridden: nested graph execution is not supported by
the action plan builder and must fail closed.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from lokdocsqr.core.errors import LokError, NodeExecutionError
from lokdocsqr.core.executor.engine import Executor
from lokdocsqr.core.node_api.decorators import register_node

from ._base import _FlowBase
from ._common import _resolve_child_graph

__all__ = ["LoopBatchNode"]


@register_node("flow.loop_batch")
class LoopBatchNode(_FlowBase):
    """Loop / batch executor for nested graphs."""

    id = "flow.loop_batch"
    retry_safe = False

    @property
    def title(self) -> str:
        return "Loop / Batch"

    @property
    def inputs(self) -> dict[str, str]:
        return {
            "items": "any[]",
            "mode": "string",
            "batch_size": "integer",
        }

    @property
    def outputs(self) -> dict[str, str]:
        return {
            "results": "any[]",
            "errors": "any[]",
            "error_count": "integer",
            "total": "integer",
            "mode": "string",
            "batch_size": "integer",
            "graph_id": "string",
            "depth": "integer",
        }

    def run(self, ctx: Any, payload: Any) -> Any:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        return self._dry_run_template(ctx, payload)

    def _preview(self, ctx: Any, payload: Any) -> Any:
        raise NodeExecutionError(
            "loop_batch: nested graph is not supported by the action plan builder"
        )

    def _execute(self, ctx: Any, payload: Any) -> Any:
        payload = payload if isinstance(payload, dict) else {}

        # Step 1 — items (strict; not _extract_items).
        items_raw = payload.get("items")
        if items_raw is None:
            items: list[Any] = []
        elif isinstance(items_raw, list):
            items = items_raw
        elif isinstance(items_raw, tuple):
            items = list(items_raw)
        else:
            raise NodeExecutionError(
                f"loop_batch: items must be a list or tuple, "
                f"got {type(items_raw).__name__}"
            )

        # Step 2 — mode (strict lowercase).
        mode = payload.get("mode", "loop")
        if mode not in ("loop", "batch"):
            raise NodeExecutionError(
                f"loop_batch: invalid mode {mode!r}, "
                f"expected 'loop' or 'batch'"
            )

        # Step 3 — batch_size (only validated for mode="batch").
        if mode == "batch":
            batch_size = payload.get("batch_size", 1)
            if (
                isinstance(batch_size, bool)
                or not isinstance(batch_size, int)
                or batch_size <= 0
            ):
                raise NodeExecutionError(
                    f"loop_batch: batch_size must be a positive int, "
                    f"got {batch_size!r}"
                )
        else:
            batch_size = 1

        # Step 4 — resolve child graph (fail-closed, even when items empty).
        graph_id, child_graph = _resolve_child_graph(ctx, payload)

        # Step 5 — empty items: no-op, but ports still covered.
        if not items:
            return {
                "results": [],
                "errors": [],
                "error_count": 0,
                "total": 0,
                "mode": mode,
                "batch_size": batch_size,
                "graph_id": graph_id,
                "depth": ctx.flow_depth + 1,
            }

        # Step 6 — build units.
        if mode == "loop":
            units = [(i, item) for i, item in enumerate(items)]
        else:  # batch
            units = [
                (chunk_index, items[offset : offset + batch_size])
                for chunk_index, offset in enumerate(range(0, len(items), batch_size))
            ]
        total = len(units)

        results: list[Any] = []
        errors: list[dict[str, Any]] = []
        flow_vars = {
            "flow.run_id": ctx.run_id,
            "flow.automation_id": graph_id,
            "flow.depth": ctx.flow_depth + 1,
            "flow.parent_run_id": ctx.run_id,
        }
        child_stack_extra: tuple[str, ...] = (
            (graph_id,) if graph_id is not None else ()
        )

        # Step 7 — iterate.
        for index, unit in units:
            if mode == "loop":
                item_var: Any = unit
                batch_var: Any = [unit]
            else:  # batch
                item_var = None  # D8f.4: fail-loud downstream
                batch_var = unit

            child_ctx = replace(
                ctx,
                variables={
                    **ctx.variables,  # D8f.3: parent variables stay visible
                    "item": item_var,
                    "index": index,
                    "batch": batch_var,
                    **flow_vars,
                },
                flow_depth=ctx.flow_depth + 1,
                subflow_stack=ctx.subflow_stack + child_stack_extra,
            )

            try:
                run_result = Executor(child_ctx).execute(child_graph)
            except LokError as exc:
                errors.append({
                    "index": index,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                })
                results.append(None)
            # D8f.2: non-LokError propagates — it is a programming bug.
            else:
                results.append(run_result)

        # Step 8 — return.
        return {
            "results": results,
            "errors": errors,
            "error_count": len(errors),
            "total": total,
            "mode": mode,
            "batch_size": batch_size,
            "graph_id": graph_id,
            "depth": ctx.flow_depth + 1,
        }

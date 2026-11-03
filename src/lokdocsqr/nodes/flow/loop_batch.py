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
    _preview_fail_closed = True

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
        if isinstance(payload, dict) and payload.get("recursive_preview") is True:
            callback = getattr(ctx, "planning_callback", None)
            if callback is None:
                raise NodeExecutionError(
                    "loop_batch: recursive_preview requires "
                    "ctx.planning_callback"
                )
            payload = payload if isinstance(payload, dict) else {}

            mode = payload.get("mode", "loop")
            if mode not in ("loop", "batch"):
                raise NodeExecutionError(
                    f"loop_batch: invalid mode {mode!r}, "
                    f"expected 'loop' or 'batch'"
                )
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

            on_child_error = payload.get("on_child_error", "warn")
            if on_child_error not in ("continue", "fail", "warn"):
                raise NodeExecutionError(
                    f"loop_batch: invalid on_child_error "
                    f"{on_child_error!r}, expected 'continue', 'fail' "
                    f"or 'warn'"
                )

            items_raw = payload.get("items")
            if items_raw is None:
                items: list[Any] = []
            elif isinstance(items_raw, (list, tuple)):
                items = list(items_raw)
            else:
                raise NodeExecutionError(
                    f"loop_batch: items must be a list or tuple, "
                    f"got {type(items_raw).__name__}"
                )

            # Resolve the child graph even for an empty item list, matching
            # the fail-closed behaviour of ``_execute`` (D8f.6).
            graph_id, child_graph = _resolve_child_graph(ctx, payload)
            units = self._units(items, mode, batch_size)
            flow_vars = {
                "flow.run_id": ctx.run_id,
                "flow.automation_id": graph_id,
                "flow.depth": ctx.flow_depth + 1,
                "flow.parent_run_id": ctx.run_id,
            }
            child_stack_extra: tuple[str, ...] = (
                (graph_id,) if graph_id is not None else ()
            )

            results: list[Any] = []
            errors: list[dict[str, Any]] = []
            warnings: list[str] = []
            # Preview every unit with the same per-item variables the real
            # execution injects. Without this, ``{item[path]}`` cannot render
            # and the plan is reported as a spurious error.
            for index, unit in units:
                if mode == "loop":
                    item_var: Any = unit
                    batch_var: Any = [unit]
                else:  # batch
                    item_var = None
                    batch_var = unit
                child_ctx = replace(
                    ctx,
                    variables={
                        **ctx.variables,
                        "item": item_var,
                        "index": index,
                        "batch": batch_var,
                        **flow_vars,
                    },
                    flow_depth=ctx.flow_depth + 1,
                    subflow_stack=ctx.subflow_stack + child_stack_extra,
                )
                try:
                    child_plan = callback(child_ctx, child_graph, graph_id)
                except LokError as exc:
                    if on_child_error == "fail":
                        raise
                    errors.append(
                        {
                            "index": index,
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                        }
                    )
                    if on_child_error == "warn":
                        warnings.append(self._warning(ctx, index, exc))
                    results.append(None)
                    continue
                child_errors = tuple(
                    getattr(child_plan, "errors", ()) or ()
                )
                if child_errors:
                    for error in child_errors:
                        errors.append(
                            {
                                "index": index,
                                "error": getattr(error, "error", str(error)),
                                "error_type": getattr(
                                    error, "exc_type", "PlanError"
                                ),
                            }
                        )
                    results.append(None)
                else:
                    results.append(child_plan)

            result: dict[str, Any] = {
                "results": results,
                "errors": errors,
                "error_count": len(errors),
                "total": len(units),
                "mode": mode,
                "batch_size": batch_size,
                "graph_id": graph_id,
                "depth": ctx.flow_depth + 1,
            }
            if warnings:
                result["warnings"] = warnings
            if on_child_error == "warn" and errors:
                result["_force_failed"] = True
            return result
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

        on_child_error = payload.get("on_child_error", "warn")
        if on_child_error not in ("continue", "fail", "warn"):
            raise NodeExecutionError(
                f"loop_batch: invalid on_child_error {on_child_error!r}, "
                f"expected 'continue', 'fail' or 'warn'"
            )

        # Step 4 — build units before resolving the child so a failed
        # resolution can still be reported per unit under ``warn`` /
        # ``continue``.
        units = self._units(items, mode, batch_size)
        total = len(units)

        # Step 5 — resolve child graph (fail-closed, even when items empty).
        try:
            graph_id, child_graph = _resolve_child_graph(ctx, payload)
        except LokError as exc:
            if on_child_error == "fail" or not units:
                raise
            child_errors = [
                {
                    "index": index,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
                for index, _unit in units
            ]
            child_warnings = (
                [self._warning(ctx, index, exc) for index, _unit in units]
                if on_child_error == "warn"
                else []
            )
            return self._result_dict(
                results=[None for _ in units],
                errors=child_errors,
                warnings=child_warnings,
                total=total,
                mode=mode,
                batch_size=batch_size,
                graph_id=None,
                depth=ctx.flow_depth + 1,
                force_failed=on_child_error == "warn",
            )

        # Step 6 — empty items: no-op, but ports still covered.
        if not items:
            return self._result_dict(
                results=[],
                errors=[],
                warnings=[],
                total=0,
                mode=mode,
                batch_size=batch_size,
                graph_id=graph_id,
                depth=ctx.flow_depth + 1,
            )

        results: list[Any] = []
        errors: list[dict[str, Any]] = []
        warnings: list[str] = []
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
                if on_child_error == "fail":
                    raise
                errors.append({
                    "index": index,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                })
                if on_child_error == "warn":
                    warnings.append(self._warning(ctx, index, exc))
                results.append(None)
            # D8f.2: non-LokError propagates — it is a programming bug.
            else:
                if not run_result.success:
                    detail = "; ".join(run_result.errors) or "child run failed"
                    errors.append({
                        "index": index,
                        "error": detail,
                        "error_type": "NodeExecutionError",
                    })
                    if on_child_error == "warn":
                        warnings.append(
                            self._warning(
                                ctx,
                                index,
                                NodeExecutionError(detail),
                            )
                        )
                    results.append(None)
                else:
                    results.append(run_result)

        # Step 8 — return.
        return self._result_dict(
            results=results,
            errors=errors,
            warnings=warnings,
            total=total,
            mode=mode,
            batch_size=batch_size,
            graph_id=graph_id,
            depth=ctx.flow_depth + 1,
            force_failed=on_child_error == "warn" and bool(errors),
        )

    @staticmethod
    def _units(items: list[Any], mode: str, batch_size: int) -> list[tuple[int, Any]]:
        """Split ``items`` into ``(index, unit)`` pairs for the run mode."""
        if mode == "loop":
            return [(index, item) for index, item in enumerate(items)]
        return [
            (chunk_index, items[offset : offset + batch_size])
            for chunk_index, offset in enumerate(
                range(0, len(items), batch_size)
            )
        ]

    def _warning(self, ctx: Any, index: int, exc: BaseException) -> str:
        node_id = getattr(ctx, "node_id", None) or self.id
        return (
            f"loop_batch node '{node_id}' item {index} failed: "
            f"{type(exc).__name__}: {exc}"
        )

    @staticmethod
    def _result_dict(
        *,
        results: list[Any],
        errors: list[dict[str, Any]],
        warnings: list[str],
        total: int,
        mode: str,
        batch_size: int,
        graph_id: str | None,
        depth: int,
        force_failed: bool = False,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "results": results,
            "errors": errors,
            "error_count": len(errors),
            "total": total,
            "mode": mode,
            "batch_size": batch_size,
            "graph_id": graph_id,
            "depth": depth,
        }
        if warnings:
            result["warnings"] = warnings
        if force_failed:
            result["_force_failed"] = True
        return result

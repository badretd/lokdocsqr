"""Shared scaffolding for flow nodes.

Internal helper module — **not** a registered node. Concrete flow nodes
subclass :class:`_FlowBase` and declare ``run``/``dry_run`` in their own
class body (``CONTRACT.md`` invariant 5).
"""
from __future__ import annotations

from typing import Any

from lokdocsqr.core.node_api.base import Node


class _FlowBase(Node):
    """Common scaffolding for flow nodes.

    Pipeline::

        run      -> _run_template      -> _execute
        dry_run  -> _dry_run_template  -> _preview (defaults to _execute)

    Control-flow nodes that do not read state need not override
    ``_preview``. Reading nodes that must not touch the filesystem in
    preview override ``_preview`` explicitly.
    """

    def run(self, ctx: Any, payload: Any) -> Any:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        return self._dry_run_template(ctx, payload)

    def _run_template(self, ctx: Any, payload: Any) -> Any:
        return self._execute(ctx, payload)

    def _dry_run_template(self, ctx: Any, payload: Any) -> Any:
        return self._preview(ctx, payload)

    def _preview(self, ctx: Any, payload: Any) -> Any:
        return self._execute(ctx, payload)

    def _execute(self, ctx: Any, payload: Any) -> Any:
        raise NotImplementedError


__all__ = ["_FlowBase"]

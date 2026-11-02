"""flow.retry — retry marker / router for on_error handling.

The node is a pure control-flow node: it does not read the filesystem and
does not sleep. ``_preview`` is intentionally not overridden; the default
``_FlowBase._preview == _execute`` is the intended contract.
"""
from __future__ import annotations

from typing import Any, ClassVar

from lokdocsqr.core.node_api.decorators import register_node

from ._base import _FlowBase


@register_node("flow.retry")
class FlowRetry(_FlowBase):
    id = "flow.retry"
    retry_safe: ClassVar[bool] = False
    _inject_on_error: ClassVar[bool] = True

    @property
    def title(self) -> str:
        return "Retry"

    @property
    def inputs(self) -> dict[str, str]:
        return {"retry_policy": "object"}

    @property
    def outputs(self) -> dict[str, str]:
        return {
            "retry": "boolean",
            "pass": "boolean",
            "retry_policy": "object",
            "on_error": "boolean",
        }

    def run(self, ctx: Any, payload: Any) -> Any:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        return self._dry_run_template(ctx, payload)

    def _execute(self, ctx: Any, payload: Any) -> Any:
        on_error = bool(payload.get("on_error", False))
        retry_policy = payload.get("retry_policy")
        return {
            "retry": True if on_error else None,
            "pass": True if not on_error else None,
            "retry_policy": retry_policy,
            "on_error": on_error,
        }

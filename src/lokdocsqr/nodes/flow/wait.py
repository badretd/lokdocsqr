"""flow.wait — pause for a deterministic duration via ``ctx.clock``.

Payload: ``{"seconds": float}``. Missing/None uses ``default_seconds``.
Negative or non-numeric values raise ``NodeExecutionError`` (fail-closed).

Both ``run`` and ``dry_run`` call ``ctx.clock.sleep``; the only difference
is the echoed ``dry_run`` flag. This is D8d.1: dry-run really sleeps,
confirmed by the operator. ``_preview`` is not overridden because the
default ``_execute`` is the intended contract for both modes.
"""
from __future__ import annotations

from typing import Any, ClassVar

from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.core.node_api.decorators import register_node

from ._base import _FlowBase


@register_node("flow.wait")
class WaitNode(_FlowBase):
    id = "flow.wait"
    retry_safe = True
    default_seconds: ClassVar[float] = 1.0

    @property
    def title(self) -> str:
        return "Wait / Delay"

    @property
    def inputs(self) -> dict[str, str]:
        return {"seconds": "float"}

    @property
    def outputs(self) -> dict[str, str]:
        return {"slept": "float", "seconds": "float", "dry_run": "bool"}

    def run(self, ctx: Any, payload: Any) -> Any:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        return self._dry_run_template(ctx, payload)

    def _execute(self, ctx: Any, payload: Any) -> Any:
        raw = payload.get("seconds") if isinstance(payload, dict) else None

        if raw is None:
            seconds = self.default_seconds
        else:
            try:
                seconds = float(raw)
            except (TypeError, ValueError):
                raise NodeExecutionError(
                    f"flow.wait: seconds must be a number, got {raw!r}"
                )

        if seconds < 0:
            raise NodeExecutionError(
                f"flow.wait: seconds must be >= 0, got {seconds}"
            )

        ctx.clock.sleep(seconds)

        return {
            "slept": seconds,
            "seconds": seconds,
            "dry_run": bool(getattr(ctx, "dry_run", False)),
        }

"""flow.wait — pause for a deterministic duration via ``ctx.clock``.

Payload: ``{"seconds": float}``. Missing/None uses ``default_seconds``.
Negative or non-numeric values raise ``NodeExecutionError`` (fail-closed).

``run`` calls ``ctx.clock.sleep``. ``dry_run`` is pure: it never sleeps
and instead reports the duration it would have waited as ``would_sleep``
(``slept`` is ``0.0``). Both modes accept and validate the same payload.
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

    def _resolve_seconds(self, payload: Any) -> float:
        raw = payload.get("seconds") if isinstance(payload, dict) else None

        if raw is None:
            seconds = self.default_seconds
        else:
            try:
                seconds = float(raw)
            except (TypeError, ValueError) as exc:
                raise NodeExecutionError(
                    f"flow.wait: seconds must be a number, got {raw!r}"
                ) from exc

        if seconds < 0:
            raise NodeExecutionError(
                f"flow.wait: seconds must be >= 0, got {seconds}"
            )

        return seconds

    def _execute(self, ctx: Any, payload: Any) -> Any:
        seconds = self._resolve_seconds(payload)
        ctx.clock.sleep(seconds)

        return {
            "slept": seconds,
            "seconds": seconds,
            "dry_run": bool(getattr(ctx, "dry_run", False)),
        }

    def _preview(self, ctx: Any, payload: Any) -> Any:
        # Dry-run is side-effect free: validate the payload but never sleep.
        seconds = self._resolve_seconds(payload)

        return {
            "slept": 0.0,
            "seconds": seconds,
            "would_sleep": seconds,
            "dry_run": True,
        }

"""flow.if_branch — deterministic branch selection from a safe condition.

The condition is interpreted by a small whitelist parser. The node
never reads the filesystem. ``_preview`` is intentionally not
overridden: for control flow the preview decision must be exactly the
same as the real decision.

Output contract (all three keys are ALWAYS present):

  * Non-sequence input (dict / scalar):
      ``{"result": bool | "error",
           "branch": "true" | "false" | "error",
           "items": []}``
  * Sequence input:
      ``{"result": [bool | "error", ...],
           "branch": ["true" | "false" | "error", ...],
           "items": [bool | "error", ...]}``  # per-input result list

Per-item parse/evaluation errors are reported as ``"error"`` values,
not raised; the node never raises for a bad condition.
"""
from __future__ import annotations

from typing import Any

from lokdocsqr.core.node_api.decorators import register_node

from ._base import _FlowBase
from ._common import _CondError, _base_mapping, _evaluate, _extract_items


@register_node("flow.if_branch")
class IfBranchNode(_FlowBase):
    id = "flow.if_branch"
    retry_safe = True

    @property
    def title(self) -> str:
        return "If / Branch"

    @property
    def inputs(self) -> dict[str, str]:
        return {"condition": "string", "items": "any[]"}

    @property
    def outputs(self) -> dict[str, str]:
        # D-deviation (documented): ports are typed ``any`` because
        # ``result`` carries either ``bool`` or the ``"error"``
        # sentinel per-item; ``branch`` is the string twin of the
        # same decision.  ``items`` always exists (empty for scalar
        # input, per-input list for sequence input).
        return {"result": "any", "branch": "any", "items": "any[]"}

    def run(self, ctx: Any, payload: Any) -> Any:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        return self._dry_run_template(ctx, payload)

    def _evaluate_one(
        self, condition: Any, item: Any, base: dict[str, Any]
    ) -> Any:
        mapping = dict(base)
        if isinstance(item, dict):
            mapping.update(item)
        else:
            mapping["item"] = item
        try:
            return bool(_evaluate(condition, mapping))
        except _CondError:
            return "error"

    @staticmethod
    def _branch(value: Any) -> str:
        if value == "error":
            return "error"
        return "true" if value else "false"

    def _execute(self, ctx: Any, payload: Any) -> Any:
        condition = (
            payload.get("condition")
            if isinstance(payload, dict)
            else None
        )
        items, is_sequence = _extract_items(payload)
        base = _base_mapping(ctx, payload)

        if is_sequence:
            results = [
                self._evaluate_one(condition, item, base)
                for item in items
            ]
            branches = [self._branch(r) for r in results]
            return {
                "result": results,
                "branch": branches,
                "items": list(results),
            }

        # M1: drop ``condition`` from the per-item mapping so it cannot
        # shadow a caller-supplied variable of the same name.
        if isinstance(payload, dict):
            item = {
                k: v
                for k, v in payload.items()
                if k != "condition"
            }
        else:
            item = {}
        value = self._evaluate_one(condition, item, base)
        return {
            "result": value,
            "branch": self._branch(value),
            "items": [],
        }

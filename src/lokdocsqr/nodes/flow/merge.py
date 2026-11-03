"""flow.merge — merge multiple incoming values into one output.

Input port ``items`` accepts either a sequence of incoming values or a
single value. This matches CONTRACT invariant 10: several edges into one
``target_port`` arrive as a list; a single edge may deliver a bare value
or a list. The node treats the payload value as the list of incoming
values, so one-edge list and multi-edge list are intentionally confluent
(D8d.5).

Modes:
  * ``last_wins`` (default) — last non-None value; ``None`` values are
    ignored (D8d.2).
  * ``concat`` — flatten one level: list values are extended, scalars are
    appended (D8d.3).
  * ``dict_merge`` — shallow later-wins merge of dict values; non-dict
    values produce per-item ``errors`` entries (D8d.4).

Unknown ``mode`` is a per-item error, not an exception. All declared
outputs are always present. ``_preview`` is not overridden: merge is pure
and control-flow only, so ``_FlowBase._preview == _execute`` is correct.
"""
from __future__ import annotations

from typing import Any

from lokdocsqr.core.node_api.decorators import register_node

from ._base import _FlowBase

_ALLOWED_MODES = frozenset({"last_wins", "concat", "dict_merge"})


@register_node("flow.merge")
class MergeNode(_FlowBase):
    id = "flow.merge"
    retry_safe = True

    @property
    def title(self) -> str:
        return "Merge / Join"

    @property
    def inputs(self) -> dict[str, str]:
        return {"items": "any[]", "mode": "string"}

    @property
    def outputs(self) -> dict[str, str]:
        return {
            "merged": "any",
            "count": "int",
            "items": "any[]",
            "mode": "string",
            "errors": "any[]",
            "error_count": "int",
        }

    def run(self, ctx: Any, payload: Any) -> Any:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        return self._dry_run_template(ctx, payload)

    def _execute(self, ctx: Any, payload: Any) -> Any:
        raw = payload.get("items") if isinstance(payload, dict) else None
        if isinstance(raw, (list, tuple)):
            items: list[Any] = list(raw)
        elif raw is None:
            items = []
        else:
            items = [raw]

        mode_raw = payload.get("mode") if isinstance(payload, dict) else None
        mode = (
            mode_raw
            if isinstance(mode_raw, str) and mode_raw
            else "last_wins"
        )

        if mode not in _ALLOWED_MODES:
            return {
                "merged": None,
                "count": len(items),
                "items": items,
                "mode": str(mode_raw),
                "errors": [
                    {
                        "path": "$mode",
                        "message": f"unknown mode: {mode_raw!r}",
                    }
                ],
                "error_count": 1,
            }

        merged: Any
        errors: list[dict[str, str]] = []

        if mode == "last_wins":
            non_none = [value for value in items if value is not None]
            merged = non_none[-1] if non_none else None
        elif mode == "concat":
            merged = []
            for value in items:
                if isinstance(value, list):
                    merged.extend(value)
                else:
                    merged.append(value)
        else:  # dict_merge
            merged = {}
            for index, value in enumerate(items):
                if isinstance(value, dict):
                    merged.update(value)
                else:
                    errors.append(
                        {
                            "path": f"$items[{index}]",
                            "message": "dict_merge requires dict items",
                        }
                    )

        return {
            "merged": merged,
            "count": len(items),
            "items": items,
            "mode": mode,
            "errors": errors,
            "error_count": len(errors),
        }

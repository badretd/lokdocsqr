"""utils.set_variables — set run variables through ctx.set_variable().

This node is pure with respect to the filesystem. It only mutates the
per-run ``ExecutionContext.variables`` mapping via the public
``ctx.set_variable`` helper; direct ``ctx.variables[...] = ...`` is not
used (SPEC_PHASE_8 §1).

Payload contract::

    {"variables": {"name": value, ...}}

Output contract (all keys always present, both run and dry-run)::

    {"variables": {name: value, ...}, "count": int, "errors": [...]}

``variables`` contains only successfully set variables; reserved names are
reported per item and never raise.
"""
from __future__ import annotations

from typing import Any

from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.core.executor.context import RESERVED_VARIABLES
from lokdocsqr.core.node_api.decorators import register_node
from lokdocsqr.nodes.flow._base import _FlowBase

__all__ = ["SetVariablesNode"]


@register_node("utils.set_variables")
class SetVariablesNode(_FlowBase):
    id = "utils.set_variables"
    retry_safe = True

    @property
    def title(self) -> str:
        return "Set Variables"

    @property
    def inputs(self) -> dict[str, str]:
        return {"variables": "dict"}

    @property
    def outputs(self) -> dict[str, str]:
        return {"variables": "dict", "count": "int", "errors": "any[]"}

    def run(self, ctx: Any, payload: Any) -> Any:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        return self._dry_run_template(ctx, payload)

    @staticmethod
    def _extract(
        payload: Any,
    ) -> tuple[dict[Any, Any] | None, list[dict[str, Any]]]:
        if not isinstance(payload, dict):
            return None, [{"name": "<payload>", "error": "variables must be a dict"}]
        variables = payload.get("variables")
        if not isinstance(variables, dict):
            return None, [{"name": "<payload>", "error": "variables must be a dict"}]
        return variables, []

    def _execute(self, ctx: Any, payload: Any) -> Any:
        variables, errors = self._extract(payload)
        if variables is None:
            return {"variables": {}, "count": 0, "errors": errors}

        applied: dict[str, Any] = {}
        for name, value in variables.items():
            if not isinstance(name, str):
                errors.append(
                    {"name": str(name), "error": "variable name must be a string"}
                )
                continue
            try:
                ctx.set_variable(name, value)
            except NodeExecutionError as exc:
                errors.append({"name": name, "error": str(exc)})
                continue
            applied[name] = value

        return {"variables": applied, "count": len(applied), "errors": errors}

    def _preview(self, ctx: Any, payload: Any) -> Any:
        variables, errors = self._extract(payload)
        if variables is None:
            return {
                "variables": {},
                "count": 0,
                "errors": errors,
                "preview": "будет установлено 0 переменных",
                "dry_run": True,
            }

        would_apply: dict[str, Any] = {}
        for name, value in variables.items():
            if not isinstance(name, str):
                errors.append(
                    {"name": str(name), "error": "variable name must be a string"}
                )
                continue
            if name in RESERVED_VARIABLES:
                errors.append(
                    {
                        "name": name,
                        "error": f"cannot set reserved variable {name!r}",
                    }
                )
                continue
            would_apply[name] = value

        return {
            "variables": would_apply,
            "count": len(would_apply),
            "errors": errors,
            "preview": f"будет установлено {len(would_apply)} переменных",
            "dry_run": True,
        }

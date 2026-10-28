"""utils.path_template — safe path assembly via core.paths.render_template.

This node renders a template string (e.g. ``"{year}/{category}/{name}"``)
against the current run's ``ctx.variables`` plus an optional local
``payload["variables"]`` overlay. It is pure: no filesystem access, no
``scope.check`` (that is the downstream fs_ops node's job), no mutation of
``ctx.variables``.

Payload contract::

    {"template": "{year}/{name}", "variables": {"year": "2024"}}

Rendering goes through :func:`lokdocsqr.core.paths.render_template`, which
already rejects absolute results and ``..`` escapes (raising
:class:`lokdocsqr.core.errors.ScopeViolation`). Missing keys are left as
literal ``{key}`` by :class:`~lokdocsqr.core.paths.SafeDict`; this node
reports that as a per-item error because a downstream fs_ops node cannot
meaningfully act on a path that still contains an unresolved placeholder.

Output contract (all keys ALWAYS present, both run and dry-run)::

    {
        "path_template": str | None,  # rendered path (canonical downstream)
        "path": str | None,           # same value (alias, fs_ops symmetry)
        "template": str | None,       # echo of the input template
        "variables": dict,            # merged mapping (read-only echo)
        "errors": [{"path": ..., "message": ...}, ...],
        "error_count": int,
    }

On any rendering failure the path fields are ``None`` and ``errors`` is
non-empty; this node never raises for a bad payload (per-item philosophy,
same as 8a/8b).

``_preview`` is intentionally NOT overridden: rendering is deterministic
and touches no filesystem, so the default ``_FlowBase._preview == _execute``
is exactly the contract we want.
"""
from __future__ import annotations

from typing import Any

from lokdocsqr.core.errors import ScopeViolation
from lokdocsqr.core.node_api.decorators import register_node
from lokdocsqr.core.paths import render_template
from lokdocsqr.nodes.flow._base import _FlowBase

__all__ = ["PathTemplateNode"]


def _err(message: str) -> dict[str, str]:
    return {"path": "$template", "message": message}


def _error_result(
    *,
    template: str | None,
    mapping: dict[str, Any],
    message: str,
) -> dict[str, Any]:
    return {
        "path_template": None,
        "path": None,
        "template": template,
        "variables": mapping,
        "errors": [_err(message)],
        "error_count": 1,
    }


@register_node("utils.path_template")
class PathTemplateNode(_FlowBase):
    id = "utils.path_template"
    retry_safe = True

    @property
    def title(self) -> str:
        return "Path Template"

    @property
    def inputs(self) -> dict[str, str]:
        return {"template": "string", "variables": "dict"}

    @property
    def outputs(self) -> dict[str, str]:
        return {
            "path_template": "string",
            "path": "string",
            "template": "string",
            "variables": "dict",
            "errors": "any[]",
            "error_count": "int",
        }

    def run(self, ctx: Any, payload: Any) -> Any:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        return self._dry_run_template(ctx, payload)

    @staticmethod
    def _merge_variables(ctx: Any, payload: Any) -> dict[str, Any]:
        """Return ``ctx.variables`` merged with ``payload["variables"]``.

        Returns a fresh dict; neither ``ctx.variables`` nor the payload
        is mutated. Payload-local values win over run-scoped ones.
        """
        ctx_vars = getattr(ctx, "variables", None)
        base: dict[str, Any] = (
            dict(ctx_vars) if isinstance(ctx_vars, dict) else {}
        )
        if isinstance(payload, dict):
            extra = payload.get("variables")
            if isinstance(extra, dict):
                base.update(extra)
        return base

    def _execute(self, ctx: Any, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return _error_result(
                template=None,
                mapping={},
                message="payload must be a dict",
            )

        mapping = self._merge_variables(ctx, payload)
        template = payload.get("template")

        if not isinstance(template, str) or not template.strip():
            return _error_result(
                template=None,
                mapping=mapping,
                message="template must be a non-empty string",
            )

        try:
            rendered = render_template(template, mapping)
        except ScopeViolation as exc:
            # render_template raises for absolute results, ``..`` escapes,
            # and malformed format specs.
            return _error_result(
                template=template,
                mapping=mapping,
                message=str(exc),
            )

        if "{" in rendered:
            # SafeDict leaves unknown ``{key}`` as a literal. A path
            # containing ``{`` is not usable downstream.
            return _error_result(
                template=template,
                mapping=mapping,
                message=(
                    f"unresolved placeholder in rendered path: {rendered!r}"
                ),
            )

        return {
            "path_template": rendered,
            "path": rendered,
            "template": template,
            "variables": mapping,
            "errors": [],
            "error_count": 0,
        }

"""Write / Append File node (``fs.write_file``).

Architecture contract:
  * Invariant 5 — ``run`` and ``dry_run`` defined explicitly on this class.
  * Invariant 7 — ``scope.check`` runs before every ``safe_fs.*`` call
    (initial path via :meth:`FsNode._resolve`; parent re-checked before
    ``safe_fs.make_dir`` when ``create_parents`` is true).
  * Invariant 9 — mutations go exclusively through
    ``safe_fs.atomic_write`` / ``safe_fs.atomic_append`` /
    ``safe_fs.make_dir``. Direct ``open(..., "w"|"a"|"x")`` is
    forbidden by review (invariant 9's known AST blind spot).

``mode = "write"`` truncates (atomic replace); ``mode = "append"``
appends atomically. ``create_parents`` defaults to ``True`` and routes
through ``safe_fs.make_dir``.
"""
from __future__ import annotations

from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.core.models import Risk
from lokdocsqr.core.node_api.decorators import register_node

from ._base import FsNode
from ._common import build_action_log, check_scope


_VALID_MODES = ("write", "append")


@register_node
class WriteFileNode(FsNode):
    """Write or append content to a templated path."""

    id = "fs.write_file"
    title = "Write / Append File"
    description = "Write or append content to a templated path."
    inputs = {
        "path_template": {"type": "string", "required": True},
        "content": {"type": "string", "required": True},
        "mode": {
            "type": "string",
            "required": False,
            "default": "write",
            "enum": list(_VALID_MODES),
        },
        "create_parents": {"type": "boolean", "required": False, "default": True},
        "variables": {"type": "object", "required": False},
    }
    outputs = {
        "action_log": {"type": "ActionLog"},
        "path": {"type": "string"},
        "bytes_written": {"type": "integer"},
    }

    # --- run ---------------------------------------------------------

    def run(self, ctx, payload: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve(ctx, payload)
        data = self._coerce_content(payload["content"])
        mode = self._resolve_mode(payload)
        create_parents = bool(payload.get("create_parents", True))

        if create_parents:
            # Parent may equal the scope root; re-check is cheap and
            # satisfies invariant 7 before every safe_fs.* call.
            check_scope(ctx, str(path.parent))
            safe_fs.make_dir(path.parent)

        if mode == "append":
            safe_fs.atomic_append(path, data)
        else:
            safe_fs.atomic_write(path, data)

        log = build_action_log(
            run_id=ctx.run_id,
            automation_id=ctx.automation_id,
            node_id=self.id,
            action_type=f"write_file:{mode}",
            source=None,
            target=str(path),
            reason="Write / Append File node",
            confidence=1.0,
            risk=Risk.MEDIUM,
            dry_run=False,
            details={"mode": mode, "bytes": len(data)},
        )
        ctx.audit.append(log)
        return {
            "action_log": log,
            "path": str(path),
            "bytes_written": len(data),
        }

    # --- dry_run -----------------------------------------------------

    def dry_run(self, ctx, payload: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve(ctx, payload)
        data = self._coerce_content(payload["content"])
        mode = self._resolve_mode(payload)

        log = build_action_log(
            run_id=ctx.run_id,
            automation_id=ctx.automation_id,
            node_id=self.id,
            action_type=f"write_file:{mode}",
            source=None,
            target=str(path),
            reason="Write / Append File node (dry-run)",
            confidence=1.0,
            risk=Risk.MEDIUM,
            dry_run=True,
            details={
                "mode": mode,
                "bytes": len(data),
                "would_overwrite": path.exists(),
            },
        )
        return {
            "action_log": log,
            "path": str(path),
            "bytes_written": len(data),
        }

    # --- helpers -----------------------------------------------------

    @staticmethod
    def _coerce_content(content: Any) -> bytes:
        if isinstance(content, bytes):
            return content
        if isinstance(content, str):
            return content.encode("utf-8")
        raise NodeExecutionError(
            "Write File: 'content' must be str or bytes, got "
            f"{type(content).__name__}"
        )

    @staticmethod
    def _resolve_mode(payload: dict[str, Any]) -> str:
        mode = payload.get("mode", "write")
        if mode not in _VALID_MODES:
            raise NodeExecutionError(
                f"Write File: 'mode' must be one of {_VALID_MODES}, "
                f"got {mode!r}"
            )
        return mode
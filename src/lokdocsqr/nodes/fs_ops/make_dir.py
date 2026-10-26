"""Create Folder node (``fs.make_dir``) — templated path, idempotent.

Architecture contract:
  * Invariant 5 — ``run`` and ``dry_run`` are defined explicitly on this
    class (do not rely on ``Node`` inheritance).
  * Invariant 7 — ``scope.check`` runs before any ``safe_fs.*`` call
    (via :meth:`FsNode._resolve`).
  * Invariant 9 — the only filesystem-mutating call is
    ``safe_fs.make_dir``.
"""
from __future__ import annotations

from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.models import Risk
from lokdocsqr.core.node_api.decorators import register_node

from ._base import FsNode
from ._common import build_action_log


@register_node
class MakeDirNode(FsNode):
    """Create a folder at a templated path (no-op if it already exists)."""

    id = "fs.make_dir"
    title = "Create Folder"
    description = "Create a folder at a templated path (idempotent)."
    inputs = {
        "path_template": {"type": "string", "required": True},
        "variables": {"type": "object", "required": False},
    }
    outputs = {
        "action_log": {"type": "ActionLog"},
        "path": {"type": "string"},
    }

    # --- run ---------------------------------------------------------

    def run(self, ctx, payload: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve(ctx, payload)
        safe_fs.make_dir(path)

        log = build_action_log(
            run_id=ctx.run_id,
            automation_id=ctx.automation_id,
            node_id=self.id,
            action_type="make_dir",
            source=None,
            target=str(path),
            reason="Create Folder node",
            confidence=1.0,
            risk=Risk.LOW,
            dry_run=False,
        )
        ctx.audit.append(log)
        return {"action_log": log, "path": str(path)}

    # --- dry_run -----------------------------------------------------

    def dry_run(self, ctx, payload: dict[str, Any]) -> dict[str, Any]:
        # Path resolution and scope check still happen in dry-run so a
        # bad template is surfaced before any mutation would occur.
        path = self._resolve(ctx, payload)
        would_create = not path.exists()

        log = build_action_log(
            run_id=ctx.run_id,
            automation_id=ctx.automation_id,
            node_id=self.id,
            action_type="make_dir",
            source=None,
            target=str(path),
            reason="Create Folder node (dry-run)",
            confidence=1.0,
            risk=Risk.LOW,
            dry_run=True,
            details={"would_create": would_create},
        )
        return {"action_log": log, "path": str(path)}
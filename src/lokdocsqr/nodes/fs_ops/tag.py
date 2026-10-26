"""Tag / Set Metadata node (``fs.tag``).

Modes: ``set``, ``add``, ``remove``. Persists tags to a sidecar JSON
``<file>.tags.json`` via ``safe_fs.atomic_write``; optionally mirrors
them into extended attributes. Removing a tag is **not** deleting the
file.

Architecture contract:
  * Invariant 5 — ``run`` and ``dry_run`` declared explicitly on this class.
  * Invariant 7 — ``scope.check`` runs before every ``safe_fs.*`` call
    (initial path via :func:`_common.check_scope`; sidecar via
    :meth:`TagNode._write_sidecar` which re-checks before writing).
  * Invariant 9 — mutations go exclusively through ``safe_fs.atomic_write``.
    ``xattr.setxattr`` is the single documented exception, guarded by the
    degradation path described in the subphase-5e spec.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.core.models import Risk
from lokdocsqr.core.node_api.decorators import register_node

from ._base import FsNode
from ._common import build_action_log, check_scope


_MODES = ("set", "add", "remove")
_XATTR_KEY = "user.lokdocsqr.tags"
_SIDECAR_SUFFIX = ".tags.json"


def _try_xattr():
    """Return the ``xattr`` module, or ``None`` if it is unavailable."""
    try:
        import xattr  # noqa: F401
    except ImportError:
        return None
    return xattr


@register_node
class TagNode(FsNode):
    """Tag / Set Metadata — tags, xattr, sidecar JSON."""

    id = "fs.tag"
    title = "Tag / Set Metadata"
    description = (
        "Set, add, or remove tags on a file. Persists to a sidecar JSON "
        "and, where available, extended attributes."
    )
    inputs = {
        "path": {"type": "string", "required": True},
        "tags": {"type": "list[str]", "required": True},
        "mode": {
            "type": "string",
            "required": False,
            "default": "set",
            "enum": list(_MODES),
        },
    }
    outputs = {
        "action_log": {"type": "ActionLog"},
        "tags": {"type": "list[str]"},
    }

    # ── planning (shared by run + dry_run) ─────────────────────────
    def _plan(self, ctx, payload: dict[str, Any]):
        raw_path = payload.get("path")
        if not raw_path:
            raise NodeExecutionError("tag: 'path' is required")
        check_scope(ctx, str(raw_path))

        tags = payload.get("tags")
        if not isinstance(tags, (list, tuple)):
            raise NodeExecutionError(
                f"tag: 'tags' must be a list, got {type(tags).__name__}"
            )
        tags = [str(t) for t in tags]

        mode = str(payload.get("mode", "set"))
        if mode not in _MODES:
            raise NodeExecutionError(
                f"tag: 'mode' must be one of {_MODES}, got {mode!r}"
            )

        path = Path(raw_path)
        if not path.exists():
            raise NodeExecutionError(
                f"tag: target file does not exist: {path}"
            )

        existing = self._read_existing_tags(path)
        if mode == "set":
            final = list(dict.fromkeys(tags))
        elif mode == "add":
            final = list(dict.fromkeys(existing + tags))
        else:  # remove
            to_remove = set(tags)
            final = [t for t in existing if t not in to_remove]

        added = [t for t in final if t not in existing]
        removed = [t for t in existing if t not in final]
        return path, mode, final, added, removed

    @staticmethod
    def _sidecar_path(path: Path) -> Path:
        return path.with_name(path.name + _SIDECAR_SUFFIX)

    def _read_existing_tags(self, path: Path) -> list[str]:
        sidecar = self._sidecar_path(path)
        if not sidecar.exists():
            return []
        try:
            raw = safe_fs.read_bytes(sidecar)
            data = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError):
            return []
        tags = data.get("tags")
        if not isinstance(tags, list):
            return []
        return [str(t) for t in tags]

    @staticmethod
    def _build_sidecar_payload(tags: list[str]) -> bytes:
        data = {
            "tags": list(tags),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(data, indent=2, sort_keys=True).encode("utf-8")

    def _write_sidecar(self, ctx, path: Path, tags: list[str]) -> Path:
        sidecar = self._sidecar_path(path)
        check_scope(ctx, str(sidecar))
        safe_fs.atomic_write(sidecar, self._build_sidecar_payload(tags))
        return sidecar

    def _write_xattr(self, ctx, path: Path, tags: list[str]) -> None:
        xattr = _try_xattr()
        if xattr is None:
            try:
                ctx.logger.info(
                    "tag: xattr module unavailable; sidecar-only for %s",
                    path,
                )
            except AttributeError:
                pass
            return
        try:
            xattr.setxattr(
                str(path), _XATTR_KEY, json.dumps(tags).encode("utf-8")
            )
        except OSError as exc:
            try:
                ctx.logger.warning(
                    "tag: xattr write failed for %s (sidecar-only): %s",
                    path,
                    exc,
                )
            except AttributeError:
                pass

    def _build_log(
        self, ctx, path, mode, added, removed, final, dry_run: bool
    ):
        return build_action_log(
            run_id=ctx.run_id,
            automation_id=ctx.automation_id,
            node_id=self.id,
            action_type="tag",
            source=str(path),
            target=str(self._sidecar_path(path)),
            reason=f"tag:{mode}",
            confidence=1.0,
            risk=Risk.LOW,
            dry_run=dry_run,
            extra={
                "mode": mode,
                "added": added,
                "removed": removed,
                "tags": final,
            },
        )

    # ── Node ABC ─────────────────────────────────────────────────
    def run(self, ctx, payload: dict[str, Any]) -> dict[str, Any]:
        path, mode, final, added, removed = self._plan(ctx, payload)
        self._write_sidecar(ctx, path, final)
        self._write_xattr(ctx, path, final)
        log = self._build_log(
            ctx, path, mode, added, removed, final, dry_run=False
        )
        ctx.audit.append(log)
        return {"action_log": log, "tags": final}

    def dry_run(self, ctx, payload: dict[str, Any]) -> dict[str, Any]:
        path, mode, final, added, removed = self._plan(ctx, payload)
        # Sidecar and xattr are *not* written in dry-run.
        log = self._build_log(
            ctx, path, mode, added, removed, final, dry_run=True
        )
        return {"action_log": log, "tags": final}

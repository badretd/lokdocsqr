"""Copy File(s) — template-driven copy, free-space check, conflict policy.

Node id: ``fs.copy``. Invariant 9: no direct mutating FS calls here;
only ``safe_fs.copy_file`` mutates.

Order (fixed):
    scope.check(src) → render dst → scope.check(dst)
    → conflict resolution → free-space check → safe_fs.copy_file.

``shutil.disk_usage`` is a *read-only* probe (explicitly permitted by
the architecture contract) and is used to fail fast before any
mutation when the target volume cannot hold the copy.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import SafeFsError
from lokdocsqr.core.node_api.decorators import register_node

from ._base import FsTemplateNode


@register_node
class CopyNode(FsTemplateNode):
    """Copy File(s) — template path, conflict policy, free-space check."""

    id = "fs.copy"
    title = "Copy File(s)"
    inputs = ("file",)
    outputs = ("file",)
    action_type = "copy"

    def __init__(
        self,
        template: str = "{name}{ext}",
        conflict_policy: str = "skip",
        check_free_space: bool = True,
    ) -> None:
        self.template = template
        self.conflict_policy = conflict_policy
        self.check_free_space = check_free_space

    def _describe_payload(self) -> dict[str, Any]:
        return {
            "params": {
                "template": {
                    "type": "string",
                    "default": self.template,
                    "description": (
                        "Destination template with {name}, {ext}, "
                        "{date}, {author}, {tags}."
                    ),
                },
                "conflict_policy": {
                    "type": "enum",
                    "values": ["skip", "rename", "overwrite", "ask"],
                    "default": self.conflict_policy,
                },
                "check_free_space": {
                    "type": "bool",
                    "default": self.check_free_space,
                },
            },
        }

    # ── mutation hook ────────────────────────────────────────────────
    def _execute(self, src: Path, dst: Path) -> None:
        self._assert_free_space(src, dst)
        safe_fs.copy_file(src, dst)

    # ── free-space probe (read-only) ─────────────────────────────────
    def _assert_free_space(self, src: Path, target: Path) -> None:
        """Raise ``SafeFsError`` if the target volume cannot hold ``src``.

        ``SafeFsError`` propagates *before* any mutation, so no
        :class:`ActionLog` is produced on this branch (invariant:
        failure leaves no trace).
        """
        if not self.check_free_space:
            return
        try:
            src_size = src.stat().st_size
        except OSError:
            return  # let safe_fs surface the real cause
        try:
            free = shutil.disk_usage(str(target.parent)).free
        except OSError:
            return
        if free < src_size:
            raise SafeFsError("insufficient_space")

    def run(self, ctx, payload):
        return self._run_template(ctx, payload)

    # ── dry-run override: template adds probe before logging ─────────
    def dry_run(self, ctx, payload):
        src, final_dst, decision = self._plan(ctx, payload)
        if decision.action == "proceed":
            self._assert_free_space(src, final_dst)
        return self._build_log(
            ctx, src, final_dst, decision, dry_run=True
        )
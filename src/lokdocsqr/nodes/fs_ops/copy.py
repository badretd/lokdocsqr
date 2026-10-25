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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import SafeFsError
from lokdocsqr.core.node_api.base import Node
from lokdocsqr.core.node_api.decorators import register_node

from ._common import (
    ConflictDecision,
    build_action_log,
    check_scope,
    render_and_check,
    resolve_conflict_policy,
    risk_for_decision,
)


@register_node
class CopyNode(Node):
    """Copy File(s) — template path, conflict policy, free-space check."""

    id = "fs.copy"
    title = "Copy File(s)"
    inputs = ("file",)
    outputs = ("file",)

    def __init__(
        self,
        template: str = "{name}{ext}",
        conflict_policy: str = "skip",
        check_free_space: bool = True,
    ) -> None:
        self.template = template
        self.conflict_policy = conflict_policy
        self.check_free_space = check_free_space

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
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

    # ── internal ────────────────────────────────────────────────────
    def _variables(self, src: Path, extra: dict[str, Any]) -> dict[str, Any]:
        try:
            mtime = src.stat().st_mtime
            date = datetime.fromtimestamp(
                mtime, tz=timezone.utc
            ).strftime("%Y-%m-%d")
        except OSError:
            date = ""
        variables: dict[str, Any] = dict(extra)
        variables.setdefault("name", src.stem)
        variables.setdefault("ext", src.suffix)
        variables.setdefault("date", date)
        variables.setdefault("author", "")
        variables.setdefault("tags", "")
        return variables

    def _render_target(
        self, ctx, src: Path, variables: dict[str, Any]
    ) -> Path:
        rendered = render_and_check(ctx, self.template, variables)
        if not rendered.suffix and src.suffix:
            rendered = rendered.with_suffix(src.suffix)
        return rendered

    def _assert_free_space(self, src: Path, target: Path) -> None:
        """Read-only: raises SafeFsError if the target volume is too small.

        ``SafeFsError`` propagates *before* any mutation, so no
        ActionLog is produced on this branch (invariant: failure
        leaves no trace).
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

    def _plan(
        self, ctx, payload: dict[str, Any]
    ) -> tuple[Path, Path, ConflictDecision]:
        src = Path(payload["source"])
        check_scope(ctx, str(src))

        variables = self._variables(src, payload.get("variables") or {})
        dst = self._render_target(ctx, src, variables)
        check_scope(ctx, str(dst))

        decision = resolve_conflict_policy(self.conflict_policy, dst)
        if decision.action == "proceed":
            check_scope(ctx, str(decision.final_dst))
        return src, decision.final_dst, decision

    def _build_log(
        self, ctx, src: Path, final_dst: Path,
        decision: ConflictDecision, dry_run: bool,
    ):
        return build_action_log(
            run_id=ctx.run_id,
            automation_id=ctx.automation_id,
            node_id=self.id,
            action_type="copy",
            source=str(src),
            target=str(final_dst),
            reason=decision.reason or "no_conflict",
            confidence=1.0,
            risk=risk_for_decision(decision),
            dry_run=dry_run,
        )

    # ── Node ABC ────────────────────────────────────────────────────
    def run(self, ctx, payload):
        src, final_dst, decision = self._plan(ctx, payload)
        if decision.action == "skip":
            log = self._build_log(
                ctx, src, final_dst, decision, dry_run=False
            )
            ctx.audit.append(log)
            return log

        self._assert_free_space(src, final_dst)  # read-only
        safe_fs.copy_file(src, final_dst)

        log = self._build_log(
            ctx, src, final_dst, decision, dry_run=False
        )
        ctx.audit.append(log)
        return log

    def dry_run(self, ctx, payload):
        src, final_dst, decision = self._plan(ctx, payload)
        if decision.action == "proceed":
            self._assert_free_space(src, final_dst)  # read-only
        return self._build_log(
            ctx, src, final_dst, decision, dry_run=True
        )
